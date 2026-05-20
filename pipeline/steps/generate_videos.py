"""Drive grok.com/imagine via CloakBrowser to turn 5 (image, script) pairs into 5 MP4s.

For each pair:
  1. Navigate to grok.com/imagine (or reset session via "New Chat")
  2. Switch to Video mode
  3. Set aspect ratio to 9:16
  4. Upload the image
  5. Type the Hindi script into the ProseMirror editor
  6. Click Submit
  7. Wait for the resulting <video> to appear
  8. Download the MP4 via HTTP
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
import errno
import fcntl
import os
import random

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import GROK_PROFILE_DIR

GROK_URL = "https://grok.com/imagine"
GEN_TIMEOUT_S = 300  # 5 min per video generation
GROK_LOCK_WAIT_S = 1800  # wait up to 30 min for browser profile to free up


def _acquire_grok_lock(timeout_s: int = GROK_LOCK_WAIT_S):
    """File-lock on the Grok profile so only one Chrome process uses it at a time.

    Chrome locks the user_data_dir with a SingletonLock and *fails* (not waits)
    if it's taken. We add a polite app-level wait so concurrent pipeline runs
    queue cleanly instead of failing the second one.
    """
    GROK_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = GROK_PROFILE_DIR.parent / "grok.lock"
    f = open(lock_path, "w")
    deadline = time.time() + timeout_s
    waited = False
    while True:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            f.write(f"{os.getpid()}\n")
            f.flush()
            if waited:
                print("  (lock acquired)", flush=True)
            return f
        except (BlockingIOError, OSError) as e:
            if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise
            if not waited:
                print(f"  (Grok browser busy with another run — waiting up to {timeout_s}s for lock)", flush=True)
                waited = True
            if time.time() > deadline:
                f.close()
                raise TimeoutError(
                    f"Grok browser still locked by another process after {timeout_s}s"
                )
            time.sleep(3)


def _stale_singleton_cleanup():
    """If the lock file is free (no Python holder) but Chrome's SingletonLock
    file from a prior crashed run is still there, remove it. We only do this
    AFTER acquiring our own lock, so no race with another active process.
    """
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = GROK_PROFILE_DIR / name
        if p.exists() or p.is_symlink():
            try:
                p.unlink()
            except Exception:
                pass


def _slug(name: str) -> str:
    return "-".join(name.lower().split())[:30]


def _switch_to_video_mode(page) -> None:
    """Click the Video tab in the 'Generation mode' radiogroup if not already active."""
    group = page.get_by_label("Generation mode")
    btn = group.locator("button", has_text="Video").first
    btn.click(timeout=10000)
    page.wait_for_timeout(800)


def _pick_radio(page, group_label: str, option_text: str) -> None:
    """Click a radio option (by text) inside the named radiogroup."""
    group = page.get_by_label(group_label)
    btn = group.locator("button", has_text=option_text).first
    btn.click(timeout=10000)
    page.wait_for_timeout(400)


def _set_resolution(page, value: str = "720p") -> None:
    _pick_radio(page, "Video resolution", value)


def _set_duration(page, value: str = "10s") -> None:
    _pick_radio(page, "Video duration", value)


def _set_aspect_9_16(page) -> None:
    """Open aspect ratio dropdown and pick 9:16."""
    ar = page.locator('button[aria-label="Aspect Ratio"]')
    if not ar.count():
        print("  (no aspect ratio button found, skipping)")
        return
    current = ar.first.inner_text().strip()
    if "9:16" in current:
        return
    ar.first.click()
    page.wait_for_timeout(500)
    # Try several ways to find the 9:16 option
    candidates = [
        page.get_by_role("menuitem", name="9:16"),
        page.get_by_role("option", name="9:16"),
        page.locator('[role="menuitem"]:has-text("9:16")'),
        page.locator('text="9:16"').first,
    ]
    for c in candidates:
        try:
            if c.count() > 0:
                c.first.click(timeout=2000)
                page.wait_for_timeout(400)
                return
        except Exception:
            continue
    print("  (warn) could not find 9:16 option — closing menu")
    page.keyboard.press("Escape")


def _upload_image(page, image_path: Path) -> None:
    """Set the hidden file input to upload the image."""
    file_inputs = page.locator('input[type="file"][name="files"]')
    if not file_inputs.count():
        file_inputs = page.locator('input[type="file"]')
    file_inputs.first.set_input_files(str(image_path))
    page.wait_for_timeout(1500)  # let preview render


def _human_jitter(page, near_x: int | None = None, near_y: int | None = None) -> None:
    """Move mouse with a couple small random offsets — anti-bot hygiene."""
    try:
        vp = page.viewport_size or {"width": 1280, "height": 500}
        x = near_x if near_x is not None else random.randint(int(vp["width"] * 0.3), int(vp["width"] * 0.7))
        y = near_y if near_y is not None else random.randint(int(vp["height"] * 0.3), int(vp["height"] * 0.7))
        # 2-3 intermediate points to look like a real motion path
        steps = random.randint(2, 4)
        for _ in range(steps):
            dx = random.randint(-25, 25)
            dy = random.randint(-25, 25)
            page.mouse.move(max(0, x + dx), max(0, y + dy), steps=random.randint(4, 10))
            page.wait_for_timeout(random.randint(20, 90))
        page.mouse.move(x, y, steps=random.randint(4, 8))
    except Exception:
        pass  # mouse hygiene is best-effort, never block the flow


def _set_prompt(page, text: str) -> None:
    """Click into the ProseMirror editor and type the prompt humanlike (variable delays)."""
    editor = page.locator(".ProseMirror.tiptap").first
    _human_jitter(page)
    editor.click()
    page.wait_for_timeout(random.randint(250, 550))
    for ch in text:
        # Most chars 30-90ms (≈12-30 wpm range); occasional brief think-pause
        page.keyboard.type(ch, delay=random.randint(25, 90))
        r = random.random()
        if r < 0.03:
            page.wait_for_timeout(random.randint(200, 550))  # think pause
        elif r < 0.10 and ch in " ,।":  # natural pause after punctuation/space
            page.wait_for_timeout(random.randint(80, 220))
    page.wait_for_timeout(random.randint(350, 700))


def _submit(page) -> None:
    """Wait for Submit to enable, then click it."""
    sub = page.locator('button[aria-label="Submit"]').first
    deadline = time.time() + 20
    while time.time() < deadline:
        if not sub.is_disabled():
            sub.click()
            return
        page.wait_for_timeout(400)
    raise RuntimeError("Submit button never became enabled within 20s")


def _dump_post_gen_state(page, label: str) -> None:
    """Capture the post-submit page (DOM + screenshot) to /tmp/grok_post_gen/<label>/.
    Used to discover the download button's selector and other post-gen UI."""
    out = Path("/tmp/grok_post_gen") / label
    out.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(out / "screenshot.png"), full_page=True)
        (out / "meta.json").write_text(json.dumps({"url": page.url, "title": page.title()}, indent=2))
        elements = page.evaluate("""() => {
            const r = {buttons: [], videos: [], aria_labelled: [], links: []};
            document.querySelectorAll('button').forEach(e => {
                r.buttons.push({
                    text: (e.innerText || '').trim().slice(0, 60),
                    aria_label: e.getAttribute('aria-label'),
                    class: e.className.toString().slice(0, 100),
                    disabled: e.disabled,
                });
            });
            document.querySelectorAll('video').forEach(e => {
                r.videos.push({
                    src: e.src || e.currentSrc, ready: e.readyState,
                    width: e.videoWidth, height: e.videoHeight, duration: e.duration,
                });
            });
            document.querySelectorAll('a[href]').forEach(e => {
                const t = (e.innerText || '').trim().slice(0, 40);
                if (t && t.length < 30) r.links.push({text: t, href: e.href.slice(0, 120)});
            });
            document.querySelectorAll('[aria-label]').forEach(e => {
                r.aria_labelled.push({tag: e.tagName, aria_label: e.getAttribute('aria-label'), role: e.getAttribute('role')});
            });
            return r;
        }""")
        (out / "elements.json").write_text(json.dumps(elements, indent=2))
        print(f"    [debug] dumped post-gen state to {out}", flush=True)
    except Exception as e:
        print(f"    [debug] dump failed: {e}", flush=True)


def _poll_url_ready(page, url: str, timeout_s: int) -> None:
    """Block until the video URL actually returns 200 (file finished encoding)."""
    deadline = time.time() + timeout_s
    last_status = None
    while time.time() < deadline:
        status = page.evaluate(f"""async () => {{
            try {{
                const r = await fetch({json.dumps(url)}, {{ method: 'GET', headers: {{Range: 'bytes=0-1'}}, credentials: 'include' }});
                return r.status;
            }} catch (e) {{ return 0; }}
        }}""")
        if status != last_status:
            print(f"    url status: {status}", flush=True)
            last_status = status
        if status == 200 or status == 206:
            return
        page.wait_for_timeout(5000)
    raise TimeoutError(f"URL not ready within {timeout_s}s: {url}")


def _wait_for_video_url(page, timeout_s: int = GEN_TIMEOUT_S) -> str:
    """Find the tall generated <video>'s src, then block until that URL is downloadable."""
    deadline = time.time() + timeout_s
    last_count = -1
    found_url: str | None = None
    while time.time() < deadline:
        urls = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('video')).map(v => ({
                src: v.src || v.currentSrc,
                ready: v.readyState,
                paused: v.paused,
                duration: v.duration,
                width: v.videoWidth,
                height: v.videoHeight,
            }));
        }""")
        candidates = [
            u for u in urls
            if u["src"] and "tooltip" not in u["src"] and "nux" not in u["src"]
            and (u["src"].startswith("http") or u["src"].startswith("blob:"))
        ]
        if len(candidates) != last_count:
            print(f"  videos in DOM: {len(candidates)}", flush=True)
            last_count = len(candidates)
        # Prefer a tall one
        tall = [u for u in candidates if u["height"] and u["width"] and u["height"] > u["width"]]
        pool = tall or candidates
        if pool:
            found_url = pool[0]["src"]
            break
        page.wait_for_timeout(2500)
    if not found_url:
        raise TimeoutError(f"No generated <video> element appeared within {timeout_s}s")
    print(f"  found URL: {found_url[:80]}...", flush=True)
    remaining = max(60, int(deadline - time.time()))
    print(f"  polling URL until ready (up to {remaining}s)...", flush=True)
    _poll_url_ready(page, found_url, remaining)
    return found_url


def _in_page_fetch(page, url: str) -> bytes:
    """Fetch a URL from inside the page so it carries cookies + page Referer."""
    import base64
    b64 = page.evaluate(f"""async () => {{
        const r = await fetch({json.dumps(url)}, {{ credentials: 'include' }});
        if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + r.statusText);
        const b = await r.blob();
        const ab = await b.arrayBuffer();
        const bytes = new Uint8Array(ab);
        // chunked base64 to avoid huge string concat
        const chunk = 32768;
        let s = '';
        for (let i = 0; i < bytes.length; i += chunk) {{
            s += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
        }}
        return btoa(s);
    }}""")
    return base64.b64decode(b64)


def _download(url: str, out: Path, page=None) -> None:
    """Download MP4 using in-page fetch (carries the browser's full request context)."""
    if page is not None:
        # Try strategies in order: in-page fetch > Playwright request context > raw requests
        try:
            out.write_bytes(_in_page_fetch(page, url))
            return
        except Exception as e:
            print(f"  (in-page fetch failed: {e}; trying Playwright request context)")
        try:
            resp = page.context.request.get(url, timeout=120000)
            if resp.ok:
                out.write_bytes(resp.body())
                return
            print(f"  (Playwright request context: {resp.status} {resp.status_text})")
        except Exception as e:
            print(f"  (Playwright request context failed: {e})")
    r = requests.get(url, timeout=120, stream=True)
    r.raise_for_status()
    with out.open("wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 64):
            f.write(chunk)


def _dismiss_consent_banner(page) -> None:
    """Click OneTrust 'Accept All' if shown, otherwise rip the banner out of the DOM."""
    try:
        btn = page.locator("#onetrust-accept-btn-handler").first
        if btn.count() and btn.is_visible():
            btn.click(timeout=2000)
            page.wait_for_timeout(500)
            return
    except Exception:
        pass
    # Fallback: nuke the banner so it stops intercepting clicks
    page.evaluate("""() => {
        for (const id of ['onetrust-consent-sdk', 'onetrust-banner-sdk', 'ot-sdk-container']) {
            const el = document.getElementById(id);
            if (el) el.remove();
        }
    }""")


def _ensure_fresh_chat(page) -> None:
    """Navigate to a clean imagine page (clears previous attachment / prompt)."""
    page.goto(GROK_URL, wait_until="domcontentloaded")
    page.wait_for_selector(".ProseMirror.tiptap", timeout=30000)
    page.wait_for_timeout(1500)
    _dismiss_consent_banner(page)


def _setup_and_submit(page, image_path: Path, prompt: str) -> None:
    """The setup phase: reset, configure, upload, prompt, submit. Returns when submit is clicked."""
    _ensure_fresh_chat(page)
    _switch_to_video_mode(page)
    _set_aspect_9_16(page)
    _set_resolution(page, "720p")
    _set_duration(page, "10s")
    _upload_image(page, image_path)
    _set_prompt(page, prompt)
    _submit(page)
    page.wait_for_timeout(3000)  # allow navigation to start


def generate_one(page, image_path: Path, prompt: str, out_path: Path) -> None:
    print(f"  · setup + submit")
    _setup_and_submit(page, image_path, prompt)
    print(f"  · waiting for navigation to settle...")
    page.wait_for_timeout(5000)
    _dump_post_gen_state(page, f"after_submit_{int(time.time())}")
    print(f"  · waiting for video generation (up to {GEN_TIMEOUT_S}s)...")
    url = _wait_for_video_url(page)
    _dump_post_gen_state(page, f"after_ready_{int(time.time())}")
    print(f"  · downloading from {url[:80]}...")
    _download(url, out_path, page=page)
    print(f"  · saved {out_path.name} ({out_path.stat().st_size // 1024} KB)")


def _collect_pending(scripts, out_dir: Path, only):
    """Return [(idx, script, image_path, out_path), ...] for the items that need work."""
    pending = []
    for i, s in enumerate(scripts, 1):
        if only and i not in only:
            continue
        obj_slug = _slug(s["object"])
        out = out_dir / f"vid_{i:02d}_{obj_slug}.mp4"
        if out.exists() and out.stat().st_size > 1024:
            print(f"[{i}/5] {s['object']}: skip (already exists)", flush=True)
            continue
        img_candidates = list(out_dir.glob(f"img_{i:02d}_*"))
        if not img_candidates:
            raise FileNotFoundError(f"No image for script #{i} ({s['object']}) in {out_dir}")
        pending.append((i, s, img_candidates[0], out))
    return pending


def _generate_parallel(ctx, pending) -> list[Path]:
    """Setup-then-wait pattern: open one tab per pending item, submit all serially,
    then poll all tabs in parallel for video URLs and download as they complete."""
    if not pending:
        return []
    print(f"(parallel: opening {len(pending)} tabs)", flush=True)
    pages = [ctx.new_page() for _ in pending]
    # Setup phase — serial per tab, but each tab's "waiting" phase begins as soon as we hit submit
    for slot, (idx, s, img, out) in enumerate(pending):
        print(f"[{idx}/5] {s['object']}: setup tab {slot+1}", flush=True)
        _setup_and_submit(pages[slot], img, s["hindi_script"])
    # All tabs have submitted. Wait + download whichever finishes first.
    print(f"  all {len(pending)} submitted; polling for completion...", flush=True)
    outputs: list[Path] = [None] * len(pending)  # type: ignore
    finished = [False] * len(pending)
    deadline = time.time() + GEN_TIMEOUT_S
    while not all(finished) and time.time() < deadline:
        for slot, (idx, s, img, out) in enumerate(pending):
            if finished[slot]:
                continue
            page = pages[slot]
            try:
                urls = page.evaluate("""() => Array.from(document.querySelectorAll('video')).map(v => ({
                    src: v.src || v.currentSrc, width: v.videoWidth, height: v.videoHeight,
                }))""")
            except Exception:
                continue
            candidates = [u for u in urls if u["src"] and "tooltip" not in u["src"] and "nux" not in u["src"]
                          and (u["src"].startswith("http") or u["src"].startswith("blob:"))]
            tall = [u for u in candidates if u["height"] and u["width"] and u["height"] > u["width"]]
            pool = tall or candidates
            if not pool:
                continue
            url = pool[0]["src"]
            # Quick ready-check via HEAD-like fetch
            try:
                status = page.evaluate(f"""async () => {{
                    try {{ const r = await fetch({json.dumps(url)}, {{ headers: {{Range: 'bytes=0-1'}}, credentials: 'include' }}); return r.status; }}
                    catch {{ return 0; }}
                }}""")
            except Exception:
                status = 0
            if status not in (200, 206):
                continue
            try:
                print(f"  [{idx}/5] ready ({status}) — downloading {out.name}", flush=True)
                _download(url, out, page=page)
                outputs[slot] = out
                finished[slot] = True
            except Exception as e:
                print(f"  [{idx}/5] download error: {e}", flush=True)
        time.sleep(3)
    # Close all tabs
    for p in pages:
        try:
            p.close()
        except Exception:
            pass
    if not all(finished):
        unfinished = [pending[i][0] for i, f in enumerate(finished) if not f]
        raise TimeoutError(f"Did not finish within {GEN_TIMEOUT_S}s: indices {unfinished}")
    return [p for p in outputs if p is not None]


def generate_all(scripts_json: Path, out_dir: Path, headless: bool = False,
                 only: list[int] | None = None,
                 parallel: bool = False) -> list[Path]:
    import cloakbrowser

    payload = json.loads(scripts_json.read_text())
    scripts = payload["scripts"]
    out_dir.mkdir(parents=True, exist_ok=True)

    lock_file = _acquire_grok_lock()
    _stale_singleton_cleanup()

    ctx = cloakbrowser.launch_persistent_context(
        user_data_dir=str(GROK_PROFILE_DIR),
        headless=headless,
        viewport={"width": 1280, "height": 500},
        args=["--window-size=1280,500", "--window-position=0,0"],
    )
    outputs: list[Path] = []
    try:
        pending = _collect_pending(scripts, out_dir, only)
        if parallel and len(pending) > 1:
            outputs = _generate_parallel(ctx, pending)
        else:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            for idx, s, img, out in pending:
                print(f"[{idx}/5] {s['object']}", flush=True)
                generate_one(page, img, s["hindi_script"], out)
                outputs.append(out)
    finally:
        try:
            ctx.close()
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            lock_file.close()
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Drive Grok Imagine to make 5 videos")
    parser.add_argument("run_dir", type=Path,
                        help="Per-run output dir (must contain scripts.json + img_*.{jpg,png})")
    parser.add_argument("--headless", action="store_true",
                        help="Hide browser (default: show — easier to debug)")
    parser.add_argument("--only", type=int, nargs="+",
                        help="Only generate these indices (1-5), useful for retries")
    args = parser.parse_args()

    scripts_json = args.run_dir / "scripts.json"
    if not scripts_json.exists():
        sys.stderr.write(f"Missing {scripts_json}\n")
        return 1

    outputs = generate_all(scripts_json, args.run_dir, headless=args.headless, only=args.only)
    print(f"\nGenerated {len(outputs)} videos in {args.run_dir}", file=sys.stderr)
    for p in outputs:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
