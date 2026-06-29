"""Cross-platform credential discovery for Claude Code and Antigravity CLI.

Tokens are only ever read here and passed to the official API endpoints. They
are never printed, logged, or written back out.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .utils import parse_iso

# --- Claude Code -----------------------------------------------------------

CLAUDE_KEYCHAIN_SERVICE = "Claude Code-credentials"


def claude_credentials_path() -> Path:
    """Path to the Claude Code credentials file (``~/.claude/.credentials.json``)."""
    return Path.home() / ".claude" / ".credentials.json"


def read_claude_credentials() -> dict | None:
    """Return the ``claudeAiOauth`` block, or ``None`` if unavailable.

    Primary source is the JSON file; on macOS the Keychain is tried as a
    fallback.
    """
    path = claude_credentials_path()
    data = _read_json_file(path)
    if isinstance(data, dict) and isinstance(data.get("claudeAiOauth"), dict):
        return data["claudeAiOauth"]
    if sys.platform == "darwin":
        return _read_claude_keychain()
    return None


def _read_claude_keychain() -> dict | None:
    raw = _macos_keychain_secret(CLAUDE_KEYCHAIN_SERVICE)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if isinstance(data, dict):
        return data.get("claudeAiOauth", data)
    return None


# --- Antigravity CLI -------------------------------------------------------

ANTIGRAVITY_TARGET = "gemini:antigravity"
ANTIGRAVITY_ENV_TOKEN = "ANTIGRAVITY_REFRESH_TOKEN"


def gemini_credentials_path() -> Path:
    """Path to the Antigravity/Gemini OAuth file on Linux/macOS."""
    return Path.home() / ".gemini" / "oauth_creds.json"


def read_antigravity_credentials() -> dict | None:
    """Discover Antigravity credentials as a normalized token dict.

    The returned shape is::

        {"access_token": str | None,
         "refresh_token": str | None,
         "expiry_epoch": float | None}

    Discovery order: Windows Credential Manager (Windows only) →
    ``~/.gemini/oauth_creds.json`` → ``ANTIGRAVITY_REFRESH_TOKEN`` env var.
    """
    if sys.platform == "win32":
        cred = _read_windows_credential(ANTIGRAVITY_TARGET)
        if cred:
            return cred
    else:
        data = _read_json_file(gemini_credentials_path())
        if isinstance(data, dict):
            return parse_gemini_file(data)

    env_token = os.environ.get(ANTIGRAVITY_ENV_TOKEN)
    if env_token:
        return {"access_token": None, "refresh_token": env_token, "expiry_epoch": None}
    return None


def parse_windows_blob(blob: bytes) -> dict:
    """Parse the UTF-8 JSON CredentialBlob stored under ``gemini:antigravity``."""
    data = json.loads(blob.decode("utf-8"))
    token = data.get("token", {}) if isinstance(data, dict) else {}
    return {
        "access_token": token.get("access_token"),
        "refresh_token": token.get("refresh_token"),
        "expiry_epoch": _expiry_to_epoch(token.get("expiry")),
    }


def parse_gemini_file(data: dict) -> dict:
    """Normalize the Linux/macOS ``oauth_creds.json`` structure."""
    return {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "expiry_epoch": _ms_to_epoch(data.get("expiry_date")),
    }


def _read_windows_credential(target: str) -> dict | None:
    """Read a generic credential from the Windows Credential Manager via ctypes."""
    import ctypes
    import ctypes.wintypes as wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = (  # noqa: RUF012 (ctypes layout, not a mutable default)
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        )

    advapi32 = ctypes.windll.advapi32
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(CREDENTIAL)),
    ]

    cred_ptr = ctypes.POINTER(CREDENTIAL)()
    cred_type_generic = 1
    if not advapi32.CredReadW(target, cred_type_generic, 0, ctypes.byref(cred_ptr)):
        return None
    try:
        cred = cred_ptr.contents
        blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
    finally:
        advapi32.CredFree(cred_ptr)
    try:
        return parse_windows_blob(blob)
    except ValueError:
        return None


def _expiry_to_epoch(expiry: str | None) -> float | None:
    dt = parse_iso(expiry)
    return dt.timestamp() if dt else None


def _ms_to_epoch(ms: float | int | None) -> float | None:
    if ms is None:
        return None
    try:
        return float(ms) / 1000.0
    except (TypeError, ValueError):
        return None


def _read_json_file(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _macos_keychain_secret(service: str) -> str | None:
    try:
        result = subprocess.run(  # noqa: S603,S607 (fixed, trusted argv)
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
