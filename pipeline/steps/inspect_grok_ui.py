"""One-shot DOM inspection of grok.com/imagine using the logged-in persistent profile.

Saves a screenshot + JSON dump of interactive elements to /tmp/grok_inspect/
so we can pick correct selectors for generate_videos.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import GROK_PROFILE_DIR

OUT = Path("/tmp/grok_inspect")
OUT.mkdir(parents=True, exist_ok=True)


def main() -> int:
    import cloakbrowser

    ctx = cloakbrowser.launch_persistent_context(
        user_data_dir=str(GROK_PROFILE_DIR),
        headless=True,
        viewport={"width": 1280, "height": 900},
    )
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://grok.com/imagine", wait_until="domcontentloaded", timeout=60000)
        # Wait for ProseMirror editor to mount (the prompt input)
        try:
            page.wait_for_selector(".ProseMirror", timeout=30000)
            print("ProseMirror mounted.")
        except Exception as e:
            print(f"(warn) ProseMirror wait failed: {e}")
        page.wait_for_timeout(3000)

        # Screenshot
        page.screenshot(path=str(OUT / "screenshot.png"), full_page=True)

        # Page metadata
        meta = {
            "url": page.url,
            "title": page.title(),
        }
        (OUT / "meta.json").write_text(json.dumps(meta, indent=2))

        # Full HTML
        (OUT / "page.html").write_text(page.content())

        # Interesting element inventory via JS
        elements = page.evaluate("""() => {
            const results = {
                file_inputs: [],
                textareas: [],
                inputs: [],
                contenteditables: [],
                buttons: [],
                videos: [],
                images: [],
                aria_labelled: []
            };
            document.querySelectorAll('input[type="file"]').forEach(e => {
                results.file_inputs.push({
                    accept: e.accept,
                    multiple: e.multiple,
                    id: e.id, name: e.name, class: e.className,
                    hidden: e.hidden || e.style.display === 'none',
                    parent_tag: e.parentElement?.tagName,
                    parent_class: e.parentElement?.className,
                });
            });
            document.querySelectorAll('textarea').forEach(e => {
                results.textareas.push({
                    id: e.id, name: e.name, class: e.className,
                    placeholder: e.placeholder, aria_label: e.getAttribute('aria-label'),
                });
            });
            document.querySelectorAll('input').forEach(e => {
                if (e.type !== 'file' && e.type !== 'hidden') {
                    results.inputs.push({
                        type: e.type, id: e.id, name: e.name, class: e.className,
                        placeholder: e.placeholder, aria_label: e.getAttribute('aria-label'),
                    });
                }
            });
            document.querySelectorAll('[contenteditable="true"], .ProseMirror').forEach(e => {
                results.contenteditables.push({
                    tag: e.tagName, class: e.className.toString().slice(0, 120), id: e.id,
                    aria_label: e.getAttribute('aria-label'),
                    placeholder: e.getAttribute('data-placeholder') || e.getAttribute('aria-placeholder'),
                    role: e.getAttribute('role'),
                    parent_class: e.parentElement?.className?.toString()?.slice(0, 120),
                });
            });
            document.querySelectorAll('button').forEach(e => {
                const text = (e.innerText || e.textContent || '').trim().slice(0, 80);
                results.buttons.push({
                    text: text,
                    aria_label: e.getAttribute('aria-label'),
                    id: e.id, class: e.className.toString().slice(0, 100),
                    disabled: e.disabled,
                    type: e.type,
                });
            });
            document.querySelectorAll('video').forEach(e => {
                results.videos.push({
                    src: e.src, currentSrc: e.currentSrc,
                    id: e.id, class: e.className,
                });
            });
            document.querySelectorAll('[aria-label]').forEach(e => {
                if (e.tagName !== 'BUTTON' && e.tagName !== 'INPUT' && e.tagName !== 'TEXTAREA') {
                    results.aria_labelled.push({
                        tag: e.tagName, aria_label: e.getAttribute('aria-label'),
                        role: e.getAttribute('role'),
                    });
                }
            });
            return results;
        }""")
        (OUT / "elements.json").write_text(json.dumps(elements, indent=2))
    finally:
        ctx.close()

    print(f"Inspection saved to {OUT}/")
    print("Files:")
    for f in sorted(OUT.iterdir()):
        print(f"  {f.name}  ({f.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
