"""Merge MP4s from a run directory into merge.mp4 (lossless concat, with re-encode fallback)."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def find_clips(directory: Path) -> list[Path]:
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() == ".mp4" and p.name != "merge.mp4"
    )


def stream_copy(clips: list[Path], output: Path) -> bool:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        list_path = Path(f.name)
        for c in clips:
            escaped = str(c.resolve()).replace("'", r"'\''")
            f.write(f"file '{escaped}'\n")
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(list_path), "-c", "copy", str(output)],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            return True
        sys.stderr.write(f"Stream copy failed:\n{r.stderr[-1500:]}\n")
        return False
    finally:
        list_path.unlink(missing_ok=True)


def reencode_concat(clips: list[Path], output: Path) -> None:
    cmd: list[str] = ["ffmpeg", "-y"]
    for c in clips:
        cmd += ["-i", str(c)]
    parts = "".join(f"[{i}:v:0][{i}:a:0?]" for i in range(len(clips)))
    cmd += [
        "-filter_complex", f"{parts}concat=n={len(clips)}:v=1:a=1[v][a]",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        str(output),
    ]
    subprocess.run(cmd, check=True)


def merge(directory: Path, output: Path | None = None) -> Path:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")
    clips = find_clips(directory)
    if not clips:
        raise RuntimeError(f"No .mp4 clips in {directory}")
    out = output or (directory / "merge.mp4")
    print(f"Merging {len(clips)} clips:", file=sys.stderr)
    for c in clips:
        print(f"  - {c.name}", file=sys.stderr)
    if not stream_copy(clips, out):
        print("Re-encoding...", file=sys.stderr)
        reencode_concat(clips, out)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge MP4 clips in a directory")
    parser.add_argument("directory", type=Path, help="Directory containing .mp4 clips")
    parser.add_argument("--out", type=Path, help="Output path (default <dir>/merge.mp4)")
    args = parser.parse_args()
    out = merge(args.directory, args.out)
    print(f"\nMerged: {out}", file=sys.stderr)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
