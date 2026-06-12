"""Extract last/first frame from a video as a PNG/JPEG via ffmpeg."""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


_FFMPEG_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def _ffmpeg_duration(video_path: Path) -> float:
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video_path)],
        capture_output=True, text=True, check=False,
    )
    m = _FFMPEG_DURATION_RE.search(r.stderr or "")
    if m:
        h, mn, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        return h * 3600 + mn * 60 + s
    raise RuntimeError(f"could not parse duration from ffmpeg output: {(r.stderr or '')[-500:]}")


def _ffprobe_duration(video_path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True, check=False,
    )
    if r.returncode == 0:
        out = (r.stdout or "").strip()
        try:
            return float(out)
        except ValueError:
            pass
    return _ffmpeg_duration(video_path)


def _grab_frame(video_path: Path, out_path: Path, ts: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ffmpeg", "-ss", f"{ts:.3f}", "-i", str(video_path),
         "-vframes", "1", "-q:v", "2", "-y", str(out_path)],
        capture_output=True, text=True, check=False,
    )


def _cv2_extract_frame(video_path: Path, out_path: Path, ts_from_end_s: float | None = None) -> bool:
    try:
        import cv2
    except ImportError:
        return False
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return False
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    if ts_from_end_s is None:
        target = max(0, total - 2)
    else:
        target = max(0, total - int(round(ts_from_end_s * fps)))
    cap.set(cv2.CAP_PROP_POS_FRAMES, target)
    ok, frame = cap.read()
    if not ok:
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, total - 1))
        ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(out_path), frame))


def extract_last_frame(video_path: Path, out_path: Path, offset_from_end_s: float = 0.05) -> Path:
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if _cv2_extract_frame(video_path, out_path, offset_from_end_s) and \
            out_path.exists() and out_path.stat().st_size >= 1024:
        return out_path

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("cv2 unavailable and ffmpeg not found on PATH")
    if shutil.which("ffprobe") is None:
        raise RuntimeError("cv2 unavailable and ffprobe not found on PATH")
    duration = _ffprobe_duration(video_path)
    last_stderr = ""
    for offset in (offset_from_end_s, 0.5, 1.0):
        ts = max(0.0, duration - offset)
        r = _grab_frame(video_path, out_path, ts)
        last_stderr = r.stderr or ""
        if r.returncode == 0 and out_path.exists() and out_path.stat().st_size >= 1024:
            return out_path

    raise RuntimeError(f"frame extraction failed for {video_path}: {last_stderr[-1500:]}")


def extract_first_frame(video_path: Path, out_path: Path) -> Path:
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    r = _grab_frame(video_path, out_path, 0.0)
    if r.returncode != 0 or not out_path.exists() or out_path.stat().st_size < 1:
        raise RuntimeError(f"ffmpeg failed to extract first frame from {video_path}: {(r.stderr or '')[-1500:]}")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract last frame of a video as an image")
    parser.add_argument("video_path", type=Path)
    parser.add_argument("out_path", type=Path)
    parser.add_argument("--offset", type=float, default=0.05,
                        help="Seconds back from end (default 0.05)")
    parser.add_argument("--first", action="store_true",
                        help="Extract first frame instead of last")
    args = parser.parse_args()

    if args.first:
        out = extract_first_frame(args.video_path, args.out_path)
    else:
        out = extract_last_frame(args.video_path, args.out_path, args.offset)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
