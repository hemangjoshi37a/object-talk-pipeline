"""Generate 5 Object-Talk scripts from a subject using Gemini text API."""
from __future__ import annotations

import argparse
import json
import os
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
# Defaults — overridable per-call via env (set by webapp/pipeline.py)
DEFAULT_COUNT = int(os.environ.get("PIPELINE_CLIP_COUNT", "5"))
DEFAULT_DURATION_S = int(os.environ.get("PIPELINE_CLIP_DURATION_S", "10"))
# Word budget: aim ~2.5 wps with a small safety margin below the 3 wps physics
# ceiling so Edge TTS rarely has to atempo-compress. NO post-hoc trimming —
# we keep retrying until Gemini lands inside the budget, then raise if it
# never complies. Trimming was removed because it cropped mid-sentence.
WORDS_PER_SECOND = 3
def _default_max_words(duration_s: int) -> int:
    return max(10, duration_s * WORDS_PER_SECOND - 5)
DEFAULT_MAX_WORDS = _default_max_words(DEFAULT_DURATION_S)
MAX_WORDS = DEFAULT_MAX_WORDS  # back-compat module-level read


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


def _validate(payload: dict, *, count: int = DEFAULT_COUNT, max_words: int = DEFAULT_MAX_WORDS) -> None:
    scripts = payload.get("scripts")
    if not isinstance(scripts, list) or len(scripts) != count:
        raise ValueError(f"Expected {count} scripts, got {len(scripts) if isinstance(scripts, list) else 'non-list'}")
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
        if actual_words > max_words:
            raise ValueError(
                f"Script #{i} ({s['object']}) is {actual_words} words — exceeds {max_words} cap"
            )
        s["word_count"] = actual_words  # trust our count, not the model's


def _call_gemini(
    subject: str,
    prior_attempts: list[str],
    *,
    extra_user_instruction: str | None = None,
    count: int = DEFAULT_COUNT,
    max_words: int = DEFAULT_MAX_WORDS,
    duration_s: int = DEFAULT_DURATION_S,
) -> str:
    system_prompt = SYSTEM_PROMPT_PATH.read_text()
    # Aim with a small safety margin under the hard cap — Gemini routinely
    # overshoots its declared limit, so we ask for max_words-3 as the target
    # and treat max_words as the absolute ceiling. No post-hoc trimming.
    target_high = max_words
    target_low = max(4, max_words - 5)
    user_text = (
        f"Subject: {subject}\n"
        f"Produce EXACTLY {count} scripts, NOT 5.\n"
        f"Each Hindi dialogue must fit in a {duration_s}-second spoken clip at a natural relaxed pace.\n"
        f"\n"
        f"=== ABSOLUTE WORD LIMIT ===\n"
        f"Every 'hindi_script' MUST be ≤ {target_high} whitespace-separated words. "
        f"Aim for {target_low}-{target_high}. Going OVER {target_high} = FAIL.\n"
        f"Before writing each script: plan the sentence, count the words on your fingers, "
        f"only THEN write. If the natural sentence is too long, rephrase shorter — never "
        f"truncate mid-thought (we will NOT trim it for you; we will reject and retry).\n"
        f"Each script MUST be a complete sentence (or two short ones) ending in a proper "
        f"terminator (। . ! ?). No dangling clauses.\n"
        f"=== END LIMIT ===\n"
        f"\n"
        f"Drop articles, fillers, and decorative adjectives to stay under cap. "
        f"Shorter and crisper is FAR better than longer and rushed.\n"
        f"Output JSON now."
    )
    if extra_user_instruction:
        user_text += f"\n\n{extra_user_instruction}"
    if prior_attempts:
        user_text += "\n\nPrevious attempts FAILED:\n"
        for note in prior_attempts:
            user_text += f"- {note}\n"
        user_text += (
            f"\nFIX: the previous attempt OVERSHOT {target_high} words. This time:\n"
            f"1. Drop one adjective per noun.\n"
            f"2. Combine two short clauses into one.\n"
            f"3. Cut anything that doesn't drive the punchline.\n"
            f"4. Re-count whitespace-separated words BEFORE finalizing.\n"
            f"You MUST land at ≤ {target_high} words per script with a complete final sentence. "
            f"The scripts array MUST have exactly {count} entries."
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
    count: int = DEFAULT_COUNT,
    duration_s: int = DEFAULT_DURATION_S,
    max_words: int | None = None,
) -> dict:
    if max_words is None:
        max_words = _default_max_words(duration_s)
    notes: list[str] = []
    last_error: Exception | None = None
    for attempt in range(max_retries):
        text = _call_gemini(subject, notes, extra_user_instruction=extra_user_instruction,
                            count=count, max_words=max_words, duration_s=duration_s)
        try:
            payload = _extract_json(text)
            _validate(payload, count=count, max_words=max_words)
            payload["subject"] = subject
            payload["clip_count"] = count
            payload["clip_duration_s"] = duration_s
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
    scripts = list(existing.get("scripts", []))
    count = len(scripts)
    if count < 1 or count > 20:
        raise ValueError(f"existing scripts.json has unexpected clip count: {count}")
    if not (1 <= idx_1based <= count):
        raise ValueError(f"idx must be 1..{count}")
    subject = existing.get("subject") or "(unknown)"
    duration_s = int(existing.get("clip_duration_s") or DEFAULT_DURATION_S)

    other_objects = [s.get("object", "") for i, s in enumerate(scripts) if i != idx_1based - 1]
    old_object = scripts[idx_1based - 1].get("object", "(unknown)")

    instr_parts = [
        f"REGENERATION REQUEST: Produce a brand-new replacement for script #{idx_1based} only.",
        f"The other {count-1} scripts in this run use these objects (DO NOT reuse): {', '.join(other_objects)}.",
        f"The previous object at slot #{idx_1based} was '{old_object}' — pick a CLEARLY DIFFERENT object/angle.",
        f"Still output ALL {count} scripts as required by the schema. Only slot #" + str(idx_1based)
        + f" will be kept — the others may be anything valid, but for clarity prefer reusing the same {count-1} objects above.",
    ]
    if hint:
        instr_parts.append(f"User hint for the new slot #{idx_1based}: {hint}")
    extra = "\n".join(instr_parts)

    fresh = generate(subject, extra_user_instruction=extra, count=count, duration_s=duration_s)
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
            raise RuntimeError(f"Gemini returned {count} scripts but all collide with kept objects")

    scripts[idx_1based - 1] = new_one
    existing["scripts"] = scripts
    existing["subject"] = subject
    return existing


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Object-Talk scripts")
    parser.add_argument("subject", help="The subject/domain (e.g. 'electric vehicles')")
    parser.add_argument("--out", type=Path, help="Optional output path (defaults to run_dir/scripts.json)")
    parser.add_argument("--only", type=int, help="Regenerate only script N; requires existing --out file")
    parser.add_argument("--hint", type=str, default=None, help="Optional hint for the regenerated slot")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT,
                        help=f"Number of clips to generate (1-20, default {DEFAULT_COUNT})")
    parser.add_argument("--duration-s", type=int, default=DEFAULT_DURATION_S,
                        help=f"Target spoken duration per clip in seconds (default {DEFAULT_DURATION_S})")
    parser.add_argument("--max-words", type=int, default=None,
                        help="Override max Hindi words per script. Default = duration_s*3-5.")
    args = parser.parse_args()

    if not 1 <= args.count <= 20:
        print(f"--count must be 1..20, got {args.count}", file=sys.stderr)
        return 2
    if not 5 <= args.duration_s <= 30:
        print(f"--duration-s must be 5..30, got {args.duration_s}", file=sys.stderr)
        return 2

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
        effective_max = args.max_words or _default_max_words(args.duration_s)
        print(f"Generating {args.count} scripts × {args.duration_s}s (≤{effective_max} words/script) "
              f"for: {args.subject}", file=sys.stderr)
        payload = generate(args.subject, count=args.count, duration_s=args.duration_s,
                           max_words=args.max_words)

    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"Wrote {out}", file=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
