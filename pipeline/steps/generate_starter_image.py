"""Generate a starter image (9:16) for a clip using Nano Banana Pro with reference image chaining.

For clip N>1 this runs a two-stage flow:
    Stage A  draft the scene from product+continuity refs.
    Stage B  product-correction pass that locks the product identity against the original photos.
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import GEMINI_API_KEY, GEMINI_IMAGE_MODEL
from http_utils import post_with_retry

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_MAX_ATTEMPTS = 3
_MAX_REFS = 4

_IDENTITY_DIRECTIVE = (
    "PRODUCT IDENTITY LOCK — read carefully.\n"
    "The reference images show the EXACT product this video is about. You MUST preserve:\n"
    "  - the product silhouette and proportions,\n"
    "  - every visible logo, brand mark, wordmark, and label, in the same position and font,\n"
    "  - every visible screen, LED, indicator, button, dial, or readout — including any numbers/text shown on them,\n"
    "  - the exact color and finish of every surface,\n"
    "  - any distinctive textures, materials, or shapes.\n"
    "The scene, lighting, camera, character, and palette continue from the prior frame — but the product itself must look identical to the reference photos, down to the smallest detail. Do not stylise, simplify, or reinterpret the product. Do not change which features are present. Do not anthropomorphise. 9:16 vertical."
)

_PRODUCT_CORRECTION_PROMPT = (
    "PRODUCT CORRECTION PASS. Take the LAST reference image (the draft scene) and modify ONLY the "
    "product depicted in it so the product matches the OTHER reference photos exactly — same "
    "silhouette, same logo position and text, same screens/LEDs/displays with the same readout, "
    "same colors and finish, same distinguishing features. Preserve everything else in the draft "
    "EXACTLY: composition, lighting, character, wardrobe, background, palette, framing, camera "
    "lens character. Output a 9:16 vertical image."
)


def _gather_product_images(run_dir: Path, limit: int) -> list[Path]:
    product_dir = run_dir / "product" / "images"
    if not product_dir.exists():
        return []
    files = sorted(p for p in product_dir.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_EXTS)
    return files[:limit]


def _gather_refs(run_dir: Path, clip_index: int) -> list[Path]:
    if clip_index == 1:
        return _gather_product_images(run_dir, limit=_MAX_REFS)

    refs: list[Path] = []

    # 1. Product images FIRST — identity outweighs scene continuity when they conflict.
    product_imgs = _gather_product_images(run_dir, limit=2)
    refs.extend(product_imgs)

    # 2. Previous last frame — the continuity anchor.
    prev_frame = run_dir / f"last_frame_{clip_index - 1:02d}.png"
    if prev_frame.exists():
        if prev_frame not in refs:
            refs.append(prev_frame)
    else:
        sys.stderr.write(f"Warning: missing previous last frame {prev_frame.name}\n")

    # 3. Canonical starter_01 — the look anchor.
    canonical = run_dir / "starter_01.png"
    if canonical.exists():
        if canonical not in refs:
            refs.append(canonical)
    else:
        sys.stderr.write(f"Warning: missing canonical anchor {canonical.name}\n")

    return refs[:_MAX_REFS]


def _inline_part(path: Path) -> dict:
    mime, _ = mimetypes.guess_type(path.name)
    if not mime:
        mime = "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"inlineData": {"mimeType": mime, "data": data}}


def _extract_image(data: dict) -> bytes | None:
    candidates = data.get("candidates") or []
    if not candidates:
        return None
    parts = candidates[0].get("content", {}).get("parts") or []
    for p in parts:
        inline = p.get("inlineData") or p.get("inline_data")
        if inline and "data" in inline:
            return base64.b64decode(inline["data"])
    return None


def _load_product_brief(run_dir: Path) -> dict:
    brief_path = run_dir / "product" / "brief.json"
    if not brief_path.exists():
        return {}
    try:
        return json.loads(brief_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _product_description_line(product_brief: dict, image_prompt: str) -> str | None:
    """Return a short literal product description line if the image prompt doesn't already
    mention the product name or description tokens. Tolerates missing fields."""
    name = (product_brief.get("product_name") or "").strip()
    desc = (product_brief.get("product_description") or "").strip()
    if not name and not desc:
        return None

    prompt_lc = image_prompt.lower()
    tokens: list[str] = []
    if name:
        tokens.extend(t for t in name.lower().split() if len(t) >= 3)
    if desc:
        tokens.extend(t for t in desc.lower().split() if len(t) >= 4)
    if tokens and any(tok in prompt_lc for tok in tokens):
        return None

    name_part = name or "the product"
    desc_part = f" — {desc}" if desc else ""
    return (
        f"The product is: {name_part}{desc_part}. Visible features include any LEDs, displays, "
        f"buttons, dials, labels, and logos shown in the reference photos."
    )


def _call_nano_banana(parts: list[dict], label: str) -> bytes | None:
    """Single Nano Banana call with retries. Returns image bytes or None if all attempts fail."""
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": "9:16"},
        },
    }
    url = ENDPOINT.format(model=GEMINI_IMAGE_MODEL, key=GEMINI_API_KEY)
    last_err: str | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        r = post_with_retry(url, json=body, timeout=180, label=f"{label}#{attempt}")
        if r.status_code != 200:
            sys.stderr.write(f"Gemini image error {r.status_code} [{label}]:\n{r.text}\n")
            r.raise_for_status()
        img_bytes = _extract_image(r.json())
        if img_bytes:
            return img_bytes
        last_err = f"no image part on attempt {attempt}"
        sys.stderr.write(f"[{label}] {last_err}, retrying...\n")
    sys.stderr.write(f"[{label}] giving up after {_MAX_ATTEMPTS} attempts ({last_err})\n")
    return None


def generate(run_dir: Path, clip_index: int) -> Path:
    brief_path = run_dir / "briefs" / f"brief_{clip_index:02d}.json"
    if not brief_path.exists():
        raise FileNotFoundError(f"Brief not found: {brief_path}")
    brief = json.loads(brief_path.read_text())
    image_prompt = brief.get("image_prompt")
    if not image_prompt:
        raise ValueError(f"brief_{clip_index:02d}.json missing 'image_prompt'")

    out_path = run_dir / f"starter_{clip_index:02d}.png"

    # ----- Clip 1: single pass, only product images as refs. -----
    if clip_index == 1:
        refs = _gather_refs(run_dir, clip_index)
        parts: list[dict] = [{"text": image_prompt}]
        for ref in refs:
            parts.append(_inline_part(ref))
        img_bytes = _call_nano_banana(parts, label=f"starter:{clip_index:02d}")
        if not img_bytes:
            raise RuntimeError(f"Starter image generation failed after {_MAX_ATTEMPTS} attempts")
        out_path.write_bytes(img_bytes)
        print(f">>> Starter ready: {clip_index}")
        return out_path

    # ----- Clip N>1: two-stage flow. -----
    product_brief = _load_product_brief(run_dir)
    desc_line = _product_description_line(product_brief, image_prompt)
    stage_a_prompt_body = f"{desc_line}\n\n{image_prompt}" if desc_line else image_prompt
    stage_a_prompt = f"{_IDENTITY_DIRECTIVE}\n\n{stage_a_prompt_body}"

    refs_a = _gather_refs(run_dir, clip_index)
    parts_a: list[dict] = [{"text": stage_a_prompt}]
    for ref in refs_a:
        parts_a.append(_inline_part(ref))

    tmp_dir = run_dir / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    draft_path = tmp_dir / f"starter_{clip_index:02d}_draft.png"

    draft_bytes = _call_nano_banana(parts_a, label=f"starter:{clip_index:02d}:A")
    if not draft_bytes:
        raise RuntimeError(f"Starter image (stage A) generation failed after {_MAX_ATTEMPTS} attempts")
    draft_path.write_bytes(draft_bytes)

    # Stage B: product correction. Product images first, draft LAST (the prompt refers to it).
    product_imgs = _gather_product_images(run_dir, limit=_MAX_REFS - 1)
    parts_b: list[dict] = [{"text": _PRODUCT_CORRECTION_PROMPT}]
    for ref in product_imgs:
        parts_b.append(_inline_part(ref))
    parts_b.append(_inline_part(draft_path))

    final_bytes = _call_nano_banana(parts_b, label=f"starter:{clip_index:02d}:B")
    if final_bytes:
        out_path.write_bytes(final_bytes)
        # Success — clean up tmp draft.
        try:
            draft_path.unlink()
            # rmdir only if empty
            if not any(tmp_dir.iterdir()):
                tmp_dir.rmdir()
        except OSError:
            pass
    else:
        sys.stderr.write(
            f"(warn) product correction pass failed for clip {clip_index}, using draft\n"
        )
        shutil.copyfile(draft_path, out_path)
        # Keep _tmp/ around for debugging on failure.

    print(f">>> Starter ready: {clip_index}")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a starter image for a clip using reference chaining")
    parser.add_argument("--run-dir", type=Path, required=True, help="Run output directory")
    parser.add_argument("--clip-index", type=int, required=True, help="1-based clip index")
    args = parser.parse_args()

    if not args.run_dir.exists():
        sys.stderr.write(f"Run dir not found: {args.run_dir}\n")
        return 1
    if args.clip_index < 1:
        sys.stderr.write("--clip-index must be >= 1\n")
        return 1

    out = generate(args.run_dir, args.clip_index)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
