"""Shared helpers: HTTP (stdlib only), time formatting, and status icons.

No third-party dependencies are used anywhere in this package.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# Status icons (see README for thresholds).
OK = "✅"  # white check mark
WARN = "⚠️"  # warning sign
CRIT = "\U0001f534"  # red circle
FAIL = "❌"  # cross mark

DEFAULT_TIMEOUT = 15


def http_request(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[int, str]:
    """Perform an HTTP request and return ``(status_code, body_text)``.

    Network failures are reported as status ``0`` with the reason as the body,
    so callers never have to wrap this in a try/except for transport errors.
    """
    req = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted URLs)
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return 0, str(getattr(exc, "reason", exc))


def http_json(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: dict | list | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[int, dict]:
    """JSON request helper. Encodes ``body`` as JSON and parses the response."""
    merged = {"Content-Type": "application/json", **(headers or {})}
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    status, text = http_request(method, url, merged, payload, timeout)
    return status, _loads(text)


def http_form(
    url: str,
    fields: dict[str, str],
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[int, dict]:
    """POST ``application/x-www-form-urlencoded`` fields and parse JSON back."""
    merged = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
    payload = urllib.parse.urlencode(fields).encode("utf-8")
    status, text = http_request("POST", url, merged, payload, timeout)
    return status, _loads(text)


def _loads(text: str) -> dict:
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        return {"raw": text}
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def parse_iso(ts: str | None) -> datetime | None:
    """Parse an ISO-8601 / RFC-3339 timestamp into a timezone-aware datetime.

    Tolerates a trailing ``Z`` and missing seconds (e.g. ``2026-06-29T09:53Z``),
    which older ``datetime.fromisoformat`` implementations reject.
    """
    if not ts:
        return None
    cleaned = ts.strip().replace("Z", "+00:00")
    dt = _try_fromisoformat(cleaned) or _try_strptime(cleaned)
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _try_fromisoformat(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _try_strptime(value: str) -> datetime | None:
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def normalize_iso(ts: str | None) -> str | None:
    """Reformat a timestamp to ``YYYY-MM-DDTHH:MM:SSZ`` (UTC, no microseconds).

    Returns the original value unchanged if it cannot be parsed, so no data is
    lost on unexpected formats.
    """
    dt = parse_iso(ts)
    if dt is None:
        return ts
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_duration(seconds: int) -> str:
    """Render a duration as ``2d 17h`` / ``4h 56m`` / ``5m``."""
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_reset_in(resets_at: str | None, now: datetime | None = None) -> str:
    """Return a human "time until reset" string for an ISO timestamp."""
    target = parse_iso(resets_at)
    if target is None:
        return "unknown"
    reference = now or datetime.now(timezone.utc)
    delta = (target - reference).total_seconds()
    if delta <= 0:
        return "now"
    return format_duration(int(delta))


def status_icon(used_pct: float | None) -> str:
    """Icon based on percentage *used*: <70 ok, 70-90 warn, 90-100 crit, else fail."""
    if used_pct is None:
        return FAIL
    if used_pct >= 100:
        return FAIL
    if used_pct >= 90:
        return CRIT
    if used_pct >= 70:
        return WARN
    return OK


def status_icon_remaining(remaining_pct: float | None) -> str:
    """Icon based on percentage *remaining* (inverse of :func:`status_icon`)."""
    if remaining_pct is None:
        return FAIL
    return status_icon(100.0 - remaining_pct)


def format_pct(value: float | None, decimals: int = 1) -> str:
    """Format a percentage value; ``None`` becomes ``n/a``."""
    if value is None:
        return "n/a"
    return f"{value:.{decimals}f}%"
