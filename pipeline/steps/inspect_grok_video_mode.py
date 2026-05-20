"""Inspect grok.com/imagine AFTER switching to Video mode, to find resolution
and duration controls. Saves dump to /tmp/grok_video_mode/."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import GROK_PROFILE_DIR
from steps.generate_videos import _dismiss_consent_banner, _switch_to_video_mode

OUT = Path("/tmp/grok_video_mode")
OUT.mkdir(parents=True, exist_ok=True)


def main() -> int:
    import cloakbrowser

    ctx = cloakbrowser.launch_persistent_context(
        user_data_dir=str(GROK_PROFILE_DIR),
        headless=False,  # easier to see what's happening
        viewport={"width": 1280, "height": 900},
    )
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://grok.com/imagine", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector(".ProseMirror.tiptap", timeout=30000)
        page.wait_for_timeout(1500)
        _dismiss_consent_banner(page)

        print("Switching to Video mode...")
        _switch_to_video_mode(page)
        page.wait_for_timeout(1500)

        page.screenshot(path=str(OUT / "video_mode.png"), full_page=True)

        # Cast a wide net: every button + every aria-labelled element + every visible text node near the toolbar
        elements = page.evaluate("""() => {
            const r = {buttons: [], aria_labelled: [], all_text_chips: []};
            document.querySelectorAll('button').forEach(e => {
                r.buttons.push({
                    text: (e.innerText || '').trim().slice(0, 60),
                    aria_label: e.getAttribute('aria-label'),
                    class: e.className.toString().slice(0, 100),
                    disabled: e.disabled,
                    role: e.getAttribute('role'),
                    parent_aria: e.parentElement?.getAttribute('aria-label'),
                });
            });
            document.querySelectorAll('[aria-label]').forEach(e => {
                r.aria_labelled.push({
                    tag: e.tagName, aria_label: e.getAttribute('aria-label'),
                    role: e.getAttribute('role'),
                    text: (e.innerText || '').trim().slice(0, 40),
                });
            });
            // Hunt for any element whose text looks like a resolution or duration
            const patterns = ['720', '1080', '480', '4s', '6s', '8s', '10s', '12s', 'sec', 'Quality', 'Speed', 'p)'];
            document.querySelectorAll('button, div, span').forEach(e => {
                const t = (e.innerText || '').trim();
                if (t && t.length < 30 && patterns.some(p => t.includes(p))) {
                    r.all_text_chips.push({
                        tag: e.tagName, text: t,
                        class: e.className.toString().slice(0, 80),
                        role: e.getAttribute('role'),
                        aria_label: e.getAttribute('aria-label'),
                    });
                }
            });
            return r;
        }""")
        (OUT / "elements.json").write_text(json.dumps(elements, indent=2))
        print(f"Saved {OUT}/video_mode.png and elements.json")
        print()
        print("=== Text chips found (potential resolution/duration controls): ===")
        for c in elements.get("all_text_chips", []):
            print(f"  {c['tag']} text={c['text']!r} role={c.get('role')} class={c.get('class')[:60]}")

        print("\nPress Enter to close browser...")
        input()
    finally:
        ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
