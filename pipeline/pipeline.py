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
    args = parser.parse_args()

    out = run_dir(args.subject)
    print(f"\n=== Run dir: {out} ===\n", flush=True)

    scripts_path = out / "scripts.json"
    merged_path = out / "merge.mp4"

    # Step 1: scripts
    if args.from_step in ("scripts",) or not scripts_path.exists():
        print(">>> Step 1/5: generate scripts", flush=True)
        payload = generate_scripts.generate(args.subject)
        scripts_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"    wrote {scripts_path.name}\n", flush=True)
    else:
        print(f"--- Step 1/5: scripts.json exists, skip ---\n", flush=True)

    # Step 2: images
    have_images = list(out.glob("img_*"))
    need_images = args.from_step in ("scripts", "images") or len(have_images) < 5
    if need_images:
        print(">>> Step 2/5: generate images", flush=True)
        generate_images.generate_all(scripts_path, out, parallel=args.parallel)
        print(flush=True)
    else:
        print(f"--- Step 2/5: {len(have_images)} images exist, skip ---\n", flush=True)

    # Step 3: videos
    have_videos = list(out.glob("vid_*.mp4"))
    need_videos = args.from_step in ("scripts", "images", "videos") or len(have_videos) < 5
    if need_videos:
        print(">>> Step 3/5: generate videos via Grok", flush=True)
        generate_videos.generate_all(scripts_path, out, headless=args.headless, parallel=args.parallel)
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
