"""One-shot capture of Grok's quota-exhausted UI.

Reuses the logged-in Cloak Browser profile, navigates to /imagine, performs
the minimum interaction needed to trigger any "out of generations" banner
(loads page, then attempts an image upload + submit with a tiny prompt), and
dumps:
  • Screenshot of the post-submit state
  • Full page HTML
  • Visible body text (so we can grep for the exact quota-error phrase)
  • Any toast/dialog/notification elements with their text + selector

All output lands in /tmp/grok_quota_capture/ — read it to design precise
detection in generate_videos.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import GROK_PROFILE_DIR

OUT = Path("/tmp/grok_quota_capture")
OUT.mkdir(parents=True, exist_ok=True)


def _dump(label: str, page) -> None:
    """Dump screenshot + HTML + visible text + suspected error elements."""
    pic = OUT / f"{label}.png"
    html = OUT / f"{label}.html"
    txt = OUT / f"{label}.txt"
    errs = OUT / f"{label}-errors.json"
    try:
        page.screenshot(path=str(pic), full_page=True)
    except Exception as e:
        print(f"  (screenshot failed: {e})")
    try:
        html.write_text(page.content())
    except Exception as e:
        print(f"  (html failed: {e})")
    try:
        body_text = page.evaluate("() => document.body.innerText || ''")
        txt.write_text(body_text or "")
    except Exception as e:
        body_text = ""
        print(f"  (text failed: {e})")
    # Hunt for toast/dialog/notification elements + anything containing limit-ish words
    try:
        elements = page.evaluate("""() => {
            const candidates = [];
            const all = document.querySelectorAll('*');
            const limitWords = /(limit|quota|exceed|out of|upgrade|subscribe|reached|run out)/i;
            for (const el of all) {
                const text = (el.innerText || '').trim();
                if (!text || text.length > 400) continue;
                if (limitWords.test(text) && el.children.length < 5) {
                    candidates.push({
                        tag: el.tagName.toLowerCase(),
                        cls: el.className?.toString().slice(0, 200),
                        id: el.id,
                        role: el.getAttribute('role'),
                        ariaLabel: el.getAttribute('aria-label'),
                        text: text.slice(0, 300),
                    });
                }
            }
            // Also: any [role=alert], [role=status], [role=dialog], .toast, .notification, [data-*toast*]
            const explicit = document.querySelectorAll('[role="alert"], [role="status"], [role="dialog"], .toast, [class*="toast"], [class*="notification"], [class*="banner"]');
            for (const el of explicit) {
                candidates.push({
                    tag: el.tagName.toLowerCase(),
                    cls: el.className?.toString().slice(0, 200),
                    role: el.getAttribute('role'),
                    text: (el.innerText || '').slice(0, 300),
                });
            }
            return candidates;
        }""")
        errs.write_text(json.dumps(elements, indent=2))
        if elements:
            print(f"  found {len(elements)} suspect element(s) → {errs}")
            for e in elements[:5]:
                print(f"    [{e.get('tag')}] role={e.get('role')!r} text={e.get('text','')[:120]!r}")
        else:
            print(f"  no limit-related elements found")
    except Exception as e:
        print(f"  (element scan failed: {e})")
    print(f"  saved → {pic.name} / {html.name} / {txt.name} / {errs.name}")


def main() -> int:
    import cloakbrowser

    ctx = cloakbrowser.launch_persistent_context(
        user_data_dir=str(GROK_PROFILE_DIR),
        headless=True,
        viewport={"width": 1280, "height": 900},
    )
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        print("→ navigating to grok.com/imagine ...")
        page.goto("https://grok.com/imagine", wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_selector(".ProseMirror", timeout=30000)
        except Exception:
            pass
        page.wait_for_timeout(4000)

        # Snapshot 1: landing page (might already show "out of generations" gate)
        print("\n=== A: landing page ===")
        _dump("a-landing", page)

        # Try switching to video mode (where the quota would apply)
        try:
            print("\n→ clicking Video mode toggle (if present) ...")
            video_btn = page.locator('button:has-text("Video"), [role="tab"]:has-text("Video")').first
            if video_btn.count():
                video_btn.click()
                page.wait_for_timeout(2000)
        except Exception as e:
            print(f"  (video toggle skipped: {e})")

        # Snapshot 2: video mode (may show a quota indicator)
        print("\n=== B: after Video-mode toggle ===")
        _dump("b-video-mode", page)

        # Type a tiny prompt and try to click Submit
        try:
            print("\n→ typing a tiny prompt and submitting ...")
            ed = page.locator(".ProseMirror.tiptap").first
            ed.click()
            page.wait_for_timeout(300)
            page.keyboard.type("test prompt for quota detection", delay=30)
            page.wait_for_timeout(500)
            sub = page.locator('button[aria-label="Submit"]').first
            if sub.count() and not sub.is_disabled(timeout=2000):
                sub.click(timeout=5000)
                page.wait_for_timeout(4000)
            else:
                print("  Submit button not enabled — likely no image attached. Trying anyway.")
        except Exception as e:
            print(f"  (submit attempt errored: {e})")

        # Snapshot 3: post-submit (where the quota toast would appear)
        print("\n=== C: after Submit attempt ===")
        _dump("c-post-submit", page)

    finally:
        try:
            ctx.close()
        except Exception:
            pass

    print(f"\nAll artifacts in: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
