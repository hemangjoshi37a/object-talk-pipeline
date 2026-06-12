"""Generate a master video plan for a product video from a brief + scraped facts."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import GEMINI_API_KEY, GEMINI_TEXT_MODEL, PROMPTS_DIR
from http_utils import post_with_retry

SYSTEM_PROMPT_PATH = PROMPTS_DIR / "plan_system.md"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

_REQUIRED_GLOBAL_FIELDS = (
    "total_duration_s", "clip_count", "clip_duration_s", "language",
    "palette", "lighting_style", "world", "characters", "music_mood",
    "voice_profile", "narrative_logline",
)
_REQUIRED_CLIP_FIELDS = (
    "index", "role", "purpose", "key_moment",
    "narrative_beat", "continuity_with_previous", "voiceover_hint",
)
_VALID_ROLES = {"hook", "middle", "cta"}


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
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


def _validate(payload: dict, *, clip_count: int, clip_duration_s: int, total_duration_s: int) -> None:
    g = payload.get("global")
    if not isinstance(g, dict):
        raise ValueError("Missing 'global' object")
    for field in _REQUIRED_GLOBAL_FIELDS:
        if field not in g:
            raise ValueError(f"global missing field: {field}")
    if int(g.get("clip_count", 0)) != clip_count:
        raise ValueError(f"global.clip_count={g.get('clip_count')} != requested {clip_count}")
    if int(g.get("clip_duration_s", 0)) != clip_duration_s:
        raise ValueError(f"global.clip_duration_s={g.get('clip_duration_s')} != requested {clip_duration_s}")
    if int(g.get("total_duration_s", 0)) != total_duration_s:
        raise ValueError(f"global.total_duration_s={g.get('total_duration_s')} != requested {total_duration_s}")
    palette = g.get("palette")
    if not isinstance(palette, list) or not (3 <= len(palette) <= 6):
        raise ValueError(f"global.palette must be a 3-6 element list, got {palette!r}")
    for c in palette:
        if not (isinstance(c, str) and re.match(r"^#[0-9a-fA-F]{6}$", c)):
            raise ValueError(f"palette entry not a #RRGGBB hex: {c!r}")
    characters = g.get("characters")
    if not isinstance(characters, list):
        raise ValueError("global.characters must be a list")
    for i, ch in enumerate(characters):
        if not isinstance(ch, dict):
            raise ValueError(f"characters[{i}] must be an object")
        for field in ("name", "description", "voice"):
            if field not in ch:
                raise ValueError(f"characters[{i}] missing field: {field}")
    vp = g.get("voice_profile")
    if not isinstance(vp, dict):
        raise ValueError("global.voice_profile must be an object")
    for field in ("tone", "type", "lang"):
        if field not in vp:
            raise ValueError(f"voice_profile missing field: {field}")

    clips = payload.get("clips")
    if not isinstance(clips, list) or len(clips) != clip_count:
        raise ValueError(f"Expected {clip_count} clips, got {len(clips) if isinstance(clips, list) else 'non-list'}")
    seen_indices: set[int] = set()
    for n, c in enumerate(clips, 1):
        if not isinstance(c, dict):
            raise ValueError(f"clips[{n}] must be an object")
        for field in _REQUIRED_CLIP_FIELDS:
            if field not in c:
                raise ValueError(f"clip #{n} missing field: {field}")
        idx = int(c["index"])
        if idx != n:
            raise ValueError(f"clip #{n} has index={idx} (expected {n}); clips must be ordered 1..K")
        if idx in seen_indices:
            raise ValueError(f"duplicate clip index: {idx}")
        seen_indices.add(idx)
        role = c["role"]
        if role not in _VALID_ROLES:
            raise ValueError(f"clip #{n} role={role!r} not in {_VALID_ROLES}")

    if clips[0]["role"] != "hook":
        raise ValueError(f"clip 1 role must be 'hook', got {clips[0]['role']!r}")
    if clips[-1]["role"] != "cta":
        raise ValueError(f"clip {clip_count} role must be 'cta', got {clips[-1]['role']!r}")
    if clip_count >= 3:
        for c in clips[1:-1]:
            if c["role"] != "middle":
                raise ValueError(f"clip {c['index']} role must be 'middle', got {c['role']!r}")


def _format_brief(product_brief: dict) -> str:
    def _g(*keys: str) -> str:
        """Return the first non-empty value across the given key aliases."""
        for k in keys:
            v = product_brief.get(k)
            if v is None or v == "":
                continue
            if isinstance(v, (list, dict)):
                return json.dumps(v, ensure_ascii=False)
            return str(v)
        return "unspecified"

    return (
        f"company: {_g('company', 'company_name')}\n"
        f"product: {_g('product', 'product_name')}\n"
        f"product_description: {_g('product_description')}\n"
        f"audience: {_g('audience', 'target_audience')}\n"
        f"tone: {_g('tone')}\n"
        f"language: {_g('language')}\n"
        f"voice_tone: {_g('voice_tone')}\n"
        f"voice_type: {_g('voice_type')}\n"
        f"feeling_to_evoke: {_g('feeling_to_evoke')}\n"
        f"vision_statement: {_g('vision_statement')}\n"
        f"visual_style_preference: {_g('visual_style_preference')}\n"
        f"structure_hook_prompt: {_g('structure_hook_prompt')}\n"
        f"structure_middle_prompt: {_g('structure_middle_prompt')}\n"
        f"structure_cta_prompt: {_g('structure_cta_prompt')}\n"
        f"structure_prompt: {_g('structure_prompt')}\n"
        f"key_facts: {_g('key_facts')}\n"
        f"website_url: {_g('website_url')}\n"
    )


def _format_scraped(scraped: dict | None) -> str:
    if not scraped:
        return "(no scraped website data provided)"
    title = scraped.get("title") or "unspecified"
    description = scraped.get("description") or "unspecified"
    text = scraped.get("text") or scraped.get("excerpt") or ""
    excerpt = text[:1500] if isinstance(text, str) else ""
    return (
        f"site_title: {title}\n"
        f"site_description: {description}\n"
        f"site_excerpt: {excerpt}\n"
    )


def _call_gemini(
    product_brief: dict,
    scraped: dict | None,
    *,
    clip_count: int,
    clip_duration_s: int,
    total_duration_s: int,
    prior_notes: list[str],
) -> str:
    system_prompt = SYSTEM_PROMPT_PATH.read_text()

    schema_hint = json.dumps({
        "global": {
            "total_duration_s": "int",
            "clip_count": "int",
            "clip_duration_s": "int",
            "language": "str",
            "palette": ["#RRGGBB", "#RRGGBB", "#RRGGBB"],
            "lighting_style": "str",
            "world": "str",
            "characters": [{"name": "str", "description": "str", "voice": "str"}],
            "music_mood": "str",
            "voice_profile": {"tone": "str", "type": "str", "lang": "str"},
            "narrative_logline": "str",
        },
        "clips": [
            {
                "index": 1,
                "role": "hook | middle | cta",
                "purpose": "str",
                "key_moment": "str",
                "narrative_beat": "str",
                "continuity_with_previous": "str",
                "voiceover_hint": "str",
            }
        ],
    }, indent=2)

    user_text = (
        f"Plan a vertical 9:16 product video.\n\n"
        f"=== PARAMETERS ===\n"
        f"total_duration_s: {total_duration_s}\n"
        f"clip_count: {clip_count}\n"
        f"clip_duration_s: {clip_duration_s}\n"
        f"(clip 1 = hook, clip {clip_count} = cta, others = middle)\n\n"
        f"=== PRODUCT BRIEF (authoritative — do not contradict, do not invent beyond) ===\n"
        f"{_format_brief(product_brief)}\n"
        f"=== SCRAPED WEBSITE FACTS ===\n"
        f"{_format_scraped(scraped)}\n"
        f"=== OUTPUT JSON SCHEMA ===\n"
        f"{schema_hint}\n\n"
        f"Return strict JSON only. clips array MUST have exactly {clip_count} entries indexed 1..{clip_count}."
    )

    if prior_notes:
        user_text += "\n\n=== PREVIOUS ATTEMPT FAILED ===\n"
        for note in prior_notes:
            user_text += f"- {note}\n"
        user_text += (
            f"\nFix every issue above. Keep clip_count = {clip_count}, "
            f"clip 1 role = hook, clip {clip_count} role = cta, "
            f"intermediate clips role = middle. Return valid JSON only."
        )

    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {
            "temperature": 0.7 if not prior_notes else 0.3,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    url = ENDPOINT.format(model=GEMINI_TEXT_MODEL, key=GEMINI_API_KEY)
    r = post_with_retry(url, json=body, timeout=120, label="plan")
    if r.status_code != 200:
        sys.stderr.write(f"Gemini error {r.status_code}:\n{r.text}\n")
        r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def generate(
    product_brief: dict,
    scraped: dict | None,
    clip_count: int,
    clip_duration_s: int,
    total_duration_s: int,
) -> dict:
    if clip_count < 1:
        raise ValueError(f"clip_count must be >= 1, got {clip_count}")
    if clip_duration_s < 1:
        raise ValueError(f"clip_duration_s must be >= 1, got {clip_duration_s}")
    if total_duration_s < 1:
        raise ValueError(f"total_duration_s must be >= 1, got {total_duration_s}")

    notes: list[str] = []
    last_error: Exception | None = None
    max_retries = 3
    for attempt in range(max_retries):
        text = _call_gemini(
            product_brief, scraped,
            clip_count=clip_count,
            clip_duration_s=clip_duration_s,
            total_duration_s=total_duration_s,
            prior_notes=notes,
        )
        try:
            payload = _extract_json(text)
            _validate(payload, clip_count=clip_count,
                      clip_duration_s=clip_duration_s,
                      total_duration_s=total_duration_s)
            return payload
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            note = str(e)[:300]
            notes.append(note)
            sys.stderr.write(f"[plan attempt {attempt+1}] {note} — retrying\n")
            debug_path = Path(f"/tmp/gemini_plan_raw_{attempt+1}.txt")
            debug_path.write_text(text)
    assert last_error is not None
    raise RuntimeError(f"Plan generation failed after {max_retries} attempts: {last_error}")


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    os.replace(tmp, path)


def main_cli() -> int:
    parser = argparse.ArgumentParser(description="Generate master video plan for a product video")
    parser.add_argument("--run-dir", type=Path, required=True,
                        help="Path to the run directory (contains product/brief.json)")
    parser.add_argument("--clip-count", type=int, required=True,
                        help="Number of clips in the video")
    parser.add_argument("--clip-duration-s", type=int, required=True,
                        help="Duration of each clip in seconds")
    parser.add_argument("--total-duration-s", type=int, required=True,
                        help="Total video duration in seconds")
    args = parser.parse_args()

    run_dir_path: Path = args.run_dir
    brief_path = run_dir_path / "product" / "brief.json"
    if not brief_path.exists():
        print(f"Missing product brief at {brief_path}", file=sys.stderr)
        return 2
    product_brief = json.loads(brief_path.read_text())

    scraped_path = run_dir_path / "product" / "scraped" / "page.json"
    scraped: dict | None = None
    if scraped_path.exists():
        try:
            scraped = json.loads(scraped_path.read_text())
        except json.JSONDecodeError as e:
            sys.stderr.write(f"Could not parse {scraped_path}: {e} — proceeding without scraped facts\n")
            scraped = None

    if args.clip_count * args.clip_duration_s != args.total_duration_s:
        sys.stderr.write(
            f"Warning: clip_count*clip_duration_s = {args.clip_count * args.clip_duration_s} "
            f"!= total_duration_s = {args.total_duration_s}\n"
        )

    print(
        f"Generating plan: {args.clip_count} clips x {args.clip_duration_s}s "
        f"(total {args.total_duration_s}s) for run {run_dir_path}",
        file=sys.stderr,
    )

    plan = generate(
        product_brief, scraped,
        clip_count=args.clip_count,
        clip_duration_s=args.clip_duration_s,
        total_duration_s=args.total_duration_s,
    )

    out = run_dir_path / "plan.json"
    _atomic_write(out, json.dumps(plan, ensure_ascii=False, indent=2))
    sys.stderr.write(f"Wrote {out}\n")
    print(">>> Plan ready")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
