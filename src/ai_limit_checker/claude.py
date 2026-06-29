"""Claude Code usage checker.

Reads the local OAuth token and queries the Anthropic usage endpoint. The
``User-Agent`` header is required: without it the endpoint returns HTTP 429.

Access tokens expire after ~8 hours. When that happens the usage endpoint
returns HTTP 401. This module automatically refreshes the token via the
``platform.claude.com`` OAuth endpoint (using the same ``client_id`` that
Claude Code itself uses) and retries the request once.
"""

from __future__ import annotations

import time

from . import utils
from .credentials import read_claude_credentials

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
USER_AGENT = "claude-code/2.1.186"
OAUTH_BETA = "oauth-2025-04-20"

# OAuth token refresh — same endpoint and client_id that Claude Code uses
# internally (extracted from the claude binary). ``expiresAt`` in the
# credentials file is a Unix timestamp in *milliseconds*.
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"

# Refresh a few minutes early to avoid race conditions
_EXPIRY_SKEW_MS = 60_000


def _is_token_expired(creds: dict, now_ms: float | None = None) -> bool:
    """Return ``True`` if the access token is expired or about to expire."""
    expires_at = creds.get("expiresAt")
    if expires_at is None:
        return False  # unknown expiry — assume valid, let the 401 path handle it
    reference = now_ms if now_ms is not None else time.time() * 1000
    return expires_at <= reference + _EXPIRY_SKEW_MS


def refresh_claude_token(refresh_token: str) -> dict:
    """Exchange a refresh token for a fresh access token via Anthropic OAuth.

    Returns the parsed token response (``access_token``, ``expires_in``, …).
    Raises :class:`RuntimeError` on failure.
    """
    status, data = utils.http_form(
        TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        },
    )
    token = data.get("access_token")
    if status != 200 or not token:
        raise RuntimeError(f"Claude token refresh failed (HTTP {status})")
    return data


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
    """Resolve credentials, query usage, and return the structured result.

    If the access token is expired (or missing), it is automatically
    refreshed using the stored refresh token. If the usage endpoint still
    returns 401, a single refresh + retry is attempted.
    """
    if creds is None:
        creds = read_claude_credentials()
    if not creds or not creds.get("accessToken"):
        # Maybe we only have a refresh token (e.g. from a partial creds file)
        if creds and creds.get("refreshToken"):
            try:
                token_data = refresh_claude_token(creds["refreshToken"])
                creds = {**creds, "accessToken": token_data["access_token"]}
            except RuntimeError:
                return _empty("no_credentials")
        else:
            return _empty("no_credentials")

    # Proactive refresh: if the token is expired, refresh before making the request
    if _is_token_expired(creds) and creds.get("refreshToken"):
        try:
            token_data = refresh_claude_token(creds["refreshToken"])
            creds = {**creds, "accessToken": token_data["access_token"]}
        except RuntimeError:
            pass  # proceed with the old token; maybe it's still valid

    status, data = fetch_claude_usage(creds["accessToken"])

    # Reactive refresh: on 401, refresh and retry once
    if status == 401 and creds.get("refreshToken"):
        try:
            token_data = refresh_claude_token(creds["refreshToken"])
            creds = {**creds, "accessToken": token_data["access_token"]}
            status, data = fetch_claude_usage(creds["accessToken"])
        except RuntimeError:
            pass  # fall through with the original 401

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
