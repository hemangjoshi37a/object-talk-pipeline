"""Generate a per-clip production brief from a master plan slot using Gemini text."""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import GEMINI_API_KEY, GEMINI_TEXT_MODEL, PROMPTS_DIR
from http_utils import post_with_retry

SYSTEM_PROMPT_PATH = PROMPTS_DIR / "clip_brief_system.md"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

VALID_ROLES = {"hook", "middle", "cta"}
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a Gemini text response."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in response:\n{text[:500]}")
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError(f"Unbalanced braces in response:\n{text[:500]}")


def _validate(payload: dict, *, clip_index: int) -> None:
    if not isinstance(payload, dict):
        raise ValueError("brief is not a JSON object")
    if payload.get("clip_index") != clip_index:
        raise ValueError(
            f"clip_index mismatch: expected {clip_index}, got {payload.get('clip_index')!r}"
        )
    role = payload.get("role")
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {sorted(VALID_ROLES)}, got {role!r}")
    duration = payload.get("duration_s")
    if not isinstance(duration, int) or duration <= 0:
        raise ValueError(f"duration_s must be a positive int, got {duration!r}")

    scene = payload.get("scene")
    if not isinstance(scene, str) or not scene.strip():
        raise ValueError("scene must be a non-empty string")

    characters = payload.get("characters")
    if not isinstance(characters, list):
        raise ValueError("characters must be a list")
    for i, c in enumerate(characters):
        if not isinstance(c, dict):
            raise ValueError(f"characters[{i}] must be an object")
        for k in ("name", "wardrobe", "expression", "action"):
            if not isinstance(c.get(k), str) or not c[k].strip():
                raise ValueError(f"characters[{i}].{k} must be a non-empty string")

    props = payload.get("props")
    if not isinstance(props, list) or not all(isinstance(p, str) for p in props):
        raise ValueError("props must be a list of strings")

    lighting = payload.get("lighting")
    if not isinstance(lighting, dict):
        raise ValueError("lighting must be an object")
    for k in ("key", "fill", "mood"):
        if not isinstance(lighting.get(k), str) or not lighting[k].strip():
            raise ValueError(f"lighting.{k} must be a non-empty string")

    palette = payload.get("color_palette")
    if not isinstance(palette, list) or not palette:
        raise ValueError("color_palette must be a non-empty list")
    for i, hx in enumerate(palette):
        if not isinstance(hx, str) or not HEX_RE.match(hx):
            raise ValueError(f"color_palette[{i}] must be a #RRGGBB hex string, got {hx!r}")

    camera = payload.get("camera")
    if not isinstance(camera, dict):
        raise ValueError("camera must be an object")
    for k in ("shot_type", "movement", "angle"):
        if not isinstance(camera.get(k), str) or not camera[k].strip():
            raise ValueError(f"camera.{k} must be a non-empty string")
    lens = camera.get("lens_mm")
    if not isinstance(lens, int) or lens <= 0:
        raise ValueError(f"camera.lens_mm must be a positive int, got {lens!r}")

    action = payload.get("action")
    if not isinstance(action, str) or not action.strip():
        raise ValueError("action must be a non-empty string")

    dialogue = payload.get("dialogue")
    if not isinstance(dialogue, dict):
        raise ValueError("dialogue must be an object")
    if not isinstance(dialogue.get("text"), str):
        raise ValueError("dialogue.text must be a string")
    if not isinstance(dialogue.get("lang"), str) or not dialogue["lang"].strip():
        raise ValueError("dialogue.lang must be a non-empty string")
    voice = dialogue.get("voice")
    if not isinstance(voice, dict):
        raise ValueError("dialogue.voice must be an object")
    for k in ("tone", "type"):
        if not isinstance(voice.get(k), str) or not voice[k].strip():
            raise ValueError(f"dialogue.voice.{k} must be a non-empty string")

    for field in ("image_prompt", "video_prompt", "continuity_notes"):
        v = payload.get(field)
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"{field} must be a non-empty string")


def _find_clip_slot(plan: dict, clip_index: int) -> dict:
    clips = plan.get("clips") or plan.get("slots") or []
    if not isinstance(clips, list):
        raise ValueError("plan.json missing 'clips' (or 'slots') list")
    for c in clips:
        if isinstance(c, dict) and (c.get("index") == clip_index or c.get("clip_index") == clip_index):
            return c
    raise ValueError(f"plan.json has no clip with index {clip_index}")


def _global_context(plan: dict) -> dict:
    """Extract the global anchors a slot must conform to."""
    g = plan.get("global") or {}
    if isinstance(g, dict) and g:
        return g
    return {
        k: plan[k]
        for k in ("palette", "color_palette", "characters", "world", "voice", "tone", "language")
        if k in plan
    }


def _call_gemini(
    *,
    plan: dict,
    clip_slot: dict,
    product_brief: dict,
    clip_index: int,
    prior_continuity_hint: str | None,
    prior_attempts: list[str],
) -> str:
    system_prompt = SYSTEM_PROMPT_PATH.read_text()

    global_ctx = _global_context(plan)
    schema_hint = {
        "clip_index": clip_index,
        "role": "hook|middle|cta",
        "duration_s": int(clip_slot.get("duration_s") or plan.get("clip_duration_s") or 10),
        "scene": "str",
        "characters": [{"name": "str", "wardrobe": "str", "expression": "str", "action": "str"}],
        "props": ["str"],
        "lighting": {"key": "str", "fill": "str", "mood": "str"},
        "color_palette": ["#RRGGBB"],
        "camera": {"shot_type": "str", "lens_mm": 0, "movement": "str", "angle": "str"},
        "action": "str",
        "dialogue": {"text": "str", "lang": "str", "voice": {"tone": "str", "type": "str"}},
        "image_prompt": "str",
        "video_prompt": "str",
        "continuity_notes": "str",
    }

    parts = [
        f"=== MASTER PLAN (global anchors — do NOT contradict) ===",
        json.dumps(global_ctx, ensure_ascii=False, indent=2),
        "",
        f"=== THIS CLIP SLOT (index {clip_index}) ===",
        json.dumps(clip_slot, ensure_ascii=False, indent=2),
        "",
        "=== PRODUCT BRIEF (facts you must honour) ===",
        json.dumps(product_brief, ensure_ascii=False, indent=2),
        "",
    ]
    if prior_continuity_hint:
        parts += [
            "=== PRIOR CLIP LAST-FRAME CONTINUITY HINT ===",
            f"PRIOR CONTINUITY (visually continue from this frame): {prior_continuity_hint}",
            "",
            "The image_prompt MUST explicitly reference this prior last-frame so the stills model "
            "continues from that exact visual state (same subject identity, wardrobe, location, "
            "lighting key, palette, and camera language).",
            "",
        ]
    else:
        parts += [
            "This is the FIRST clip (or no prior continuity hint provided). "
            "Establish the world from scratch — the image_prompt should describe the opening visual cleanly.",
            "",
        ]

    parts += [
        "=== OUTPUT SCHEMA (return strict JSON matching this shape) ===",
        json.dumps(schema_hint, ensure_ascii=False, indent=2),
        "",
        f"Set clip_index = {clip_index}. Use the role from the slot. duration_s must match the slot's duration.",
        "Return ONLY the JSON object — no markdown fences, no commentary.",
    ]
    if prior_attempts:
        parts.append("\nPrevious attempts FAILED with these errors — FIX them this time:")
        for note in prior_attempts:
            parts.append(f"- {note}")

    user_text = "\n".join(parts)

    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {
            "temperature": 0.7 if not prior_attempts else 0.3,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    url = ENDPOINT.format(model=GEMINI_TEXT_MODEL, key=GEMINI_API_KEY)
    r = post_with_retry(url, json=body, timeout=120, label=f"brief-{clip_index:02d}")
    if r.status_code != 200:
        sys.stderr.write(f"Gemini error {r.status_code}:\n{r.text}\n")
        r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def _load_plan(run_dir: Path) -> dict:
    plan_path = run_dir / "plan.json"
    if not plan_path.exists():
        raise FileNotFoundError(f"plan.json missing at {plan_path}")
    return json.loads(plan_path.read_text())


def _load_product_brief(run_dir: Path) -> dict:
    brief_path = run_dir / "product" / "brief.json"
    if not brief_path.exists():
        raise FileNotFoundError(f"product/brief.json missing at {brief_path}")
    return json.loads(brief_path.read_text())


def generate(
    run_dir: Path,
    clip_index: int,
    prior_continuity_hint: str | None = None,
    *,
    max_retries: int = 3,
) -> dict:
    plan = _load_plan(run_dir)
    clip_slot = _find_clip_slot(plan, clip_index)
    product_brief = _load_product_brief(run_dir)

    notes: list[str] = []
    last_error: Exception | None = None
    for attempt in range(max_retries):
        text = _call_gemini(
            plan=plan,
            clip_slot=clip_slot,
            product_brief=product_brief,
            clip_index=clip_index,
            prior_continuity_hint=prior_continuity_hint,
            prior_attempts=notes,
        )
        try:
            payload = _extract_json(text)
            _validate(payload, clip_index=clip_index)
            briefs_dir = run_dir / "briefs"
            briefs_dir.mkdir(parents=True, exist_ok=True)
            out_path = briefs_dir / f"brief_{clip_index:02d}.json"
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            print(f">>> Brief ready: {clip_index}")
            return payload
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            note = str(e)[:300]
            notes.append(note)
            sys.stderr.write(f"[brief {clip_index:02d} attempt {attempt+1}] {note} — retrying\n")
            debug_path = Path(f"/tmp/gemini_brief_{clip_index:02d}_raw_{attempt+1}.txt")
            debug_path.write_text(text)
    assert last_error is not None
    raise RuntimeError(
        f"Clip brief generation failed for clip {clip_index} after {max_retries} attempts: {last_error}"
    )


def _clip_indices(plan: dict) -> list[int]:
    clips = plan.get("clips") or plan.get("slots") or []
    indices: list[int] = []
    for c in clips:
        if not isinstance(c, dict):
            continue
        idx = c.get("index", c.get("clip_index"))
        if isinstance(idx, int):
            indices.append(idx)
    if not indices:
        raise ValueError("plan.json has no clips with an 'index' field")
    return sorted(indices)


def generate_all(run_dir: Path, parallel: bool = True) -> list[dict]:
    plan = _load_plan(run_dir)
    indices = _clip_indices(plan)
    results: dict[int, dict] = {}
    if parallel and len(indices) > 1:
        with ThreadPoolExecutor(max_workers=len(indices)) as ex:
            futures = {ex.submit(generate, run_dir, i, None): i for i in indices}
            for fut in futures:
                i = futures[fut]
                results[i] = fut.result()
    else:
        for i in indices:
            results[i] = generate(run_dir, i, None)
    return [results[i] for i in sorted(results)]


def main_cli() -> int:
    parser = argparse.ArgumentParser(description="Generate per-clip production briefs from a master plan")
    parser.add_argument("--run-dir", type=Path, required=True, help="Run directory containing plan.json + product/brief.json")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--clip-index", type=int, help="Generate brief for a single clip index")
    group.add_argument("--all", action="store_true", help="Generate briefs for all clips in plan.json")
    parser.add_argument("--parallel", action="store_true", help="When using --all, run clips concurrently")
    parser.add_argument("--prior-hint", type=str, default=None,
                        help="Optional prior-clip last-frame continuity hint (only meaningful with --clip-index > 1)")
    args = parser.parse_args()

    run_dir: Path = args.run_dir
    if not run_dir.exists():
        print(f"run-dir does not exist: {run_dir}", file=sys.stderr)
        return 2

    if args.all:
        briefs = generate_all(run_dir, parallel=args.parallel)
        print(json.dumps(briefs, ensure_ascii=False, indent=2))
    else:
        brief = generate(run_dir, args.clip_index, prior_continuity_hint=args.prior_hint)
        print(json.dumps(brief, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
