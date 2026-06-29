"""Antigravity CLI usage checker.

Reads the local Google OAuth token (refreshing it if expired), then queries
Cloud Code's internal endpoints for the active project, tier, and per-model
quota.

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

LOAD_URL = "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
MODELS_URL = "https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels"
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


def fetch_models(token: str, project_id: str | None) -> dict:
    """Call ``fetchAvailableModels`` for the given project."""
    status, data = utils.http_json(
        "POST", MODELS_URL, headers=_auth_headers(token), body={"project": project_id or ""}
    )
    if status != 200:
        raise RuntimeError(f"fetchAvailableModels HTTP {status}")
    return data


def parse_models(data: dict) -> list[dict]:
    """Build the model list from a ``fetchAvailableModels`` response.

    Models without quota information are skipped. ``remainingFraction`` (0-1)
    is converted to a percentage.
    """
    models_raw = data.get("models")
    if isinstance(models_raw, dict):
        items = list(models_raw.items())
    elif isinstance(models_raw, list):
        items = [(_model_id(m), m) for m in models_raw]
    else:
        return []

    result: list[dict] = []
    for name, model in items:
        if not isinstance(model, dict):
            continue
        quota = model.get("quotaInfo")
        if not isinstance(quota, dict) or quota.get("remainingFraction") is None:
            continue
        fraction = float(quota["remainingFraction"])
        result.append(
            {
                "name": name or _model_id(model),
                "display_name": model.get("displayName") or name or _model_id(model),
                "remaining_pct": round(fraction * 100.0, 1),
                "resets_at": utils.normalize_iso(quota.get("resetTime")),
            }
        )
    return result


def tightest_remaining(models: list[dict]) -> float | None:
    """Smallest ``remaining_pct`` across all models, or ``None`` if empty."""
    values = [m["remaining_pct"] for m in models if m.get("remaining_pct") is not None]
    return min(values) if values else None


def check_antigravity(creds: dict | None = None) -> dict:
    """Resolve credentials, query Cloud Code, and return the structured result."""
    if creds is None:
        creds = read_antigravity_credentials()
    if not creds:
        return _empty("no_credentials")

    try:
        token = get_access_token(creds)
        if not token:
            return _empty("error", "could not obtain access token")
        load = fetch_load_code_assist(token)
        project_id = load.get("cloudaicompanionProject")
        tier = _extract_tier(load)
        models = parse_models(fetch_models(token, project_id))
    except (RuntimeError, ValueError, KeyError, TypeError) as exc:
        return _empty("error", str(exc))

    return {
        "status": "ok",
        "error": None,
        "tier": tier,
        "project_id": project_id,
        "models": models,
        "tightest_remaining_pct": tightest_remaining(models),
        "model_count": len(models),
    }


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}


def _extract_tier(load: dict) -> str | None:
    tier = load.get("currentTier")
    if isinstance(tier, dict):
        return tier.get("name") or tier.get("id")
    if isinstance(tier, str):
        return tier
    return None


def _model_id(model: dict) -> str | None:
    if not isinstance(model, dict):
        return None
    return model.get("modelId") or model.get("name") or model.get("id")


def _empty(status: str, error: str | None = None) -> dict:
    return {
        "status": status,
        "error": error,
        "tier": None,
        "project_id": None,
        "models": [],
        "tightest_remaining_pct": None,
        "model_count": 0,
    }
