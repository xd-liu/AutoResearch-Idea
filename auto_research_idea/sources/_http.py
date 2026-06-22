"""Shared HTTP helper with backoff — paper APIs rate-limit aggressively."""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)


def _retry_after_seconds(value) -> Optional[float]:
    """Parse a Retry-After header value as delta-seconds, else None.

    Per RFC 7231 the header may also be an HTTP-date; we don't compute the delta
    for that form — returning None lets the caller fall back to its backoff
    rather than crashing on float()."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_with_retry(
    url: str,
    *,
    params: dict,
    headers: Optional[dict] = None,
    timeout: int = 30,
    max_attempts: int = 4,
) -> Optional[requests.Response]:
    """GET with exponential backoff on 429/5xx/timeout.

    Returns the successful Response, or None if every attempt failed (callers
    treat None as "no results from this source" rather than crashing the run).
    """
    backoff = 2.0
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.get(url, params=params, headers=headers or {}, timeout=timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                ra = _retry_after_seconds(resp.headers.get("Retry-After"))
                # Honor a longer server-requested wait, but never dip below our
                # own exponential backoff (a 'Retry-After: 0' must not busy-loop).
                wait = max(ra, backoff) if ra is not None else backoff
                if attempt < max_attempts:
                    time.sleep(wait)
                    backoff *= 2
                    continue
                logger.warning("%s: %s after %d attempts", url, resp.status_code, attempt)
                return None
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            if attempt < max_attempts:
                time.sleep(backoff)
                backoff *= 2
                continue
            logger.warning("%s failed after %d attempts: %s", url, attempt, e)
            return None
    return None
