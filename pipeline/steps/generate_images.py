"""Generate 5 PNGs (9:16) from the image prompts in scripts.json using Gemini image model."""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import GEMINI_API_KEY, GEMINI_IMAGE_MODEL

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"


_MIME_TO_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


def generate_one(prompt: str, out_path_stem: Path) -> Path:
    """Generate an image. Returns the actual path written (with extension matching the response mime)."""
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": "9:16"},
        },
    }
    url = ENDPOINT.format(model=GEMINI_IMAGE_MODEL, key=GEMINI_API_KEY)
    r = requests.post(url, json=body, timeout=180)
    if r.status_code != 200:
        sys.stderr.write(f"Gemini image error {r.status_code}:\n{r.text}\n")
        r.raise_for_status()
    data = r.json()
    parts = data["candidates"][0]["content"]["parts"]
    for p in parts:
        inline = p.get("inlineData") or p.get("inline_data")
        if inline and "data" in inline:
            mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
            ext = _MIME_TO_EXT.get(mime, ".bin")
            out = out_path_stem.with_suffix(ext)
            out.write_bytes(base64.b64decode(inline["data"]))
            return out
    raise RuntimeError(f"No image in response. Parts: {[list(p.keys()) for p in parts]}")


def generate_all(scripts_path: Path, out_dir: Path,
                 only: list[int] | None = None,
                 parallel: bool = False) -> list[Path]:
    payload = json.loads(scripts_path.read_text())
    out_dir.mkdir(parents=True, exist_ok=True)
    tasks: list[tuple[int, dict, Path]] = []  # (idx, script, stem)
    for i, s in enumerate(payload["scripts"], 1):
        if only and i not in only:
            continue
        for existing in out_dir.glob(f"img_{i:02d}_*"):
            existing.unlink()
        stem = out_dir / f"img_{i:02d}_{_slug(s['object'])}"
        tasks.append((i, s, stem))

    def _one(idx: int, s: dict, stem: Path) -> tuple[int, Path]:
        print(f"[{idx}/5] {s['object']}: generating image...", file=sys.stderr)
        out = generate_one(s["image_prompt"], stem)
        print(f"      saved {out.name} ({out.stat().st_size // 1024} KB)", file=sys.stderr)
        return idx, out

    if parallel and len(tasks) > 1:
        from concurrent.futures import ThreadPoolExecutor
        print(f"(parallel: {len(tasks)} images concurrent)", file=sys.stderr)
        results: list[tuple[int, Path]] = []
        with ThreadPoolExecutor(max_workers=min(5, len(tasks))) as ex:
            for r in ex.map(lambda t: _one(*t), tasks):
                results.append(r)
        results.sort(key=lambda r: r[0])
        return [p for _, p in results]
    else:
        return [_one(*t)[1] for t in tasks]


def _slug(name: str) -> str:
    return "-".join(name.lower().split())[:30]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate 5 images from scripts.json")
    parser.add_argument("scripts_json", type=Path, help="Path to scripts.json")
    parser.add_argument("--out", type=Path, help="Output dir (defaults to same dir as scripts.json)")
    parser.add_argument("--only", type=int, nargs="+",
                        help="Only generate these 1-based indices")
    parser.add_argument("--parallel", action="store_true",
                        help="Run image generations concurrently (up to 5 at a time)")
    args = parser.parse_args()

    if not args.scripts_json.exists():
        sys.stderr.write(f"Not found: {args.scripts_json}\n")
        return 1

    out_dir = args.out or args.scripts_json.parent
    outputs = generate_all(args.scripts_json, out_dir, only=args.only, parallel=args.parallel)
    print(f"\nGenerated {len(outputs)} images in {out_dir}", file=sys.stderr)
    for p in outputs:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
