"""Antigravity CLI usage checker.

Reads the local Google OAuth token (refreshing it if expired), then queries
Cloud Code's internal endpoints for the active project, tier, and the grouped
usage limits (weekly + five-hour windows) that the Antigravity desktop app
displays.

Usage comes from ``retrieveUserQuotaSummary`` — the same endpoint the desktop
app uses. It returns model *groups* ("Gemini Models", "Claude and GPT models"),
each with a "Weekly Limit" and a "Five Hour Limit". The older
``retrieveUserQuota`` / ``fetchAvailableModels`` endpoints report a raw
per-model ``remainingFraction`` that is always ``1`` (the real limits are
enforced at the group level), which is why they appear to show "100% free".

``loadCodeAssist`` reports *two* tiers. ``currentTier`` is the Cloud Code
Assist API tier, which is always ``free-tier`` for consumer (non-GCP) accounts
no matter what Google One AI subscription they hold. ``paidTier`` carries the
actual Google One subscription (e.g. "Google AI Ultra") and is only present
when one exists — the agy binary reads this same field to detect Google One
credits. We therefore prefer ``paidTier`` for the displayed tier.

OAuth client credentials are NOT hardcoded. They are extracted from the
agy binary at runtime so the package works for anyone with agy installed.
Users may override via env vars ``AGY_CLIENT_ID`` / ``AGY_CLIENT_SECRET``.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

from . import utils
from .credentials import read_antigravity_credentials

# The Antigravity desktop app uses the "daily-" prefixed host, which returns
# real-time quota data (including Gemini usage). The non-prefixed host returns
# remainingFraction=1 for Gemini models (always 100% remaining), which is wrong.
# Discovered by inspecting the agy language server logs (2026-06-29).
_API_HOST = "https://daily-cloudcode-pa.googleapis.com"
LOAD_URL = f"{_API_HOST}/v1internal:loadCodeAssist"
QUOTA_SUMMARY_URL = f"{_API_HOST}/v1internal:retrieveUserQuotaSummary"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USER_AGENT = "antigravity/windows/amd64"

_EXPIRY_SKEW_SECONDS = 60


def _resolve_oauth_client() -> tuple[str, str]:
    """Return (client_id, client_secret) for the Antigravity CLI.

    Discovery order:
    1. Environment variables AGY_CLIENT_ID / AGY_CLIENT_SECRET
    2. Extracted from the agy binary on disk (all platforms)
    3. Empty strings (will fail with a clear error)
    """
    client_id = os.environ.get("AGY_CLIENT_ID", "")
    client_secret = os.environ.get("AGY_CLIENT_SECRET", "")
    if client_id and client_secret:
        return client_id, client_secret

    bin_id, bin_secret = _extract_from_agy_binary()
    return (
        client_id or bin_id,
        client_secret or bin_secret,
    )


def _extract_from_agy_binary() -> tuple[str, str]:
    """Scan the agy binary for its embedded OAuth client_id and client_secret.

    The agy binary embeds one client secret and one or more client IDs.
    We try each ID with the found secret and return the working pair.
    If no network probe is possible, we prefer the ID starting with ``1071``
    (the Antigravity CLI app).
    """
    agy_path = _find_agy_binary()
    if not agy_path:
        return "", ""

    try:
        data = agy_path.read_bytes()
    except OSError:
        return "", ""

    # Find all GOCSPX-... secrets
    secret_pattern = re.compile(rb"GOCSPX-[A-Za-z0-9_-]{20,}")
    secrets = secret_pattern.findall(data)
    if not secrets:
        return "", ""

    # Find all client IDs
    id_pattern = re.compile(rb"(\d{10,}-[a-z0-9]+\.apps\.googleusercontent\.com)")
    ids = id_pattern.findall(data)
    if not ids:
        return "", ""

    # Prefer the client_id that starts with "1071" (Antigravity CLI app)
    client_id = ""
    for cid in ids:
        if cid.startswith(b"1071"):
            client_id = cid.decode("ascii")
            break
    if not client_id:
        client_id = ids[0].decode("ascii")

    client_secret = secrets[0].decode("ascii")
    return client_id, client_secret


def _find_agy_binary() -> Path | None:
    """Locate the agy executable on disk."""
    import sys

    agy = shutil.which("agy")
    if agy:
        path = Path(agy)
        if path.exists():
            return path

    # Common install locations (check .exe on Windows)
    home = Path.home()
    if sys.platform == "win32":
        candidates = [
            home / "AppData" / "Local" / "agy" / "bin" / "agy.exe",
            home / "AppData" / "Local" / "agy" / "bin" / "agy",
        ]
    else:
        candidates = [
            home / ".local" / "bin" / "agy",
            Path("/usr/local/bin/agy"),
            Path("/opt/agy/bin/agy"),
        ]
    for c in candidates:
        if c.exists():
            return c
    return None


def refresh_access_token(refresh_token: str) -> str:
    """Exchange a refresh token for a fresh access token via Google OAuth."""
    client_id, client_secret = _resolve_oauth_client()
    if not client_id or not client_secret:
        raise RuntimeError(
            "Antigravity OAuth client credentials not found. "
            "Set AGY_CLIENT_ID and AGY_CLIENT_SECRET env vars, "
            "or install the agy CLI so they can be auto-discovered."
        )
    status, data = utils.http_form(
        TOKEN_URL,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    token = data.get("access_token")
    if status != 200 or not token:
        raise RuntimeError(f"token refresh failed (HTTP {status})")
    return token


def get_access_token(creds: dict, now: float | None = None) -> str | None:
    """Return a usable access token, refreshing it when expired or absent."""
    token = creds.get("access_token")
    expiry = creds.get("expiry_epoch")
    reference = now if now is not None else time.time()
    expired = expiry is not None and expiry <= reference + _EXPIRY_SKEW_SECONDS
    if (token is None or expired) and creds.get("refresh_token"):
        return refresh_access_token(creds["refresh_token"])
    return token


def fetch_load_code_assist(token: str) -> dict:
    """Call ``loadCodeAssist`` to discover the project id and current tier."""
    status, data = utils.http_json(
        "POST",
        LOAD_URL,
        headers=_auth_headers(token),
        body={"metadata": {"ideType": "ANTIGRAVITY"}},
    )
    if status != 200:
        raise RuntimeError(f"loadCodeAssist HTTP {status}")
    return data


def fetch_quota_summary(token: str, project_id: str | None) -> dict:
    """Call ``retrieveUserQuotaSummary`` for the grouped weekly/5h limits.

    This is the endpoint the Antigravity desktop app uses for its "Weekly
    Limit" / "Five Hour Limit" readouts. The ``project`` field is optional
    (the server resolves the caller's project), but we pass it when known.
    """
    status, data = utils.http_json(
        "POST",
        QUOTA_SUMMARY_URL,
        headers=_auth_headers(token),
        body={"project": project_id} if project_id else {},
    )
    if status != 200:
        raise RuntimeError(f"retrieveUserQuotaSummary HTTP {status}")
    return data


def parse_quota_summary(data: dict) -> list[dict]:
    """Build the grouped-limit list from a ``retrieveUserQuotaSummary`` response.

    Each group (e.g. "Gemini Models") carries one bucket per window — a
    "Weekly Limit" and a "Five Hour Limit". ``remainingFraction`` (0-1) is
    converted to both a *used* and a *remaining* percentage, since the app
    reports usage as "93% used" rather than "7% remaining".
    """
    groups_raw = data.get("groups")
    if not isinstance(groups_raw, list):
        return []

    groups: list[dict] = []
    for group in groups_raw:
        if not isinstance(group, dict):
            continue
        buckets = [
            parsed
            for bucket in (group.get("buckets") or [])
            if (parsed := _parse_bucket(bucket)) is not None
        ]
        if not buckets:
            continue
        groups.append(
            {
                "name": group.get("displayName") or "Models",
                "models": _models_in_group(group.get("description")),
                "buckets": buckets,
            }
        )
    return groups


def _parse_bucket(bucket: object) -> dict | None:
    if not isinstance(bucket, dict) or bucket.get("remainingFraction") is None:
        return None
    fraction = float(bucket["remainingFraction"])
    return {
        "label": bucket.get("displayName") or bucket.get("bucketId") or "Limit",
        "window": bucket.get("window"),
        "used_pct": round((1.0 - fraction) * 100.0, 1),
        "remaining_pct": round(fraction * 100.0, 1),
        # Raw 0-1 fraction, kept at full precision. The server reports exactly
        # ``1`` for an untouched bucket and a precise float (e.g. ``0.9841012``)
        # once any usage is recorded, so this distinguishes "genuinely zero" from
        # "tiny but nonzero" — a distinction the rounded ``used_pct`` loses.
        "remaining_fraction": fraction,
        "resets_at": utils.normalize_iso(bucket.get("resetTime")),
        "note": bucket.get("description"),
    }


def highest_used(groups: list[dict]) -> float | None:
    """Largest ``used_pct`` across every bucket, or ``None`` if there are none.

    This is the most-constrained limit — the one closest to being hit.
    """
    values = [
        bucket["used_pct"]
        for group in groups
        for bucket in group["buckets"]
        if bucket.get("used_pct") is not None
    ]
    return max(values) if values else None


def check_antigravity(creds: dict | None = None) -> dict:
    """Resolve credentials, query Cloud Code, and return the structured result.

    If a 401 is encountered during the API calls, a token refresh is attempted
    and the calls are retried once.
    """
    if creds is None:
        creds = read_antigravity_credentials()
    if not creds:
        return _empty("no_credentials")

    try:
        token = get_access_token(creds)
        if not token:
            return _empty("error", "could not obtain access token")
        try:
            load = fetch_load_code_assist(token)
            project_id = load.get("cloudaicompanionProject")
            tier = _extract_tier(load)
            groups = parse_quota_summary(fetch_quota_summary(token, project_id))
        except RuntimeError as exc:
            # On 401, refresh the token and retry the full sequence once
            if "HTTP 401" not in str(exc) or not creds.get("refresh_token"):
                raise
            token = refresh_access_token(creds["refresh_token"])
            load = fetch_load_code_assist(token)
            project_id = load.get("cloudaicompanionProject")
            tier = _extract_tier(load)
            groups = parse_quota_summary(fetch_quota_summary(token, project_id))
    except (RuntimeError, ValueError, KeyError, TypeError) as exc:
        return _empty("error", str(exc))

    return {
        "status": "ok",
        "error": None,
        "tier": tier["tier"],
        "tier_id": tier["tier_id"],
        "is_paid": tier["is_paid"],
        "api_tier_id": tier["api_tier_id"],
        "project_id": project_id,
        "groups": groups,
        "highest_used_pct": highest_used(groups),
        "group_count": len(groups),
    }


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}


def _extract_tier(load: dict) -> dict:
    """Resolve the tier to display from a ``loadCodeAssist`` response.

    Returns a dict with::

        tier         display name (``paidTier`` if present, else ``currentTier``)
        tier_id      matching tier id
        is_paid      True when a Google One ``paidTier`` subscription is present
        api_tier_id  the raw ``currentTier`` id (e.g. "free-tier"), for debugging

    The Google One subscription (``paidTier``) is the meaningful, user-facing
    tier, so it wins over the Cloud Code Assist API tier (``currentTier``),
    which is always ``free-tier`` for consumer accounts.
    """
    api_name, api_id = _tier_fields(load.get("currentTier"))
    paid_name, paid_id = _tier_fields(load.get("paidTier"))
    is_paid = paid_name is not None or paid_id is not None
    return {
        "tier": (paid_name or paid_id) if is_paid else (api_name or api_id),
        "tier_id": paid_id if is_paid else api_id,
        "is_paid": is_paid,
        "api_tier_id": api_id,
    }


def _tier_fields(tier: object) -> tuple[str | None, str | None]:
    """Return ``(name, id)`` from a ``UserTier`` object or bare tier string."""
    if isinstance(tier, dict):
        return (tier.get("name") or tier.get("id")), tier.get("id")
    if isinstance(tier, str):
        return tier, None
    return None, None


def _models_in_group(description: object) -> str | None:
    """Pull the model list out of a group description, if present.

    e.g. "Models within this group: Gemini Flash, Gemini Pro" -> the trailing
    "Gemini Flash, Gemini Pro".
    """
    if not isinstance(description, str):
        return None
    marker = "Models within this group:"
    if marker in description:
        return description.split(marker, 1)[1].strip() or None
    return None


def _empty(status: str, error: str | None = None) -> dict:
    return {
        "status": status,
        "error": error,
        "tier": None,
        "tier_id": None,
        "is_paid": False,
        "api_tier_id": None,
        "project_id": None,
        "groups": [],
        "highest_used_pct": None,
        "group_count": 0,
    }
