"""Website scraper utility for the product-video flow.

Lightweight requests+BS4 first, optional Playwright (cloakbrowser) fallback
for SPAs / bot-walled sites. Writes page.json + downloaded images under
out_dir, and returns the resulting dict.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from http_utils import get_with_retry  # noqa: E402

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
MAX_TEXT_CHARS = 6000
MIN_IMG_BYTES = 5 * 1024
MAX_IMG_BYTES = 5 * 1024 * 1024
STRIP_TAGS = ("script", "style", "nav", "footer", "header", "noscript", "svg", "form")
_WS = re.compile(r"\s+")


def _make_soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def _meta(soup: BeautifulSoup, name: str, prop: bool = False) -> str | None:
    key = "property" if prop else "name"
    tag = soup.find("meta", attrs={key: name})
    if tag and tag.get("content"):
        return tag["content"].strip() or None
    return None


def _extract_text(soup: BeautifulSoup) -> str:
    root = soup.find("main") or soup.find("article") or soup.body or soup
    clone = BeautifulSoup(str(root), "html.parser")
    for tag in clone.find_all(STRIP_TAGS):
        tag.decompose()
    text = clone.get_text(separator=" ", strip=True)
    text = _WS.sub(" ", text).strip()
    return text[:MAX_TEXT_CHARS]


def _area_estimate(img_tag: Any) -> int:
    def _intval(v: Any) -> int:
        try:
            return int(re.sub(r"[^0-9]", "", str(v)) or "0")
        except Exception:
            return 0
    w = _intval(img_tag.get("width"))
    h = _intval(img_tag.get("height"))
    if w and h:
        return w * h
    if w or h:
        return (w or h) * 200
    return 0


def _collect_image_urls(soup: BeautifulSoup, base_url: str, limit_seen: int = 40) -> list[str]:
    main = soup.find("main") or soup.find("article") or soup.body or soup
    main_imgs = main.find_all("img") if main else []
    main_set = {id(t) for t in main_imgs}
    all_imgs = soup.find_all("img")
    scored: list[tuple[int, int, str]] = []
    for idx, tag in enumerate(all_imgs):
        src = tag.get("src") or tag.get("data-src") or tag.get("data-original")
        if not src:
            srcset = tag.get("srcset")
            if srcset:
                src = srcset.split(",")[0].strip().split(" ")[0]
        if not src:
            continue
        src = src.strip()
        if src.startswith("data:"):
            continue
        try:
            full = urljoin(base_url, src)
        except Exception:
            continue
        if not full.startswith(("http://", "https://")):
            continue
        in_main = id(tag) in main_set
        score = _area_estimate(tag) + (1_000_000 if in_main else 0)
        scored.append((score, -idx, full))
    scored.sort(reverse=True)
    seen: list[str] = []
    dedup: set[str] = set()
    for _, _, url in scored:
        if url in dedup:
            continue
        dedup.add(url)
        seen.append(url)
        if len(seen) >= limit_seen:
            break
    return seen


def _ext_from_response(resp: requests.Response, url: str) -> str:
    ct = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if ct:
        ext = mimetypes.guess_extension(ct)
        if ext:
            if ext == ".jpe":
                return ".jpg"
            return ext
    path = urlparse(url).path
    suf = Path(path).suffix.lower()
    if suf in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"):
        return ".jpg" if suf == ".jpeg" else suf
    return ".jpg"


def _download_images(urls: list[str], out_dir: Path, max_images: int) -> list[str]:
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for url in urls:
        if len(saved) >= max_images:
            break
        try:
            resp = get_with_retry(
                url,
                headers=HEADERS,
                timeout=15,
                max_retries=1,
                label=f"img-{len(saved)+1}",
                stream=False,
            )
            if resp.status_code >= 400:
                continue
            data = resp.content
            if len(data) < MIN_IMG_BYTES or len(data) > MAX_IMG_BYTES:
                continue
            ext = _ext_from_response(resp, url)
            name = f"img_{len(saved)+1:02d}{ext}"
            (images_dir / name).write_bytes(data)
            saved.append(f"images/{name}")
        except Exception:
            continue
    return saved


def _parse_html(html: str, url: str, max_images: int) -> dict[str, Any]:
    soup = _make_soup(html)
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    og_title = _meta(soup, "og:title", prop=True)
    description = _meta(soup, "description") or _meta(soup, "og:description", prop=True)
    og_image_raw = _meta(soup, "og:image", prop=True)
    og_image = urljoin(url, og_image_raw) if og_image_raw else None
    text = _extract_text(soup)
    image_urls = _collect_image_urls(soup, url)
    if og_image and og_image not in image_urls:
        image_urls.insert(0, og_image)
    return {
        "title": title or (og_title or ""),
        "description": description,
        "og_image": og_image,
        "text": text,
        "image_urls_seen": image_urls,
        "html_len": len(html),
    }


def _try_requests(url: str, timeout_s: int) -> tuple[str | None, str | None]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout_s, allow_redirects=True)
        if resp.status_code >= 400:
            return None, f"HTTP {resp.status_code}"
        return resp.text or "", None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _try_playwright(url: str, out_dir: Path, timeout_s: int) -> tuple[str | None, str | None, str | None]:
    try:
        import cloakbrowser  # type: ignore
    except Exception as e:
        return None, None, f"cloakbrowser import failed: {e}"

    user_data_dir = tempfile.mkdtemp(prefix="scrape_pw_")
    screenshot_rel: str | None = None
    html: str | None = None
    err: str | None = None
    ctx = None
    try:
        ctx = cloakbrowser.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=True,
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
        except Exception as e:
            err = f"goto failed: {type(e).__name__}: {e}"
        try:
            page.wait_for_timeout(2500)
        except Exception:
            pass
        try:
            html = page.content()
        except Exception as e:
            err = err or f"content() failed: {type(e).__name__}: {e}"
        try:
            shot_path = out_dir / "screenshot.png"
            page.screenshot(path=str(shot_path), full_page=False)
            if shot_path.exists() and shot_path.stat().st_size > 0:
                screenshot_rel = "screenshot.png"
        except Exception:
            pass
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    finally:
        try:
            if ctx is not None:
                ctx.close()
        except Exception:
            pass
    return html, screenshot_rel, err


def scrape(url: str, out_dir: Path, max_images: int = 5, timeout_s: int = 20) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "url": url,
        "title": "",
        "description": None,
        "og_image": None,
        "text": "",
        "image_urls_seen": [],
        "saved_images": [],
        "screenshot": None,
        "fetched_at": int(time.time()),
        "fetched_via": "failed",
    }

    parsed: dict[str, Any] | None = None
    fetched_via: str | None = None
    err_msg: str | None = None

    html, req_err = _try_requests(url, timeout_s)
    if html is not None:
        try:
            parsed = _parse_html(html, url, max_images)
            fetched_via = "requests"
        except Exception as e:
            err_msg = f"requests-parse: {type(e).__name__}: {e}"
    else:
        err_msg = req_err

    need_fallback = False
    if parsed is None:
        need_fallback = True
    else:
        if parsed["html_len"] < 500:
            need_fallback = True
        elif not parsed["image_urls_seen"] and len(parsed["text"]) < 200:
            need_fallback = True

    screenshot_rel: str | None = None
    if need_fallback:
        pw_html, screenshot_rel, pw_err = _try_playwright(url, out_dir, timeout_s)
        if pw_html:
            try:
                parsed = _parse_html(pw_html, url, max_images)
                fetched_via = "playwright"
                err_msg = None
            except Exception as e:
                err_msg = f"playwright-parse: {type(e).__name__}: {e}"
        else:
            err_msg = err_msg or pw_err

    if parsed is not None and fetched_via is not None:
        seen = parsed["image_urls_seen"]
        saved: list[str] = []
        try:
            saved = _download_images(seen, out_dir, max_images)
        except Exception:
            saved = []
        result.update(
            {
                "title": parsed["title"],
                "description": parsed["description"],
                "og_image": parsed["og_image"],
                "text": parsed["text"],
                "image_urls_seen": seen,
                "saved_images": saved,
                "screenshot": screenshot_rel,
                "fetched_via": fetched_via,
            }
        )
    else:
        result["screenshot"] = screenshot_rel
        result["error"] = err_msg or "unknown error"

    (out_dir / "page.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Scrape a website for product-video context.")
    ap.add_argument("url")
    ap.add_argument("out_dir")
    ap.add_argument("--max-images", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=20)
    args = ap.parse_args()
    try:
        data = scrape(args.url, Path(args.out_dir), max_images=args.max_images, timeout_s=args.timeout)
    except Exception as e:
        sys.stderr.write(f"scrape failed: {type(e).__name__}: {e}\n")
        return 1
    if data.get("fetched_via") == "failed":
        sys.stderr.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        return 1
    sys.stdout.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
