"""One-time interactive Grok login helper.

Run this ONCE. It launches a headed Chromium pointed at https://grok.com/imagine
with a persistent user-data dir. Log in normally inside the browser, then come
back to the terminal and press Enter to save the session and exit.

Subsequent automation runs reuse the same user-data dir and skip login until
the cookies expire (~weeks).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import GROK_PROFILE_DIR

GROK_URL = "https://grok.com/imagine"


def main() -> int:
    import cloakbrowser

    GROK_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Profile dir: {GROK_PROFILE_DIR}")
    print("Launching CloakBrowser (stealth Chromium)...")

    ctx = cloakbrowser.launch_persistent_context(
        user_data_dir=str(GROK_PROFILE_DIR),
        headless=False,
        viewport={"width": 1280, "height": 500},
        args=["--window-size=1280,500", "--window-position=0,0"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(GROK_URL, wait_until="domcontentloaded")

    print()
    print("=" * 60)
    print(" Log into Grok in the browser window that just opened.")
    print(" When you can see grok.com/imagine working normally,")
    print(" come back here and press Enter to save the session.")
    print("=" * 60)
    input(" [Enter when logged in] > ")

    try:
        current = page.url
        title = page.title()
        print(f"Current URL: {current}")
        print(f"Page title:  {title}")
    except Exception as e:
        print(f"(could not read page state: {e})")

    ctx.close()
    print(f"Session saved to {GROK_PROFILE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
