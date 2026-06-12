"""End-to-end product-video pipeline orchestrator.

Usage:
    python3 pipeline_product.py <run_id> [options]

Runs alongside pipeline.py — does not touch the original Object-Talk flow.
Each step is skipped if its output already exists, so re-runs after a failure are cheap.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import signal
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import GEMINI_API_KEY, GEMINI_TEXT_MODEL, OUTPUT_ROOT
from http_utils import post_with_retry
from steps import generate_clip_brief
from steps import generate_plan
from steps import generate_starter_image
from steps import generate_videos
from steps import merge_videos
from steps import scrape_website
from steps import upload_video
from steps.extract_last_frame import extract_last_frame


STEP_KEYS = ("scrape", "plan", "briefs", "merge", "upload")
APPROVAL_POLL_S = 2.0
DEFAULT_APPROVAL_TIMEOUT_S = 3600

GEMINI_GENERATE_CONTENT_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
)
CONTINUITY_PROMPT = (
    "You are a senior cinematographer doing a continuity report. Describe this frame in "
    "<= 90 words for a colleague rebuilding the look in the next shot. Cover: subject identity "
    "(preserve!), wardrobe/expression/pose, location, key/fill lighting, mood, dominant colors "
    "with hex if obvious, camera shot type + lens estimate, framing. No prose preamble. "
    "Description only."
)


def describe_frame(image_path: Path) -> str:
    """Send a frame to Gemini Vision and return a concise description (<= 100 words) of subject,
    lighting, palette, location, mood, camera framing. Used as prior_continuity_hint for the
    next clip's brief."""
    try:
        if not image_path.exists():
            return ""
        b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        body = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": CONTINUITY_PROMPT},
                    {"inlineData": {"mimeType": "image/png", "data": b64}},
                ],
            }],
        }
        url = GEMINI_GENERATE_CONTENT_URL.format(model=GEMINI_TEXT_MODEL, key=GEMINI_API_KEY)
        r = post_with_retry(url, json=body, timeout=120, label=f"continuity-{image_path.stem}")
        if r.status_code != 200:
            sys.stderr.write(f"  (warn) describe_frame {r.status_code}: {r.text[:300]}\n")
            return ""
        data = r.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = (candidates[0].get("content") or {}).get("parts") or []
        for p in parts:
            text = p.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
        return ""
    except Exception as e:
        sys.stderr.write(f"  (warn) describe_frame failed: {type(e).__name__}: {e}\n")
        return ""


def _load_brief(run_dir: Path) -> dict:
    brief_path = run_dir / "product" / "brief.json"
    if not brief_path.exists():
        raise FileNotFoundError(f"Missing product brief at {brief_path}")
    brief = json.loads(brief_path.read_text())
    if "product" not in brief and brief.get("product_name"):
        brief["product"] = brief["product_name"]
    if "company" not in brief and brief.get("company_name"):
        brief["company"] = brief["company_name"]
    return brief


def _load_plan(run_dir: Path) -> dict:
    plan_path = run_dir / "plan.json"
    if not plan_path.exists():
        raise FileNotFoundError(f"Missing plan at {plan_path}")
    return json.loads(plan_path.read_text())


def _load_scraped(run_dir: Path) -> dict | None:
    scraped_path = run_dir / "product" / "scraped" / "page.json"
    if not scraped_path.exists():
        return None
    try:
        return json.loads(scraped_path.read_text())
    except json.JSONDecodeError:
        return None


def _clip_video_path(run_dir: Path, clip_index: int) -> Path | None:
    matches = sorted(run_dir.glob(f"vid_{clip_index:02d}_*.mp4"))
    for m in matches:
        if m.stat().st_size > 1024:
            return m
    return None


def _delete_if_exists(path: Path | None) -> None:
    if path is None:
        return
    try:
        if path.exists():
            path.unlink()
    except Exception as e:
        print(f"  (warn) could not delete {path}: {e}", flush=True)


def _slug_for_brief(brief: dict) -> str:
    """Mirror generate_videos._slug for synthesized scripts."""
    name = (brief.get("product") or brief.get("company") or "clip").strip()
    return "-".join(name.lower().split())[:30] or "clip"


def _synthesize_scripts_for_clip(run_dir: Path, clip_index: int, clip_duration_s: int) -> Path:
    """Build a minimal scripts.json so generate_videos.generate_all(only=[N])
    can find an entry to drive Grok for this one clip. The dialogue/action
    come from the per-clip brief; the 'object' slug controls the output filename
    convention so vid_NN_*.mp4 appears in the run dir.
    """
    brief_path = run_dir / "briefs" / f"brief_{clip_index:02d}.json"
    if not brief_path.exists():
        raise FileNotFoundError(f"Missing clip brief: {brief_path}")
    clip_brief = json.loads(brief_path.read_text())

    product_brief = _load_brief(run_dir)
    object_slug = _slug_for_brief(product_brief)

    dialogue = ((clip_brief.get("dialogue") or {}).get("text") or "").strip()
    action = (clip_brief.get("action") or "").strip()
    image_prompt = (clip_brief.get("image_prompt") or "").strip()
    video_prompt = (clip_brief.get("video_prompt") or "").strip()

    entries = []
    for i in range(1, clip_index + 1):
        if i == clip_index:
            entries.append({
                "object": object_slug,
                "hindi_script": dialogue,
                "action_script": action or video_prompt,
                "image_prompt": image_prompt,
            })
        else:
            entries.append({
                "object": object_slug,
                "hindi_script": "",
                "action_script": "",
                "image_prompt": "",
            })

    payload = {
        "subject": product_brief.get("product") or product_brief.get("company") or "product",
        "clip_duration_s": clip_duration_s,
        "scripts": entries,
    }
    out_path = run_dir / "scripts.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return out_path


def _ensure_starter_image_as_clip_image(run_dir: Path, clip_index: int) -> None:
    """generate_videos looks for img_NN_*.png — point that at starter_NN.png so
    Grok uploads the chained starter as its image reference.

    Additionally mirror the first product reference photo (if any) to a sibling
    file img_NN_product.<ext>. generate_videos picks that up as an OPTIONAL
    second upload, which keeps Grok anchored to the real product (logo,
    LED/screen readouts, silhouette) instead of drifting to a plain stand-in.
    """
    starter = run_dir / f"starter_{clip_index:02d}.png"
    if not starter.exists():
        raise FileNotFoundError(f"Missing starter image: {starter}")
    target = run_dir / f"img_{clip_index:02d}_starter.png"
    if not target.exists():
        try:
            target.symlink_to(starter.name)
        except (OSError, NotImplementedError):
            target.write_bytes(starter.read_bytes())

    # Mirror the first product image, if one exists. Glob over the supported
    # extensions in priority order; only the first match is used.
    product_images_dir = run_dir / "product" / "images"
    if not product_images_dir.is_dir():
        return
    product_src: Path | None = None
    for ext in ("png", "jpg", "jpeg", "webp"):
        matches = sorted(product_images_dir.glob(f"img_01.{ext}"))
        if matches:
            product_src = matches[0]
            break
    if product_src is None:
        return
    product_dst = run_dir / f"img_{clip_index:02d}_product{product_src.suffix}"
    if product_dst.exists():
        return
    try:
        # Use a relative symlink target so the run dir stays portable.
        rel = os.path.relpath(product_src, run_dir)
        product_dst.symlink_to(rel)
    except (OSError, NotImplementedError):
        product_dst.write_bytes(product_src.read_bytes())


def _generate_clip_video(run_dir: Path, clip_index: int, clip_duration_s: int,
                          headless: bool) -> Path:
    """Drive Grok for a single clip. Reuses generate_videos.generate_all with
    only=[clip_index] and a synthesized scripts.json so the existing single-clip
    download/quota logic applies."""
    _ensure_starter_image_as_clip_image(run_dir, clip_index)
    scripts_path = _synthesize_scripts_for_clip(run_dir, clip_index, clip_duration_s)

    os.environ["CLIP_DURATION_S"] = str(clip_duration_s)
    outputs = generate_videos.generate_all(
        scripts_path, run_dir, headless=headless, only=[clip_index], parallel=False,
    )
    produced = _clip_video_path(run_dir, clip_index)
    if produced is None:
        raise RuntimeError(
            f"Grok did not produce vid_{clip_index:02d}_*.mp4 (returned {len(outputs)} paths)"
        )
    return produced


def wait_for_approval(run_dir: Path, clip_n: int,
                      timeout_s: int = DEFAULT_APPROVAL_TIMEOUT_S) -> str:
    """Block until approvals/clip_NN.approved or .rejected appears.

    On rejection, wipe the clip's outputs (starter, video, last frame, mirrored
    img reference) so the next iteration regenerates them. Returns 'approved'
    or 'rejected'; raises TimeoutError on timeout.
    """
    approvals_dir = run_dir / "approvals"
    approvals_dir.mkdir(parents=True, exist_ok=True)
    approved = approvals_dir / f"clip_{clip_n:02d}.approved"
    rejected = approvals_dir / f"clip_{clip_n:02d}.rejected"

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if approved.exists():
            return "approved"
        if rejected.exists():
            print(f">>> Rejected: {clip_n}", flush=True)
            _delete_if_exists(rejected)
            _delete_if_exists(run_dir / f"starter_{clip_n:02d}.png")
            _delete_if_exists(run_dir / f"last_frame_{clip_n:02d}.png")
            _delete_if_exists(run_dir / f"img_{clip_n:02d}_starter.png")
            for prod in run_dir.glob(f"img_{clip_n:02d}_product.*"):
                _delete_if_exists(prod)
            for vid in run_dir.glob(f"vid_{clip_n:02d}_*.mp4"):
                _delete_if_exists(vid)
            return "rejected"
        time.sleep(APPROVAL_POLL_S)
    raise TimeoutError(f"Approval for clip {clip_n} not received within {timeout_s}s")


def generate_metadata_for_product(brief: dict, plan: dict) -> dict:
    """Thin adapter into steps.upload_video.generate_metadata using the product
    brief + plan instead of an Object-Talk scripts payload."""
    clips = plan.get("clips") or []
    clip_duration_s = int((plan.get("global") or {}).get("clip_duration_s") or 10)

    object_label = (brief.get("product") or brief.get("company") or "product").strip()
    synthesized_scripts = []
    for c in clips:
        beat = (c.get("narrative_beat") or c.get("voiceover_hint")
                or c.get("purpose") or c.get("key_moment") or "")
        synthesized_scripts.append({
            "object": object_label,
            "hindi_script": str(beat),
        })

    scripts_payload = {
        "subject": object_label,
        "clip_duration_s": clip_duration_s,
        "scripts": synthesized_scripts,
    }
    subject = (
        f"{brief.get('company', '')} — {object_label}"
        if brief.get("company") else object_label
    )
    return upload_video.generate_metadata(subject.strip(" —"), scripts_payload)


def _step_scrape(run_dir: Path, brief: dict) -> None:
    scraped_dir = run_dir / "product" / "scraped"
    page_json = scraped_dir / "page.json"
    website_url = (brief.get("website_url") or "").strip()
    if not website_url:
        print("    (no website_url in brief — skipping scrape)", flush=True)
        return
    if page_json.exists():
        print("    scraped/page.json exists — skip", flush=True)
        return
    scrape_website.scrape(website_url, scraped_dir, max_images=5)


def _step_plan(run_dir: Path, brief: dict, scraped: dict | None,
                clip_count: int, clip_duration_s: int, total_duration_s: int) -> None:
    plan_path = run_dir / "plan.json"
    if plan_path.exists():
        print("    plan.json exists — skip", flush=True)
        return
    plan = generate_plan.generate(
        brief, scraped,
        clip_count=clip_count,
        clip_duration_s=clip_duration_s,
        total_duration_s=total_duration_s,
    )
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    print(f"    wrote {plan_path.name}", flush=True)
    print(">>> Plan ready", flush=True)


def _step_briefs(run_dir: Path, clip_count: int, parallel: bool) -> None:
    briefs_dir = run_dir / "briefs"
    briefs_dir.mkdir(parents=True, exist_ok=True)
    missing = [i for i in range(1, clip_count + 1)
               if not (briefs_dir / f"brief_{i:02d}.json").exists()]
    if not missing:
        print(f"    {clip_count} briefs exist — skip", flush=True)
        return
    generate_clip_brief.generate_all(run_dir, parallel=parallel)


def main() -> int:
    parser = argparse.ArgumentParser(description="Product-video pipeline orchestrator")
    parser.add_argument("run_id", help="Existing run directory name under pipeline/output/")
    parser.add_argument("--review-mode", choices=["auto", "per_clip"], default="auto")
    parser.add_argument("--clip-count", type=int, default=5)
    parser.add_argument("--clip-duration-s", type=int, default=10)
    parser.add_argument("--total-duration-s", type=int, default=50)
    parser.add_argument("--skip-upload", action="store_true")
    parser.add_argument("--privacy", default="public",
                        choices=["public", "unlisted", "private"])
    parser.add_argument("--parallel", action="store_true",
                        help="Pass through to brief generation")
    parser.add_argument("--headless", action="store_true",
                        help="Pass through to Grok video step")
    parser.add_argument("--from-step", default=None,
                        help="Resume marker: scrape, plan, briefs, clip1..clipN, merge, upload")
    args = parser.parse_args()

    run_dir = OUTPUT_ROOT / args.run_id
    if not run_dir.exists():
        sys.stderr.write(f"Run dir does not exist: {run_dir}\n")
        return 1

    # Mark this process as a product-mode run so steps.generate_videos swaps
    # its legacy Pixar GROK_STYLE_GUARD for the plan-derived PRODUCT_STYLE_GUARD
    # we set below (after plan.json exists).
    os.environ["PRODUCT_RUN_ID"] = args.run_id

    try:
        brief = _load_brief(run_dir)
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        return 1

    K = args.clip_count
    if K < 1:
        sys.stderr.write(f"--clip-count must be >= 1, got {K}\n")
        return 1

    print(f"\n=== Product run dir: {run_dir} ===", flush=True)
    print(f"  clips: {K} x {args.clip_duration_s}s = {args.total_duration_s}s, "
          f"review={args.review_mode}", flush=True)

    from_step = args.from_step
    clip_start = 1
    if from_step and from_step.startswith("clip"):
        try:
            clip_start = int(from_step[4:])
        except ValueError:
            sys.stderr.write(f"--from-step '{from_step}' is not a clipN form\n")
            return 1
        if not 1 <= clip_start <= K:
            sys.stderr.write(f"--from-step clip{clip_start} out of range 1..{K}\n")
            return 1

    # Resume gating: if --from-step points to a named step, force-re-run that
    # step (clear its output) and let downstream skip-checks handle the rest.
    # If --from-step is clipN, jump straight to the clip loop at N.
    force_step: str | None = None
    if from_step in STEP_KEYS:
        force_step = from_step

    try:
        print(">>> Step 1/7: scrape website", flush=True)
        if force_step == "scrape":
            page_json = run_dir / "product" / "scraped" / "page.json"
            _delete_if_exists(page_json)
        _step_scrape(run_dir, brief)

        scraped = _load_scraped(run_dir)

        print(">>> Step 2/7: generate plan", flush=True)
        if force_step == "plan":
            _delete_if_exists(run_dir / "plan.json")
        _step_plan(run_dir, brief, scraped, K, args.clip_duration_s, args.total_duration_s)

        plan = _load_plan(run_dir)

        style = plan.get("global", {})
        vs = (style.get("visual_style") or "").strip()
        vsn = (style.get("visual_style_notes") or "").strip()
        palette = ", ".join(style.get("palette") or [])
        guard_lines = []
        if vs:
            guard_lines.append(f"VISUAL STYLE (do not deviate): {vs}.")
        if vsn:
            guard_lines.append(vsn)
        if palette:
            guard_lines.append(f"Color palette anchor: {palette}.")
        if style.get("characters"):
            chars = "; ".join(
                f"{c.get('name','')}: {c.get('description','')}"
                for c in style["characters"][:3]
            )
            guard_lines.append(f"Character identity (preserve across clips): {chars}.")
        guard_lines.append(
            "Do not turn the product into a cartoon character unless visual_style "
            "explicitly says so. Do not change visual style across clips."
        )
        os.environ["PRODUCT_STYLE_GUARD"] = "\n".join(guard_lines)

        print(">>> Step 3/7: generate clip briefs", flush=True)
        if force_step == "briefs":
            for i in range(1, K + 1):
                _delete_if_exists(run_dir / "briefs" / f"brief_{i:02d}.json")
        _step_briefs(run_dir, K, args.parallel)

        for N in range(clip_start, K + 1):
            starter_path = run_dir / f"starter_{N:02d}.png"
            if not starter_path.exists():
                if N > 1:
                    prior_last_frame = run_dir / f"last_frame_{N-1:02d}.png"
                    if prior_last_frame.exists():
                        print(f">>> Step 3b/7.{N}: continuity hint from clip {N-1}", flush=True)
                        hint = describe_frame(prior_last_frame)
                        if hint:
                            generate_clip_brief.generate(run_dir, N, prior_continuity_hint=hint)
                            print(f">>> Brief refined: {N}", flush=True)
                print(f">>> Step 4/7.{N}: starter image for clip {N}", flush=True)
                generate_starter_image.generate(run_dir, N)
            else:
                print(f"    starter_{N:02d}.png exists — skip", flush=True)

            vid_path = _clip_video_path(run_dir, N)
            if vid_path is None:
                print(f">>> Step 5/7.{N}: video for clip {N}", flush=True)
                vid_path = _generate_clip_video(
                    run_dir, N, args.clip_duration_s, headless=args.headless,
                )
                print(f">>> Clip video ready: {N}", flush=True)
            else:
                print(f"    {vid_path.name} exists — skip", flush=True)

            last_frame_path = run_dir / f"last_frame_{N:02d}.png"
            if N < K and not last_frame_path.exists():
                print(f">>> Step 6/7.{N}: extract last frame of clip {N}", flush=True)
                extract_last_frame(vid_path, last_frame_path)
                print(f">>> Last frame ready: {N}", flush=True)

            if args.review_mode == "per_clip" and N < K:
                print(f">>> Awaiting approval: {N}", flush=True)
                verdict = wait_for_approval(run_dir, N)
                if verdict == "rejected":
                    print(f">>> Rejected — regenerating clip {N}", flush=True)
                    # Re-run this clip by decrementing N for the next loop tick.
                    # We achieve this with a manual repeat: recurse on this iteration.
                    # Use a while-style do-over by restarting from current N.
                    return _rerun_from(args, N)
                print(f">>> Approved: {N}", flush=True)

        merged_path = run_dir / "merge.mp4"
        if force_step == "merge":
            _delete_if_exists(merged_path)
        if not merged_path.exists():
            print(">>> Step 7/7: merge", flush=True)
            merge_videos.merge(run_dir, merged_path)
            print(f"    wrote {merged_path.name}", flush=True)
        else:
            print(f"    {merged_path.name} exists — skip", flush=True)

        if args.skip_upload:
            print(">>> Upload skipped (--skip-upload)", flush=True)
            return 0

        print(">>> Upload to YouTube", flush=True)
        meta = generate_metadata_for_product(brief, plan)
        (run_dir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        print(f"    title: {meta['title']}", flush=True)
        vid_id = upload_video.upload(merged_path, meta, privacy=args.privacy)
        url = f"https://youtu.be/{vid_id}"
        (run_dir / "youtube_url.txt").write_text(url + "\n")
        print(f"\n=== Done: {url} ===", flush=True)
        return 0
    except KeyboardInterrupt:
        sys.stderr.write("\nInterrupted.\n")
        return 130
    except Exception as e:
        sys.stderr.write(f"\nPipeline failed: {type(e).__name__}: {e}\n")
        traceback.print_exc()
        return 1


def _rerun_from(args: argparse.Namespace, clip_n: int) -> int:
    """Re-invoke main() flow starting from clip_n. Used after a rejection."""
    args.from_step = f"clip{clip_n}"
    saved_argv = sys.argv
    try:
        new_argv = [saved_argv[0], args.run_id,
                    "--review-mode", args.review_mode,
                    "--clip-count", str(args.clip_count),
                    "--clip-duration-s", str(args.clip_duration_s),
                    "--total-duration-s", str(args.total_duration_s),
                    "--privacy", args.privacy,
                    "--from-step", f"clip{clip_n}"]
        if args.skip_upload:
            new_argv.append("--skip-upload")
        if args.parallel:
            new_argv.append("--parallel")
        if args.headless:
            new_argv.append("--headless")
        sys.argv = new_argv
        return main()
    finally:
        sys.argv = saved_argv


def _install_sigterm_handler() -> None:
    def _handler(signum, frame):
        sys.stderr.write(f"\nReceived signal {signum} — exiting.\n")
        sys.exit(143)
    try:
        signal.signal(signal.SIGTERM, _handler)
    except Exception:
        pass


if __name__ == "__main__":
    _install_sigterm_handler()
    sys.exit(main())
