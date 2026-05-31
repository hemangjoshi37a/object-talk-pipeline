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

# When set, run Grok in text-only video mode (no image upload). The prompt then
# carries the full character description in its SUBJECT block.
SKIP_IMAGES = os.environ.get("SKIP_IMAGES", "0") not in ("0", "false", "False", "")

# Sentinel logged when Grok's video generation quota is exhausted. The web
# backend parses for this marker to surface a distinct error in the UI and to
# abort early so we don't keep retrying clip after clip in a futile way.
QUOTA_MARKER = "GROK_QUOTA_EXCEEDED"


class GrokQuotaExceeded(RuntimeError):
    """Raised when Grok refuses to start a video generation due to limit/quota.
    Distinct from generic RuntimeError so callers can stop the batch immediately."""


def _detect_quota_exceeded(page) -> str | None:
    """Detect Grok's quota-exceeded gate using EXCLUSIVE signals — i.e. text
    that only appears when the server returns a 429 rate-limit, never on a
    healthy page.

    Earlier versions of this detector also matched the "Upgrade to SuperGrok"
    heading. That turned out to be a false-positive trap: Grok renders the
    "Upgrade to SuperGrok" string as a persistent CTA next to the resolution
    selector on every page, regardless of quota state — confirmed live via
    capture_quota_state.py against a healthy session. Only these two phrases
    are exclusive to actual server-side rate-limit responses:

      • "Rate limit reached"
      • "You've reached your limit, come back later"

    Their presence is a high-confidence signal; their absence means the page
    is healthy. Returns a short diagnostic string when detected, None otherwise.
    """
    try:
        body_text = page.evaluate("() => document.body.innerText || ''") or ""
    except Exception:
        return None
    lower = body_text.lower()
    # Exclusive signal #1 — Grok's inline rate-limit notice next to the prompt.
    # CAUTION: Grok now also shows "720p rate limit reached. Switched to 480p."
    # as a benign downgrade toast — generation STILL happens at the lower
    # resolution. We must NOT treat that as a fatal refusal. Discriminator:
    # if the text includes "switched to" (case-insensitive), it's a downgrade
    # notice, not a generation block.
    if "rate limit reached" in lower:
        i = lower.find("rate limit reached")
        snip = body_text[max(0, i - 60): i + 160].replace("\n", " ").strip()
        if "switched to" in snip.lower():
            # Resolution auto-downgrade — generation continues, do not abort.
            return None
        return f"server says \"Rate limit reached\" — context: '{snip}'"
    # Exclusive signal #2 — the longer server toast
    if "you've reached your limit, come back later" in lower:
        return "server rate-limit toast: \"You've reached your limit, come back later.\""
    # Exclusive signal #3 — Grok's "Imagine is currently under heavy load.
    # Try again later." banner. The submit click goes through but the server
    # refuses to start a generation, bouncing us back to /imagine. Without
    # this detector the pipeline would wait the full 300s timeout for a video
    # element that's never coming.
    if "imagine is currently under heavy load" in lower:
        return "server load banner: \"Imagine is currently under heavy load. Try again later.\""
    if "try again later" in lower and "heavy load" in lower:
        return "server load banner (heavy-load variant)"
    return None


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
    """Click into the ProseMirror editor and type the prompt.

    Critical: never send a bare Enter — Grok's ProseMirror interprets it as
    "submit". Embedded "\\n" must become Shift+Enter (soft line break).

    Typing speed: the old impl simulated 12-30 wpm with random per-char think
    pauses; for a 1000-char Pixar-prompt this cost ~60s per clip with no
    detection benefit (cookies already authenticate us). We now type the
    whole text in one keyboard call (delay=0) and only break for "\\n"s.
    """
    editor = page.locator(".ProseMirror.tiptap").first
    _human_jitter(page)
    editor.click()
    page.wait_for_timeout(200)
    # Split on newlines so we can substitute Shift+Enter; otherwise blast the
    # whole segment through page.keyboard.type with zero delay (Playwright
    # serializes input events fast enough that ProseMirror keeps up).
    parts = text.split("\n")
    for i, segment in enumerate(parts):
        if segment:
            page.keyboard.type(segment, delay=0)
        if i < len(parts) - 1:
            page.keyboard.press("Shift+Enter")
    page.wait_for_timeout(250)


def _submit(page) -> None:
    """Wait for Submit to enable, then click it.

    Two things make this trickier than it looks:
      • `is_disabled()` carries Playwright's default 30s auto-wait when the
        locator hasn't settled yet — that swallows the polling loop. We pass
        an explicit short `timeout=` so each probe is bounded.
      • The new prompt (DIALOGUE + ACTION) is longer, so Grok takes a few extra
        seconds to validate the input and enable Submit. Bumped deadline to 60s.
    """
    sub = page.locator('button[aria-label="Submit"]').first
    deadline = time.time() + 60
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            disabled = sub.is_disabled(timeout=2000)
        except Exception as e:
            last_err = e
            page.wait_for_timeout(500)
            continue
        if not disabled:
            try:
                sub.click(timeout=5000)
                return
            except Exception as e:
                last_err = e
                page.wait_for_timeout(500)
                continue
        page.wait_for_timeout(500)
    raise RuntimeError(
        f"Submit button never became enabled within 60s (last error: {last_err})"
    )


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


def _url_ready_via_context(page, url: str) -> int:
    """Readiness probe via Playwright's request context.

    Why not urllib? Because imagine-public.x.ai isn't actually public — it
    checks the request's cookies/Referer and 403s anonymous clients.
    Why not page.evaluate(fetch)? Because that's subject to page CORS, which
    blocks credentialed cross-origin requests with Range headers.

    Playwright's `context.request` lives in Node, not the page — it sends
    the browser's cookies AND isn't subject to CORS. Best of both worlds.

    Returns HTTP status; 0 on network error.
    """
    if not url.startswith("http"):
        return 0
    try:
        # Use GET with a tiny Range header to avoid downloading the full file
        # but still tell the server "I want the start of the file" (some CDNs
        # don't implement HEAD).
        resp = page.context.request.get(
            url,
            headers={
                "Range": "bytes=0-1",
                "Referer": "https://grok.com/",
                "User-Agent": "Mozilla/5.0",
            },
            timeout=15000,
            max_redirects=3,
        )
        return resp.status
    except Exception:
        return 0


def _poll_url_ready(page, url: str, timeout_s: int) -> None:
    """Block until the matching <video> element reports it has loaded data.

    Why not probe the URL directly? Because imagine-public.x.ai inspects more
    than cookies/Referer — even credentialed requests via Playwright's context
    return 403 until the file is fully encoded AND the request comes from an
    actively-rendered <video> element. The browser itself, however, has no
    such trouble: once the <video src=...> tag exists with readyState >= 2,
    the bytes ARE reachable from the page (and _in_page_fetch will succeed).
    So we use the DOM as the ground truth instead.

    readyState scale:
      0 = HAVE_NOTHING       — no data
      1 = HAVE_METADATA      — duration known, no frame data
      2 = HAVE_CURRENT_DATA  — enough to render the current frame
      3 = HAVE_FUTURE_DATA   — enough to play forward briefly
      4 = HAVE_ENOUGH_DATA   — buffered through to end at current speed
    We require >= 2 (the file's first frame has been fetched and decoded).
    """
    deadline = time.time() + timeout_s
    last_ready = -1
    while time.time() < deadline:
        info = page.evaluate(f"""() => {{
            const target = {json.dumps(url)};
            const v = Array.from(document.querySelectorAll('video'))
                .find(v => (v.src || v.currentSrc) === target);
            if (!v) return {{ ready: -1, found: false }};
            return {{
                ready: v.readyState,
                duration: v.duration,
                width: v.videoWidth,
                height: v.videoHeight,
                found: true,
            }};
        }}""")
        if not info.get("found"):
            print("    waiting: <video> not in DOM yet", flush=True)
        else:
            ready = int(info.get("ready", 0))
            if ready != last_ready:
                print(f"    readyState={ready}/4 "
                      f"duration={info.get('duration') or 0:.1f}s "
                      f"{info.get('width') or 0}x{info.get('height') or 0}",
                      flush=True)
                last_ready = ready
            if ready >= 2 and (info.get("duration") or 0) > 0:
                return
        page.wait_for_timeout(3000)
    raise TimeoutError(f"<video> element never reached readyState >= 2 within {timeout_s}s: {url}")


# Back-compat alias — some call sites still use the old name in the parallel pass.
def _url_ready_py(url: str) -> int:
    """DEPRECATED: kept for callers that don't have a page object handy.
    The Playwright-context probe is preferred wherever a page is available."""
    import urllib.request, urllib.error
    if not url.startswith("http"):
        return 0
    req = urllib.request.Request(
        url, method="GET",
        headers={
            "Range": "bytes=0-1",
            "Referer": "https://grok.com/",
            "User-Agent": "Mozilla/5.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def _snapshot_video_urls(page) -> set[str]:
    """Collect every <video src> currently rendered, so we can later detect a
    truly NEW one. Grok keeps a history feed in the DOM (4+ stale `<video>`
    elements from previous submissions), and without a baseline our picker
    happily grabs an old anonymous URL that looks "tall" + valid.
    """
    try:
        urls = page.evaluate("""() =>
            Array.from(document.querySelectorAll('video'))
                .map(v => v.src || v.currentSrc).filter(Boolean)
        """)
        return set(urls)
    except Exception:
        return set()


def _wait_for_video_url(page, timeout_s: int = GEN_TIMEOUT_S,
                        baseline_urls: set[str] | None = None) -> str:
    """Find the tall generated <video>'s src, then block until it loads.

    `baseline_urls` is the set of video URLs that existed BEFORE submit — any
    URL in this set is a stale leftover from Grok's history feed and we ignore
    it. Without this guard we routinely pick a 6s 448x672 anonymous preview
    from a prior attempt instead of the actual fresh generation.
    """
    baseline = baseline_urls or set()
    deadline = time.time() + timeout_s
    started = time.time()
    last_count = -1
    last_heartbeat = started
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
            # Exclude videos that were already in the DOM before submit —
            # those are stale entries from Grok's history feed (especially
            # the anonymous `imagine-public.x.ai` previews from prior runs).
            and u["src"] not in baseline
        ]
        if len(candidates) != last_count:
            print(f"  videos in DOM: {len(candidates)}", flush=True)
            last_count = len(candidates)
        # Bail early if Grok's "heavy load" / rate-limit banner shows up
        # mid-poll — the request was accepted at click time but the server
        # is now refusing to generate. Without this we'd burn the full 300s.
        load_err = _detect_quota_exceeded(page)
        if load_err:
            raise GrokQuotaExceeded(f"{QUOTA_MARKER}: {load_err}")
        # Selection priority (highest first):
        #   1. assets.grok.com/users/...  — the URL pattern produced for the
        #      LOGGED-IN session. Anything we generate now lives here.
        #   2. tall AND not on imagine-public.x.ai — explicitly avoid the
        #      anonymous-session URL: when the DOM has multiple videos (4+),
        #      `imagine-public.x.ai/share-videos/...` is almost always a
        #      stale preview from a previous anonymous run.
        #   3. tall                      — fallback for anonymous mode.
        #   4. first candidate           — last resort.
        # Within each tier, prefer the LAST entry in DOM order: Grok appends
        # new videos at the bottom, so newest == most likely to be ours.
        def pick(filter_fn):
            matches = [u for u in candidates if filter_fn(u)]
            return matches[-1] if matches else None
        authed = pick(lambda u: "assets.grok.com/users/" in u["src"])
        tall_authed = authed  # already an authed URL implies a real generation
        tall_clean = pick(lambda u: u["height"] and u["width"] and u["height"] > u["width"]
                                    and "imagine-public.x.ai" not in u["src"])
        tall_any = pick(lambda u: u["height"] and u["width"] and u["height"] > u["width"])
        first_any = pick(lambda _u: True)
        chosen = tall_authed or tall_clean or tall_any or first_any
        if chosen:
            found_url = chosen["src"]
            break
        # Heartbeat: print elapsed every 30s so the user can tell from the logs
        # that we're still alive and just waiting on Grok to render.
        now = time.time()
        if now - last_heartbeat >= 30:
            elapsed = int(now - started)
            remaining_s = int(deadline - now)
            print(f"  · still waiting for Grok render… "
                  f"elapsed {elapsed}s, {remaining_s}s left "
                  f"({len(candidates)} videos in DOM)", flush=True)
            last_heartbeat = now
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


def _download_via_video_reload(page, url: str, out: Path, timeout_s: int = 30) -> bool:
    """Capture the bytes the <video> element loaded by forcing it to re-fetch
    and intercepting Playwright's `response` event.

    Why this works when nothing else does: `imagine-public.x.ai` 403s every
    request that isn't a <video>-element media fetch (it checks for the
    Sec-Fetch-Dest: video header browsers add automatically, or the absence
    of credentials/Origin, etc.). The <video> tag we found IS that exact
    fetch — we just need to trigger it again and grab the response body.
    """
    try:
        with page.expect_response(
            lambda r: r.url == url and r.status in (200, 206),
            timeout=timeout_s * 1000,
        ) as resp_info:
            # v.load() forces a fresh network fetch (re-runs the media request
            # without re-using the cached buffer). The response event fires
            # for that fetch.
            page.evaluate(f"""() => {{
                const target = {json.dumps(url)};
                const v = Array.from(document.querySelectorAll('video'))
                    .find(v => (v.src || v.currentSrc) === target);
                if (v) v.load();
            }}""")
        resp = resp_info.value
        body = resp.body()
        if body and len(body) > 10_000:
            out.write_bytes(body)
            return True
        print(f"  (video-reload: body too small ({len(body) if body else 0} bytes))",
              flush=True)
        return False
    except Exception as e:
        print(f"  (video-reload capture failed: {e})", flush=True)
        return False


def _download_via_response_capture(page, url: str, out: Path, timeout_s: int = 60) -> bool:
    """Capture the bytes by opening the URL in a fresh tab — heavier than
    `_download_via_video_reload` (slower, sometimes hangs in headless mode);
    kept as a last-resort browser-side path before raw HTTP."""
    new_page = page.context.new_page()
    try:
        try:
            with new_page.expect_response(
                lambda r: r.url == url and r.status in (200, 206),
                timeout=timeout_s * 1000,
            ) as resp_info:
                try:
                    new_page.goto(url, wait_until="commit", timeout=timeout_s * 1000)
                except Exception:
                    pass
            resp = resp_info.value
        except Exception as e:
            print(f"  (response-capture: no response intercepted: {e})", flush=True)
            return False
        body = resp.body()
        if body and len(body) > 10_000:
            out.write_bytes(body)
            return True
        print(f"  (response-capture: body too small ({len(body) if body else 0} bytes))",
              flush=True)
        return False
    finally:
        try:
            new_page.close()
        except Exception:
            pass


def _download(url: str, out: Path, page=None) -> None:
    """Download MP4 using whichever of these works (in order):
      1. Playwright request context — fast, carries session cookies, bypasses
         page CORS. Works for `assets.grok.com/users/...` (auth'd URLs).
      2. **video-element reload + response intercept** — Grok's
         `imagine-public.x.ai` CDN 403s everything except the exact <video>
         media fetch the browser makes. We force the existing <video> to
         re-fetch and grab the response body. Fast.
      3. In-page fetch — same-origin assets only.
      4. New-tab response capture — last-resort browser path; slow.
      5. Raw requests — outside the browser, no cookies.
    """
    if page is not None:
        # 1. Authenticated Playwright request.
        try:
            t0 = time.time()
            resp = page.context.request.get(url, timeout=60000)
            if resp.ok:
                body = resp.body()
                out.write_bytes(body)
                print(f"  · downloaded via Playwright request ({len(body)//1024} KB, "
                      f"{time.time()-t0:.1f}s)", flush=True)
                return
            print(f"  (Playwright request: HTTP {resp.status}; trying video reload)",
                  flush=True)
        except Exception as e:
            print(f"  (Playwright request failed: {e}; trying video reload)",
                  flush=True)
        # 2. Force the <video> element to re-fetch, intercept the response.
        try:
            t0 = time.time()
            if _download_via_video_reload(page, url, out, timeout_s=30):
                print(f"  · downloaded via video-reload "
                      f"({out.stat().st_size//1024} KB, {time.time()-t0:.1f}s)",
                      flush=True)
                return
        except Exception as e:
            print(f"  (video-reload failed: {e}; trying in-page fetch)", flush=True)
        # 3. In-page fetch (cookies + Referer, CORS-prone).
        try:
            t0 = time.time()
            body = _in_page_fetch(page, url)
            out.write_bytes(body)
            print(f"  · downloaded via in-page fetch ({len(body)//1024} KB, "
                  f"{time.time()-t0:.1f}s)", flush=True)
            return
        except Exception as e:
            print(f"  (in-page fetch failed: {e}; trying new-tab capture)",
                  flush=True)
        # 4. New-tab response capture (slow, last resort with the page).
        try:
            t0 = time.time()
            if _download_via_response_capture(page, url, out, timeout_s=30):
                print(f"  · downloaded via new-tab capture "
                      f"({out.stat().st_size//1024} KB, {time.time()-t0:.1f}s)",
                      flush=True)
                return
        except Exception as e:
            print(f"  (new-tab capture failed: {e})", flush=True)
    # Last-resort fallback: raw requests with retry-on-transient-errors.
    # Imported here (not top of module) to keep this fallback path lazy and
    # avoid breaking older deployments that don't have http_utils.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from http_utils import get_with_retry
    r = get_with_retry(url, timeout=120, stream=True, label=f"video-dl:{out.name}")
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


def _setup_and_submit(page, image_path: Path | None, prompt: str) -> set[str]:
    """The setup phase: reset, configure, upload, prompt, submit.

    Returns the set of video URLs that existed in the DOM AT THE INSTANT of
    submit — used by `_wait_for_video_url` to filter out stale Grok-history
    entries (otherwise the picker grabs a 6s anonymous preview from a prior
    attempt).

    `image_path` may be None — text-only video mode (prompt carries the
    character description).
    """
    _ensure_fresh_chat(page)
    # Quota check BEFORE we even try to set up — if Grok's landing page already
    # says we're out of generations, fail fast without wasting time on upload+typing.
    early = _detect_quota_exceeded(page)
    if early:
        raise GrokQuotaExceeded(f"{QUOTA_MARKER}: {early}")
    _switch_to_video_mode(page)
    _set_aspect_9_16(page)
    _set_resolution(page, "720p")
    _set_duration(page, "10s")
    if image_path is not None:
        _upload_image(page, image_path)
    _set_prompt(page, prompt)
    # Snapshot the existing video URLs RIGHT before submit — anything we find
    # later that's NOT in this set is a genuinely new generation, not a stale
    # entry from Grok's history feed.
    baseline = _snapshot_video_urls(page)
    if baseline:
        print(f"  · baseline videos in DOM (will skip): {len(baseline)}", flush=True)
    _submit(page)
    page.wait_for_timeout(1200)  # brief settle before quota check + URL poll
    # Post-submit quota check — Grok sometimes only flags the quota after you
    # click submit (toast appears instead of navigating to the generation view).
    after = _detect_quota_exceeded(page)
    if after:
        raise GrokQuotaExceeded(f"{QUOTA_MARKER}: {after}")
    return baseline


def generate_one(page, image_path: Path | None, prompt: str, out_path: Path) -> None:
    print(f"  · setup + submit{' (text-only)' if image_path is None else ''}")
    baseline = _setup_and_submit(page, image_path, prompt)
    print(f"  · waiting for navigation to settle...")
    page.wait_for_timeout(1500)
    _dump_post_gen_state(page, f"after_submit_{int(time.time())}")
    print(f"  · waiting for video generation (up to {GEN_TIMEOUT_S}s)...")
    url = _wait_for_video_url(page, baseline_urls=baseline)
    _dump_post_gen_state(page, f"after_ready_{int(time.time())}")
    print(f"  · downloading from {url[:80]}...")
    _download(url, out_path, page=page)
    print(f"  · saved {out_path.name} ({out_path.stat().st_size // 1024} KB)")


def _collect_pending(scripts, out_dir: Path, only):
    """Return [(idx, script, image_path|None, out_path), ...] for items that need work.

    When SKIP_IMAGES is set, missing images are tolerated and the slot is queued
    with image_path=None — Grok then runs in text-only video mode.
    """
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
        img: Path | None
        if img_candidates:
            img = img_candidates[0]
        elif SKIP_IMAGES:
            img = None
        else:
            raise FileNotFoundError(f"No image for script #{i} ({s['object']}) in {out_dir}")
        pending.append((i, s, img, out))
    return pending


GROK_STYLE_GUARD = (
    "STYLE (maintain throughout every single frame, no drift):\n"
    "Pixar-style 3D animated cartoon character — NOT a human, NOT a person. "
    "Keep the personified object's exact look, proportions, eyes, mouth and "
    "color scheme stable across all frames. Soft cinematic lighting, shallow "
    "depth of field. The character is an animated OBJECT with a face — do not "
    "morph it into a human figure, human body, or realistic person."
)


def _build_grok_prompt(s: dict, include_character: bool = True) -> str:
    """Build the full prompt typed into Grok's text box for a single clip.

    Sections (each labelled so Grok knows what role each plays):
      STYLE    — guards against drift to a realistic human figure
      SUBJECT  — character's visual description (carried in prompt even when
                 an image is uploaded, since the visual anchor sometimes still
                 drifts after the first frame)
      ACTION   — camera + character + scenery motion
      DIALOGUE — Hindi script verbatim (Grok lip-syncs to this)
    """
    dialogue = (s.get("hindi_script") or "").strip()
    action = (s.get("action_script") or "").strip()
    character = (s.get("image_prompt") or "").strip() if include_character else ""

    parts = [GROK_STYLE_GUARD]
    if character:
        parts.append(
            "SUBJECT (the visual character — render this exactly):\n"
            f"{character}"
        )
    if action:
        parts.append(
            "ACTION (camera + character + scenery motion during the clip):\n"
            f"{action}"
        )
    if dialogue:
        parts.append(
            "DIALOGUE (the character speaks this verbatim in Hindi, lip-sync to it):\n"
            f"{dialogue}"
        )
    return "\n\n".join(parts)


def _generate_parallel(ctx, pending) -> list[Path]:
    """Setup-then-wait pattern: open one tab per pending item, submit all serially,
    then poll all tabs in parallel for video URLs and download as they complete.

    Per-tab isolation: if one tab fails to set up/submit (Grok UI glitch,
    Cloudflare challenge, etc.) we mark that slot dead and keep the rest alive.
    Failed slots are returned to the caller as None so they can be retried serially.
    """
    if not pending:
        return []
    print(f"(parallel: opening {len(pending)} tabs)", flush=True)
    pages = [ctx.new_page() for _ in pending]
    # Setup phase — serial per tab, but each tab's "waiting" phase begins as soon as we hit submit.
    # A failure in one tab MUST NOT abort the others — log it and continue.
    submitted: list[bool] = [False] * len(pending)
    setup_errors: list[str | None] = [None] * len(pending)
    baselines: list[set[str]] = [set() for _ in pending]
    quota_hit = False
    for slot, (idx, s, img, out) in enumerate(pending):
        if quota_hit:
            # No point setting up more tabs once Grok said we're out of generations.
            setup_errors[slot] = "skipped — quota already exhausted"
            print(f"  [{idx}/5] SKIPPED (quota exhausted on earlier tab)", flush=True)
            continue
        print(f"[{idx}/5] {s['object']}: setup tab {slot+1}", flush=True)
        try:
            baselines[slot] = _setup_and_submit(pages[slot], img, _build_grok_prompt(s, include_character=True))
            submitted[slot] = True
        except GrokQuotaExceeded as e:
            setup_errors[slot] = str(e)[:300]
            print(f"  [{idx}/5] ✗ {e}", flush=True)
            print(f"  ⛔ {QUOTA_MARKER}: aborting remaining tabs — won't waste retries.", flush=True)
            quota_hit = True
        except Exception as e:
            setup_errors[slot] = str(e)[:200]
            print(f"  [{idx}/5] SETUP FAILED — will retry serially after others finish. ({setup_errors[slot]})", flush=True)
    n_submitted = sum(submitted)
    print(f"  {n_submitted}/{len(pending)} submitted; polling for completion...", flush=True)
    outputs: list[Path | None] = [None] * len(pending)
    finished = [not submitted[i] for i in range(len(pending))]  # tabs that didn't submit are pre-"finished" (skipped)
    download_attempts: list[dict] = [{"tries": 0, "last_url": None, "last_error": None} for _ in pending]
    deadline = time.time() + GEN_TIMEOUT_S
    while not all(finished) and time.time() < deadline:
        for slot, (idx, s, img, out) in enumerate(pending):
            if finished[slot]:
                continue
            page = pages[slot]
            try:
                urls = page.evaluate("""() => Array.from(document.querySelectorAll('video')).map(v => ({
                    src: v.src || v.currentSrc, width: v.videoWidth, height: v.videoHeight,
                    duration: v.duration, readyState: v.readyState,
                }))""")
            except Exception as e:
                download_attempts[slot]["last_error"] = f"evaluate: {e}"
                continue
            candidates = [u for u in urls if u["src"] and "tooltip" not in u["src"] and "nux" not in u["src"]
                          and (u["src"].startswith("http") or u["src"].startswith("blob:"))
                          and u["src"] not in baselines[slot]]
            tall = [u for u in candidates if u["height"] and u["width"] and u["height"] > u["width"]]
            pool = tall or candidates
            if not pool:
                continue
            # Latest entry first (Grok appends new videos at the end).
            url = pool[-1]["src"]
            download_attempts[slot]["last_url"] = url
            # Ready-check: query the <video> element's readyState in the DOM —
            # external URL probes hit 403 even when the browser can play it
            # (the CDN does weird origin checks).
            ready_info = page.evaluate(f"""() => {{
                const target = {json.dumps(url)};
                const v = Array.from(document.querySelectorAll('video'))
                    .find(v => (v.src || v.currentSrc) === target);
                return v ? {{ ready: v.readyState, duration: v.duration }}
                         : {{ ready: -1, duration: 0 }};
            }}""")
            if not (ready_info.get("ready", 0) >= 2 and (ready_info.get("duration") or 0) > 0):
                download_attempts[slot]["last_error"] = (
                    f"ready-check: readyState={ready_info.get('ready')}"
                )
                continue
            download_attempts[slot]["tries"] += 1
            try:
                print(f"  [{idx}/5] ready ({status}) — downloading {out.name} (attempt {download_attempts[slot]['tries']})", flush=True)
                _download(url, out, page=page)
                # Sanity: the file must actually contain video bytes
                if not out.exists() or out.stat().st_size < 10_000:
                    raise RuntimeError(f"download produced {out.stat().st_size if out.exists() else 0} bytes — likely empty/HTML")
                outputs[slot] = out
                finished[slot] = True
            except Exception as e:
                download_attempts[slot]["last_error"] = f"download: {e}"
                print(f"  [{idx}/5] download error (try {download_attempts[slot]['tries']}): {e}", flush=True)
                # Give up after 3 attempts to download this same URL — the URL may have expired
                if download_attempts[slot]["tries"] >= 3:
                    print(f"  [{idx}/5] ✗ giving up after 3 download attempts; will retry serially later", flush=True)
                    finished[slot] = True  # mark "done with parallel attempt" so we move on
        time.sleep(3)
    # Close all tabs
    for p in pages:
        try:
            p.close()
        except Exception:
            pass
    # Final per-slot report so caller can see what failed and why
    for slot, (idx, s, _, _) in enumerate(pending):
        if outputs[slot] is None:
            why = setup_errors[slot] or download_attempts[slot].get("last_error") or "unknown"
            print(f"  [{idx}/5] ✗ NOT captured in parallel pass — reason: {why}", flush=True)
    # Return outputs (may contain None for failed slots) — caller decides retry strategy
    return outputs  # type: ignore[return-value]


def generate_all(scripts_json: Path, out_dir: Path, headless: bool = False,
                 only: list[int] | None = None,
                 parallel: bool = False) -> list[Path]:
    import cloakbrowser

    payload = json.loads(scripts_json.read_text())
    scripts = payload["scripts"]
    out_dir.mkdir(parents=True, exist_ok=True)

    # Auto-detect headless: launching a headed Chromium without an X server
    # (e.g. backend started over SSH with no DISPLAY) crashes with a cryptic
    # "Target page, context or browser has been closed" + X11 error. The
    # persistent profile already carries the Grok login cookies, so headless
    # works fine for everything except the first-time interactive login.
    if not headless and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        print("  [grok] no DISPLAY/WAYLAND_DISPLAY in environment — falling back to headless mode "
              "(cookies in browser_data/grok will be reused). To force a headed browser, "
              "export DISPLAY=:0 before starting the backend.", flush=True)
        headless = True

    lock_file = _acquire_grok_lock()
    _stale_singleton_cleanup()

    ctx = cloakbrowser.launch_persistent_context(
        user_data_dir=str(GROK_PROFILE_DIR),
        headless=headless,
        viewport={"width": 1280, "height": 500},
        args=["--window-size=1280,500", "--window-position=0,0"],
    )
    outputs: list[Path] = []
    quota_exhausted = False
    try:
        pending = _collect_pending(scripts, out_dir, only)
        if parallel and len(pending) > 1:
            try:
                parallel_out = _generate_parallel(ctx, pending)
            except GrokQuotaExceeded as e:
                print(f"⛔ {e}", flush=True)
                quota_exhausted = True
                parallel_out = [None] * len(pending)
            # Fold completed paths into outputs; collect the failed slots for serial retry.
            failed_pending = []
            for slot, (idx, s, img, out) in enumerate(pending):
                if parallel_out[slot] is not None:
                    outputs.append(parallel_out[slot])
                else:
                    failed_pending.append((idx, s, img, out))
            # Retry failed slots serially in a fresh tab — but ONLY if we
            # haven't hit the quota. Retrying after a quota error is futile.
            if failed_pending and not quota_exhausted:
                print(f"⟳ retrying {len(failed_pending)} slot(s) serially: "
                      f"{[i for i, *_ in failed_pending]}", flush=True)
                page = ctx.new_page()
                for idx, s, img, out in failed_pending:
                    if quota_exhausted:
                        print(f"  [{idx}/5] skipping serial retry — quota exhausted", flush=True)
                        continue
                    print(f"[{idx}/5] {s['object']} (serial retry)", flush=True)
                    try:
                        generate_one(page, img, _build_grok_prompt(s, include_character=True), out)
                        outputs.append(out)
                    except GrokQuotaExceeded as e:
                        print(f"  ⛔ {e} — stopping further attempts.", flush=True)
                        quota_exhausted = True
                    except Exception as e:
                        print(f"  [{idx}/5] ✗ serial retry also failed: {e}", flush=True)
        else:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            for idx, s, img, out in pending:
                if quota_exhausted:
                    print(f"  [{idx}/5] skipping — quota exhausted on earlier clip", flush=True)
                    continue
                print(f"[{idx}/5] {s['object']}", flush=True)
                try:
                    generate_one(page, img, _build_grok_prompt(s, include_character=True), out)
                    outputs.append(out)
                except GrokQuotaExceeded as e:
                    print(f"  ⛔ {e}", flush=True)
                    quota_exhausted = True
    finally:
        try:
            ctx.close()
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            lock_file.close()
    # If Grok refused at any point (quota / rate-limit / heavy-load), raise
    # so the orchestrator (pipeline.py) HALTS before merge + upload. Previously
    # this only raised when *zero* clips succeeded — meaning a 1-of-5 run would
    # silently continue, merge the single clip, and upload to YouTube as if
    # the whole batch worked. The user wants a Grok-side error to abort the
    # entire run; partial output is never the intent. Successful clips stay
    # on disk so a subsequent "Retry from videos" picks up where this stopped.
    if quota_exhausted:
        produced = len(outputs)
        raise GrokQuotaExceeded(
            f"{QUOTA_MARKER}: Grok refused mid-batch — {produced} clip(s) saved, "
            f"remaining halted. The successful clips stay on disk; wait for the "
            f"server-side issue to clear, then retry from the videos step."
        )
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
