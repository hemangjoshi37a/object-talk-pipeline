"""Append a footer to existing YouTube videos linking back to the repo + hjlabs.in.

Reuses the cached OAuth token at ~/.youtube-mcp/token.json. Fetches each video's
current snippet (title, description, tags, categoryId), appends the credit
block to the description, and writes it back via videos.update.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from steps.upload_video import get_credentials  # type: ignore
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

VIDEO_IDS = [
    "L_ANLwXPGcM",
    "8V-eQM8zLcQ",
    "Ns0Y0-VF56c",
    "WVTmfwQ8vDE",
    "SS92CmARvn8",
    "AbIxt_bP7FQ",
]

CREDIT_BLOCK = """

—
🛠 Built with the Object Talk Pipeline (open source): https://github.com/hemangjoshi37a/object-talk-pipeline
🌐 hjLabs: https://hjlabs.in"""

MARKER = "github.com/hemangjoshi37a/object-talk-pipeline"


def update_one(yt, video_id: str) -> str:
    resp = yt.videos().list(part="snippet", id=video_id).execute()
    items = resp.get("items", [])
    if not items:
        return f"  ✗ {video_id}: not found"
    snip = items[0]["snippet"]
    desc = snip.get("description", "")
    if MARKER in desc:
        return f"  ↺ {video_id}: already has credit, skipping"
    new_desc = desc.rstrip() + CREDIT_BLOCK
    body = {
        "id": video_id,
        "snippet": {
            "title": snip["title"],
            "description": new_desc,
            "tags": snip.get("tags", []),
            "categoryId": snip["categoryId"],
            "defaultLanguage": snip.get("defaultLanguage"),
            "defaultAudioLanguage": snip.get("defaultAudioLanguage"),
        },
    }
    # Drop None values
    body["snippet"] = {k: v for k, v in body["snippet"].items() if v is not None}
    yt.videos().update(part="snippet", body=body).execute()
    return f"  ✓ {video_id}: credit appended ({len(new_desc) - len(desc)} chars)"


def main() -> int:
    creds = get_credentials()
    yt = build("youtube", "v3", credentials=creds)
    print(f"Updating {len(VIDEO_IDS)} videos with credit footer...")
    for vid in VIDEO_IDS:
        try:
            print(update_one(yt, vid))
        except HttpError as e:
            print(f"  ✗ {vid}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
