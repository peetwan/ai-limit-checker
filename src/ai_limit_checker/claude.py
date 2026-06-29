"""Claude Code usage checker.

Reads the local OAuth token and queries the Anthropic usage endpoint. The
``User-Agent`` header is required: without it the endpoint returns HTTP 429.
"""

from __future__ import annotations

from . import utils
from .credentials import read_claude_credentials

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
USER_AGENT = "claude-code/2.1.186"
OAUTH_BETA = "oauth-2025-04-20"


def fetch_claude_usage(token: str) -> tuple[int, dict]:
    """Call the usage endpoint and return ``(status_code, json)``."""
    return utils.http_json(
        "GET",
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": OAUTH_BETA,
            "User-Agent": USER_AGENT,
        },
    )


def parse_claude_usage(data: dict) -> dict:
    """Turn a raw usage response into the structured ``claude`` payload."""
    return {
        "plan": _extract_plan(data),
        "five_hour": _window(data.get("five_hour")),
        "seven_day": _window(data.get("seven_day")),
        "seven_day_sonnet": _window(data.get("seven_day_sonnet")),
    }


def check_claude(creds: dict | None = None) -> dict:
    """Resolve credentials, query usage, and return the structured result."""
    if creds is None:
        creds = read_claude_credentials()
    if not creds or not creds.get("accessToken"):
        return _empty("no_credentials")

    status, data = fetch_claude_usage(creds["accessToken"])
    if status == 0:
        return _empty("error", f"connection failed: {data.get('raw', 'network error')}")
    if status != 200:
        return _empty("error", f"HTTP {status}")

    parsed = parse_claude_usage(data)
    if not parsed.get("plan"):
        parsed["plan"] = _plan_from_creds(creds)
    return {"status": "ok", "error": None, **parsed}


def _window(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict) or raw.get("utilization") is None:
        return None
    used = round(float(raw["utilization"]), 1)
    return {
        "used_pct": used,
        "remaining_pct": round(100.0 - used, 1),
        "resets_at": utils.normalize_iso(raw.get("resets_at")),
    }


def _extract_plan(data: dict) -> str | None:
    plan = data.get("plan")
    if isinstance(plan, str):
        return plan
    if isinstance(plan, dict):
        return plan.get("name") or plan.get("type")
    return None


def _plan_from_creds(creds: dict) -> str | None:
    sub = creds.get("subscriptionType")
    return sub.capitalize() if isinstance(sub, str) and sub else None


def _empty(status: str, error: str | None = None) -> dict:
    return {
        "status": status,
        "plan": None,
        "five_hour": None,
        "seven_day": None,
        "seven_day_sonnet": None,
        "error": error,
    }
