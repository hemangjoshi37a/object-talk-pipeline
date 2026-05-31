"""Auto-generate YouTube metadata from scripts + subject, then upload via cached OAuth token."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (
    GEMINI_API_KEY, GEMINI_TEXT_MODEL,
    YOUTUBE_TOKEN_PATH, YOUTUBE_CLIENT_SECRET,
)
from http_utils import post_with_retry

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube"]

METADATA_PROMPT = """Given the subject and the __N_CLIPS__ Object-Talk Hindi scripts below, produce YouTube metadata for a __TOTAL_S__-second Shorts upload that combines all __N_CLIPS__ clips.

ZERO-HALLUCINATION RULES (these override creative latitude):
- Use ONLY the __N_CLIPS__ object names exactly as they appear in the scripts below — do not rename, abbreviate, or invent variants
- Do NOT invent numeric statistics ("makes it 3x faster", "lasts 10 years") — use qualitative language only
- Do NOT invent brand names, product models, certifications, standards, or awards
- Do NOT make medical, financial, or safety claims that aren't generic common knowledge
- Tags must be real searchable terms in the domain, not fabricated phrases
- The description's bullet list MUST reflect the real function of each object as stated in the scripts — do not add facts not present in the scripts

Return strict JSON only:
{
  "title": "<≤95 chars, Hindi+English mix, must include all __N_CLIPS__ object names verbatim, attention-grabbing, no emojis, no fake stats>",
  "description": "<3-6 short paragraphs (Hindi+English): a hook, a bulleted list naming all __N_CLIPS__ objects with 1 line each describing ONLY their real function from the scripts, a Shorts hashtag block at the end. 800-1500 chars total. No invented numbers, no fake brand mentions, no emojis.>",
  "tags": ["<15-25 SEO tags, mix of English and Hindi/Hinglish, lowercase, no #, only real domain terms>"],
  "category_id": "<one of: 22 (People&Blogs), 27 (Education), 28 (Sci&Tech), 26 (How-to)>"
}

Subject: __SUBJECT__

Scripts:
__SCRIPTS_BLOCK__
"""


def generate_metadata(subject: str, scripts_payload: dict) -> dict:
    scripts_block = "\n\n".join(
        f"{i}. {s['object']}: {s['hindi_script']}"
        for i, s in enumerate(scripts_payload["scripts"], 1)
    )
    n_clips = len(scripts_payload.get("scripts", []))
    duration_s = int(scripts_payload.get("clip_duration_s") or 10)
    total_s = n_clips * duration_s
    # Avoid str.format here: the prompt contains literal { } JSON braces.
    prompt = (METADATA_PROMPT
              .replace("__SUBJECT__", subject)
              .replace("__SCRIPTS_BLOCK__", scripts_block)
              .replace("__N_CLIPS__", str(n_clips))
              .replace("__TOTAL_S__", str(total_s)))
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.6,
            "responseMimeType": "application/json",
            "maxOutputTokens": 4096,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    url = GEMINI_ENDPOINT.format(model=GEMINI_TEXT_MODEL, key=GEMINI_API_KEY)
    r = post_with_retry(url, json=body, timeout=120, label="metadata")
    r.raise_for_status()
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    # Gemini sometimes wraps JSON in ```json fences or appends prose after the
    # JSON object — a plain json.loads fails on "Extra data" then. Extract the
    # first balanced JSON object the way generate_scripts does.
    meta = _extract_json(text)
    # sanity
    if len(meta.get("title", "")) > 100:
        meta["title"] = meta["title"][:97] + "..."
    return meta


def _extract_json(text: str) -> dict:
    """Pull the first balanced JSON object out of a Gemini text response.

    Robust to:
      - ```json fences
      - leading/trailing prose
      - trailing extra content after the JSON object
    """
    import re
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in metadata response:\n{text[:500]}")
    depth = 0
    end = -1
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        raise ValueError(f"Unbalanced JSON object in metadata response:\n{text[start:start+500]}")
    return json.loads(text[start:end+1])


def get_credentials() -> Credentials:
    if not YOUTUBE_TOKEN_PATH.exists():
        from google_auth_oauthlib.flow import InstalledAppFlow
        if not YOUTUBE_CLIENT_SECRET.exists():
            raise RuntimeError(f"Missing OAuth client at {YOUTUBE_CLIENT_SECRET}")
        flow = InstalledAppFlow.from_client_secrets_file(str(YOUTUBE_CLIENT_SECRET), SCOPES)
        creds = flow.run_local_server(port=0)
        YOUTUBE_TOKEN_PATH.write_text(creds.to_json())
        YOUTUBE_TOKEN_PATH.chmod(0o600)
        return creds
    creds = Credentials.from_authorized_user_file(str(YOUTUBE_TOKEN_PATH), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        YOUTUBE_TOKEN_PATH.write_text(creds.to_json())
    return creds


def upload(video_path: Path, meta: dict, privacy: str = "public") -> str:
    from datetime import datetime, timezone

    creds = get_credentials()
    yt = build("youtube", "v3", credentials=creds)

    # Ahmedabad, Gujarat, India
    recording_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    body = {
        "snippet": {
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta["tags"],
            "categoryId": meta.get("category_id", "28"),
            "defaultLanguage": "hi",
            "defaultAudioLanguage": "hi",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            # "Altered content" disclosure (synthetic media). User asked for "No".
            "containsSyntheticMedia": False,
            "embeddable": True,
            "publicStatsViewable": True,
            "license": "youtube",
        },
        "recordingDetails": {
            "recordingDate": recording_date,
            "locationDescription": "Ahmedabad, Gujarat, India",
            "location": {"latitude": 23.0225, "longitude": 72.5714, "altitude": 53.0},
        },
    }
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(
        part="snippet,status,recordingDetails",
        body=body,
        media_body=media,
    )
    size_mb = video_path.stat().st_size / 1_048_576
    print(f"Uploading {video_path.name} ({size_mb:.1f} MB)...", file=sys.stderr)
    response = None
    try:
        while response is None:
            status, response = req.next_chunk()
            if status:
                print(f"  {int(status.progress()*100)}%", file=sys.stderr)
    except HttpError as e:
        sys.stderr.write(f"Upload failed: {e}\n")
        raise
    return response["id"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-generate metadata + upload to YouTube")
    parser.add_argument("video", type=Path, help="Path to merged video (mp4)")
    parser.add_argument("scripts_json", type=Path, help="Path to scripts.json")
    parser.add_argument("--privacy", default="public", choices=["public", "unlisted", "private"])
    parser.add_argument("--meta-out", type=Path, help="Save generated metadata JSON next to video")
    args = parser.parse_args()

    scripts_payload = json.loads(args.scripts_json.read_text())
    subject = scripts_payload["subject"]

    print(f"Generating metadata for: {subject}", file=sys.stderr)
    meta = generate_metadata(subject, scripts_payload)
    print(f"Title: {meta['title']}", file=sys.stderr)
    meta_out = args.meta_out or args.video.parent / "metadata.json"
    meta_out.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"Saved metadata to {meta_out}", file=sys.stderr)

    vid = upload(args.video, meta, privacy=args.privacy)
    print(f"\nUploaded: https://youtu.be/{vid}", file=sys.stderr)
    print(f"Studio:   https://studio.youtube.com/video/{vid}/edit", file=sys.stderr)
    print(vid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
