"""Shared config: loads .env, exposes paths and model names."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def _load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())

_load_env()

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_TEXT_MODEL = os.environ.get("GEMINI_TEXT_MODEL", "gemini-3.5-flash")
GEMINI_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3-pro-image-preview")
YOUTUBE_TOKEN_PATH = Path(os.environ["YOUTUBE_TOKEN_PATH"])
YOUTUBE_CLIENT_SECRET = Path(os.environ["YOUTUBE_CLIENT_SECRET"])
GROK_PROFILE_DIR = Path(os.environ["GROK_PROFILE_DIR"])

OUTPUT_ROOT = ROOT / "output"
PROMPTS_DIR = ROOT / "prompts"


def run_dir(subject: str) -> Path:
    """Return per-run output directory, slugified from subject."""
    slug = "-".join(subject.lower().split())[:60]
    d = OUTPUT_ROOT / slug
    d.mkdir(parents=True, exist_ok=True)
    return d
