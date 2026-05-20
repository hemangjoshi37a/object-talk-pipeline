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

SYSTEM_PROMPT_PATH = PROMPTS_DIR / "object_talk_system.md"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
MAX_WORDS = 45


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
        for field in ("object", "image_prompt", "hindi_script", "word_count"):
            if field not in s:
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


def _call_gemini(subject: str, prior_attempts: list[str]) -> str:
    system_prompt = SYSTEM_PROMPT_PATH.read_text()
    user_text = f"Subject: {subject}\n\nProduce the JSON now."
    if prior_attempts:
        user_text += "\n\nPrevious attempts failed validation:\n"
        for note in prior_attempts:
            user_text += f"- {note}\n"
        user_text += (
            f"\nFix the issues. The HARD MAX is {MAX_WORDS} Hindi/English words per script. "
            f"Aim for 30-38 to leave headroom. Count whitespace-separated tokens as you write, "
            f"and drop low-information words (articles, mild adjectives) to stay under cap."
        )

    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {
            "temperature": 0.85 if not prior_attempts else 0.4,
            "responseMimeType": "application/json",
            "maxOutputTokens": 8192,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    url = ENDPOINT.format(model=GEMINI_TEXT_MODEL, key=GEMINI_API_KEY)
    r = requests.post(url, json=body, timeout=120)
    if r.status_code != 200:
        sys.stderr.write(f"Gemini error {r.status_code}:\n{r.text}\n")
        r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def generate(subject: str, max_retries: int = 5) -> dict:
    notes: list[str] = []
    last_error: Exception | None = None
    for attempt in range(max_retries):
        text = _call_gemini(subject, notes)
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate 5 Object-Talk scripts")
    parser.add_argument("subject", help="The subject/domain (e.g. 'electric vehicles')")
    parser.add_argument("--out", type=Path, help="Optional output path (defaults to run_dir/scripts.json)")
    args = parser.parse_args()

    out = args.out or (run_dir(args.subject) / "scripts.json")
    print(f"Generating scripts for: {args.subject}", file=sys.stderr)
    payload = generate(args.subject)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"Wrote {out}", file=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
