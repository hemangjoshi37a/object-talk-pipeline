"""Shared HTTP retry-with-backoff helper for pipeline steps.

Gemini / YouTube / Grok endpoints all suffer occasional transient failures:
TLS read timeouts, mid-response chunked-encoding aborts, 5xx, 429 rate limits.
A single failure should not kill a 20-minute pipeline run — wrap every
network call to a remote API with `post_with_retry` / `get_with_retry`.
"""
from __future__ import annotations

import random
import sys
import time
from typing import Any, Iterable

import requests

# Errors we always retry. ConnectionError + Timeout cover the bulk of
# transient cases (TCP reset, slow remote, mid-chunk read timeout).
_NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)

# Status codes worth retrying. 408/425/429 = retry-after; 5xx = remote glitch.
_DEFAULT_RETRY_STATUS: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})


def _sleep_backoff(attempt: int, base: float = 4.0, cap: float = 60.0) -> float:
    """Exponential backoff with jitter. attempt is 1-indexed for the first retry."""
    delay = min(cap, base * (2 ** (attempt - 1)))
    # Decorrelated jitter — avoids thundering-herd when many runs retry together.
    delay = random.uniform(base, delay)
    return delay


def request_with_retry(
    method: str,
    url: str,
    *,
    max_retries: int = 5,
    timeout: float | tuple[float, float] = 120,
    retry_status: Iterable[int] | None = None,
    label: str = "",
    **kwargs: Any,
) -> requests.Response:
    """HTTP request with exponential backoff on transient errors.

    Behaviour:
      • ConnectionError / Timeout / ChunkedEncodingError → retry
      • HTTP status in retry_status → retry
      • Other HTTPError or non-retry status → returned as-is (caller handles)

    Args:
        method: 'GET', 'POST', etc.
        url: full URL.
        max_retries: maximum number of *additional* attempts after the first.
        timeout: passed through to requests.
        retry_status: which HTTP statuses to treat as transient. Defaults to
                      408/425/429/5xx.
        label: short description used in retry log lines (e.g. "image-2").

    Returns the final `requests.Response`. Raises the last exception only
    after all retries are exhausted.
    """
    statuses = frozenset(retry_status) if retry_status is not None else _DEFAULT_RETRY_STATUS
    tag = f"[{label}] " if label else ""
    last_exc: BaseException | None = None
    last_status: int | None = None
    # 1 initial + max_retries → total attempts
    for attempt in range(1, max_retries + 2):
        try:
            resp = requests.request(method, url, timeout=timeout, **kwargs)
        except _NETWORK_ERRORS as e:
            last_exc = e
            if attempt > max_retries:
                break
            delay = _sleep_backoff(attempt)
            sys.stderr.write(
                f"  {tag}network error (attempt {attempt}/{max_retries+1}): "
                f"{type(e).__name__}: {e} — retrying in {delay:.1f}s\n"
            )
            sys.stderr.flush()
            time.sleep(delay)
            continue
        # Got a response — decide whether to retry based on status
        if resp.status_code in statuses:
            last_status = resp.status_code
            if attempt > max_retries:
                return resp  # exhausted — let caller handle the bad response
            delay = _sleep_backoff(attempt)
            # Honour Retry-After if the server gave us one (cap at 120s)
            ra = resp.headers.get("Retry-After")
            if ra:
                try:
                    delay = max(delay, min(120.0, float(ra)))
                except ValueError:
                    pass
            sys.stderr.write(
                f"  {tag}HTTP {resp.status_code} (attempt {attempt}/{max_retries+1}) "
                f"— retrying in {delay:.1f}s\n"
            )
            sys.stderr.flush()
            time.sleep(delay)
            continue
        return resp

    # All retries failed with network exceptions
    assert last_exc is not None or last_status is not None
    if last_exc is not None:
        raise last_exc
    # Should not reach here — when status retries exhaust we return the response above
    raise RuntimeError(f"{tag}retries exhausted, last status: {last_status}")


def post_with_retry(url: str, **kwargs: Any) -> requests.Response:
    return request_with_retry("POST", url, **kwargs)


def get_with_retry(url: str, **kwargs: Any) -> requests.Response:
    return request_with_retry("GET", url, **kwargs)
