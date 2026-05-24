"""Generate 5 Object-Talk scripts from a subject using Gemini text API."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (
    GEMINI_API_KEY, GEMINI_TEXT_MODEL, PROMPTS_DIR, run_dir,
)
from http_utils import post_with_retry

SYSTEM_PROMPT_PATH = PROMPTS_DIR / "object_talk_system.md"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
MAX_WORDS = 48


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a Gemini text response.

    Gemini sometimes wraps JSON in ```json fences despite instructions; strip them.
    """
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON object found in response:\n{text[:500]}")
    return json.loads(text[start:end+1])


def _validate(payload: dict) -> None:
    scripts = payload.get("scripts")
    if not isinstance(scripts, list) or len(scripts) != 5:
        raise ValueError(f"Expected 5 scripts, got {len(scripts) if isinstance(scripts, list) else 'non-list'}")
    seen_objects: set[str] = set()
    for i, s in enumerate(scripts, 1):
        for field in ("object", "image_prompt", "hindi_script", "action_script", "word_count"):
            if field not in s:
                # Backfill action_script with a minimal default if a legacy
                # (pre-action_script) payload comes through — keeps old runs
                # usable without forcing a regenerate.
                if field == "action_script":
                    s["action_script"] = ""
                    continue
                raise ValueError(f"Script #{i} missing field: {field}")
        obj = s["object"].lower().strip()
        if obj in seen_objects:
            raise ValueError(f"Duplicate object across scripts: {obj}")
        seen_objects.add(obj)
        actual_words = len(s["hindi_script"].split())
        if actual_words > MAX_WORDS:
            raise ValueError(
                f"Script #{i} ({s['object']}) is {actual_words} words — exceeds {MAX_WORDS} cap"
            )
        s["word_count"] = actual_words  # trust our count, not the model's


def _call_gemini(
    subject: str,
    prior_attempts: list[str],
    *,
    extra_user_instruction: str | None = None,
) -> str:
    system_prompt = SYSTEM_PROMPT_PATH.read_text()
    user_text = f"Subject: {subject}\n\nProduce the JSON now."
    if extra_user_instruction:
        user_text += f"\n\n{extra_user_instruction}"
    if prior_attempts:
        user_text += "\n\nPrevious attempts failed validation:\n"
        for note in prior_attempts:
            user_text += f"- {note}\n"
        user_text += (
            f"\nFix the issues. The HARD MAX is {MAX_WORDS} Hindi/English words per script. "
            f"Aim for 30-38 to leave headroom. Count whitespace-separated tokens as you write, "
            f"and drop low-information words (articles, mild adjectives) to stay under cap."
        )

    # Google Search grounding: factually anchors object selection + claims by
    # letting the model consult real web sources before answering. Massively
    # reduces hallucination of brand names, fake specs, invented standards.
    #
    # Gemini API constraint: tools (incl. googleSearch) cannot be combined
    # with responseMimeType=application/json — so we ask for JSON in the
    # prompt and rely on _extract_json() to strip any markdown fences.
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "tools": [{"googleSearch": {}}],
        "generationConfig": {
            "temperature": 0.85 if not prior_attempts else 0.4,
            "maxOutputTokens": 8192,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    url = ENDPOINT.format(model=GEMINI_TEXT_MODEL, key=GEMINI_API_KEY)
    r = post_with_retry(url, json=body, timeout=120, label="scripts")
    if r.status_code != 200:
        sys.stderr.write(f"Gemini error {r.status_code}:\n{r.text}\n")
        r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def generate(
    subject: str,
    max_retries: int = 5,
    *,
    extra_user_instruction: str | None = None,
) -> dict:
    notes: list[str] = []
    last_error: Exception | None = None
    for attempt in range(max_retries):
        text = _call_gemini(subject, notes, extra_user_instruction=extra_user_instruction)
        try:
            payload = _extract_json(text)
            _validate(payload)
            payload["subject"] = subject
            return payload
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            note = str(e)[:300]
            notes.append(note)
            sys.stderr.write(f"[attempt {attempt+1}] {note} — retrying\n")
            debug_path = Path(f"/tmp/gemini_scripts_raw_{attempt+1}.txt")
            debug_path.write_text(text)
    assert last_error is not None
    raise RuntimeError(f"Script generation failed after {max_retries} attempts: {last_error}")


def regenerate_one(
    existing: dict,
    idx_1based: int,
    *,
    hint: str | None = None,
) -> dict:
    """Replace scripts[idx-1] in `existing` with a fresh Gemini-generated one.

    Strategy: generate a full set of 5 (the model is trained to do that) and
    take the slot at `idx_1based`. We pass other slot `object` names so the
    model knows to pick a non-overlapping new object for the regen target.
    """
    if not (1 <= idx_1based <= 5):
        raise ValueError("idx must be 1..5")
    scripts = list(existing.get("scripts", []))
    if len(scripts) != 5:
        raise ValueError(f"existing scripts.json must have 5 scripts, found {len(scripts)}")
    subject = existing.get("subject") or "(unknown)"

    other_objects = [s.get("object", "") for i, s in enumerate(scripts) if i != idx_1based - 1]
    old_object = scripts[idx_1based - 1].get("object", "(unknown)")

    instr_parts = [
        f"REGENERATION REQUEST: Produce a brand-new replacement for script #{idx_1based} only.",
        f"The other 4 scripts in this run use these objects (DO NOT reuse): {', '.join(other_objects)}.",
        f"The previous object at slot #{idx_1based} was '{old_object}' — pick a CLEARLY DIFFERENT object/angle.",
        "Still output ALL 5 scripts as required by the schema. Only slot #" + str(idx_1based)
        + " will be kept — the others may be anything valid, but for clarity prefer reusing the same 4 objects above.",
    ]
    if hint:
        instr_parts.append(f"User hint for the new slot #{idx_1based}: {hint}")
    extra = "\n".join(instr_parts)

    fresh = generate(subject, extra_user_instruction=extra)
    new_one = fresh["scripts"][idx_1based - 1]

    # Validate the single replacement doesn't collide with the kept 4.
    kept_lower = {o.lower().strip() for o in other_objects}
    if new_one.get("object", "").lower().strip() in kept_lower:
        # Bump the index toward a non-colliding slot from the fresh batch
        for cand in fresh["scripts"]:
            if cand.get("object", "").lower().strip() not in kept_lower:
                new_one = cand
                break
        else:
            raise RuntimeError("Gemini returned 5 scripts but all collide with kept objects")

    scripts[idx_1based - 1] = new_one
    existing["scripts"] = scripts
    existing["subject"] = subject
    return existing


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate 5 Object-Talk scripts")
    parser.add_argument("subject", help="The subject/domain (e.g. 'electric vehicles')")
    parser.add_argument("--out", type=Path, help="Optional output path (defaults to run_dir/scripts.json)")
    parser.add_argument("--only", type=int, help="Regenerate only script N (1..5); requires existing --out file")
    parser.add_argument("--hint", type=str, default=None, help="Optional hint for the regenerated slot")
    args = parser.parse_args()

    out = args.out or (run_dir(args.subject) / "scripts.json")

    if args.only:
        if not out.exists():
            print(f"--only requires existing scripts.json at {out}", file=sys.stderr)
            return 2
        print(f"Regenerating script #{args.only} for: {args.subject}"
              + (f" (hint: {args.hint})" if args.hint else ""), file=sys.stderr)
        existing = json.loads(out.read_text())
        payload = regenerate_one(existing, args.only, hint=args.hint)
    else:
        print(f"Generating scripts for: {args.subject}", file=sys.stderr)
        payload = generate(args.subject)

    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"Wrote {out}", file=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
