"""End-to-end Object-Talk pipeline orchestrator.

Usage:
    python3.13 pipeline.py "smart factory automation"
    python3.13 pipeline.py "EV charging" --privacy unlisted --skip-upload

Each step is skipped if its output already exists, so re-runs after a failure are cheap.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import run_dir
from steps import generate_scripts
from steps import generate_images
from steps import generate_videos
from steps import generate_videos_comfyui
from steps import merge_videos
from steps import upload_video


def main() -> int:
    parser = argparse.ArgumentParser(description="Object-Talk Hindi Reels pipeline")
    parser.add_argument("subject", help="Subject/domain (e.g. 'smart factory automation')")
    parser.add_argument("--privacy", default="public",
                        choices=["public", "unlisted", "private"])
    parser.add_argument("--headless", action="store_true",
                        help="Run browser headless during video gen (default: visible)")
    parser.add_argument("--skip-upload", action="store_true",
                        help="Stop after merge — do not upload to YouTube")
    parser.add_argument("--from-step", choices=["scripts", "images", "videos", "merge", "upload"],
                        default="scripts",
                        help="Resume from a specific step (forces that step to re-run)")
    parser.add_argument("--parallel", action="store_true",
                        help="Run image generations concurrently and use multi-tab video generation")
    parser.add_argument("--video-provider", default=None,
                        choices=["grok", "comfyui"],
                        help="Which backend renders the 5 clips. Defaults to env VIDEO_PROVIDER or 'grok'.")
    parser.add_argument("--skip-images", action="store_true",
                        help="Skip Gemini image generation (step 2). The video provider runs "
                             "text-only with the character description folded into the prompt.")
    parser.add_argument("--clip-count", type=int, default=5,
                        help="Number of clips per video (1-20). Default 5.")
    parser.add_argument("--clip-duration-s", type=int, default=10,
                        help="Target spoken duration per clip in seconds (5-30). Default 10.")
    parser.add_argument("--max-words", type=int, default=None,
                        help="Manual override for max Hindi words per script. "
                             "Default = duration_s * 3 - 5 (~25 for 10s).")
    args = parser.parse_args()

    if not 1 <= args.clip_count <= 20:
        sys.stderr.write(f"--clip-count must be 1..20, got {args.clip_count}\n")
        return 1
    if not 5 <= args.clip_duration_s <= 30:
        sys.stderr.write(f"--clip-duration-s must be 5..30, got {args.clip_duration_s}\n")
        return 1

    # Resolve video provider: CLI > env > default 'grok'
    import os as _os
    video_provider = args.video_provider or _os.environ.get("VIDEO_PROVIDER", "grok")
    if video_provider not in ("grok", "comfyui"):
        sys.stderr.write(f"unknown --video-provider '{video_provider}'\n")
        return 1

    # Distinct exit code for Grok quota-exhausted so the webapp can render a
    # different error than a generic failure (and so the user knows the only
    # remedy is to wait + retry from the videos step).
    QUOTA_EXIT_CODE = 42

    out = run_dir(args.subject)
    print(f"\n=== Run dir: {out} ===\n", flush=True)

    scripts_path = out / "scripts.json"
    merged_path = out / "merge.mp4"

    # Set frame count for the video step based on clip duration.
    # Wan needs (length-1)%4==0, so we round to the next valid length.
    fps = 24
    target_frames = args.clip_duration_s * fps + 1   # e.g. 10s → 241
    # Round UP to satisfy (n-1)%4==0
    while (target_frames - 1) % 4 != 0:
        target_frames += 1
    import os as _os
    _os.environ["COMFYUI_WAN_FRAMES"] = str(target_frames)
    # LTX uses (length-1)%8==0 — compute separately for safety
    target_frames_ltx = args.clip_duration_s * fps + 1
    while (target_frames_ltx - 1) % 8 != 0:
        target_frames_ltx += 1
    _os.environ["COMFYUI_FRAMES"] = str(target_frames_ltx)
    # Wan S2V auto-sizes length from audio duration but still needs to know
    # the upper bound so TTS can be paced to fit. Stash the per-clip duration
    # ceiling so the videos step can read it.
    _os.environ["CLIP_DURATION_S"] = str(args.clip_duration_s)
    print(f"  (clip target: {args.clip_count} × {args.clip_duration_s}s = "
          f"{args.clip_count * args.clip_duration_s}s total; "
          f"frames: wan={target_frames}, ltx={target_frames_ltx})", flush=True)

    # Step 1: scripts
    if args.from_step in ("scripts",) or not scripts_path.exists():
        print(">>> Step 1/5: generate scripts", flush=True)
        payload = generate_scripts.generate(args.subject,
                                            count=args.clip_count,
                                            duration_s=args.clip_duration_s,
                                            max_words=args.max_words)
        scripts_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"    wrote {scripts_path.name}\n", flush=True)
    else:
        print(f"--- Step 1/5: scripts.json exists, skip ---\n", flush=True)

    # Step 2: images (skipped when --skip-images — text-only video mode)
    if args.skip_images:
        print(">>> Step 2/5: skipped (--skip-images, text-only video mode)\n", flush=True)
        # Surface this to the video provider via env so it knows not to look
        # for img_NN_* files and to fold the character description into the prompt.
        import os as _os
        _os.environ["SKIP_IMAGES"] = "1"
    else:
        have_images = list(out.glob("img_*"))
        need_images = args.from_step in ("scripts", "images") or len(have_images) < args.clip_count
        if need_images:
            print(">>> Step 2/5: generate images", flush=True)
            generate_images.generate_all(scripts_path, out, parallel=args.parallel)
            print(flush=True)
        else:
            print(f"--- Step 2/5: {len(have_images)} images exist, skip ---\n", flush=True)

    # Step 3: videos
    have_videos = list(out.glob("vid_*.mp4"))
    need_videos = args.from_step in ("scripts", "images", "videos") or len(have_videos) < args.clip_count
    if need_videos:
        if video_provider == "comfyui":
            print(">>> Step 3/5: generate videos via ComfyUI", flush=True)
            generate_videos_comfyui.generate_all(
                scripts_path, out, headless=args.headless, parallel=args.parallel,
            )
        else:
            print(">>> Step 3/5: generate videos via Grok", flush=True)
            try:
                generate_videos.generate_all(scripts_path, out, headless=args.headless, parallel=args.parallel)
            except generate_videos.GrokQuotaExceeded as e:
                # Don't let the traceback flood the log — emit a clear marker the
                # webapp can pattern-match on, then exit fast.
                print(f"\n⛔ {e}", flush=True)
                print(f"=== {generate_videos.QUOTA_MARKER}: halted before merge/upload ===", flush=True)
                return QUOTA_EXIT_CODE
        print(flush=True)
    else:
        print(f"--- Step 3/5: {len(have_videos)} videos exist, skip ---\n", flush=True)

    # Step 4: merge
    need_merge = args.from_step in ("scripts", "images", "videos", "merge") or not merged_path.exists()
    if need_merge:
        print(">>> Step 4/5: merge", flush=True)
        merge_videos.merge(out, merged_path)
        print(f"    wrote {merged_path.name}\n", flush=True)
    else:
        print(f"--- Step 4/5: merge.mp4 exists, skip ---\n", flush=True)

    # Step 5: upload
    if args.skip_upload:
        print(">>> Step 5/5: skipped (--skip-upload)\n", flush=True)
        return 0

    print(">>> Step 5/5: upload to YouTube", flush=True)
    scripts_payload = json.loads(scripts_path.read_text())
    meta = upload_video.generate_metadata(args.subject, scripts_payload)
    (out / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"    title: {meta['title']}", flush=True)
    vid_id = upload_video.upload(merged_path, meta, privacy=args.privacy)
    url = f"https://youtu.be/{vid_id}"
    (out / "youtube_url.txt").write_text(url + "\n")
    print(f"\n=== Done: {url} ===", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
