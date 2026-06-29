"""Tests for credential discovery and blob parsing (no real secrets touched)."""

import json

from ai_limit_checker import credentials
from ai_limit_checker.utils import parse_iso


def test_parse_windows_blob():
    blob = json.dumps(
        {
            "token": {
                "access_token": "AT",
                "refresh_token": "RT",
                "expiry": "2026-06-29T18:00:00Z",
            },
            "auth_method": "consumer",
        }
    ).encode("utf-8")
    result = credentials.parse_windows_blob(blob)
    assert result["access_token"] == "AT"
    assert result["refresh_token"] == "RT"
    assert result["expiry_epoch"] == parse_iso("2026-06-29T18:00:00Z").timestamp()


def test_parse_gemini_file():
    data = {
        "access_token": "AT",
        "refresh_token": "RT",
        "expiry_date": 1900000000000,
    }
    result = credentials.parse_gemini_file(data)
    assert result["access_token"] == "AT"
    assert result["refresh_token"] == "RT"
    assert result["expiry_epoch"] == 1900000000.0


def test_parse_gemini_file_missing_expiry():
    result = credentials.parse_gemini_file({"access_token": "AT"})
    assert result["expiry_epoch"] is None
    assert result["refresh_token"] is None


def test_read_claude_credentials_from_file(tmp_path, monkeypatch):
    path = tmp_path / ".credentials.json"
    path.write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "AT", "refreshToken": "RT"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(credentials, "claude_credentials_path", lambda: path)
    creds = credentials.read_claude_credentials()
    assert creds == {"accessToken": "AT", "refreshToken": "RT"}


def test_read_claude_credentials_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials, "claude_credentials_path", lambda: tmp_path / "nope.json")
    monkeypatch.setattr(credentials.sys, "platform", "linux")
    assert credentials.read_claude_credentials() is None


def test_read_antigravity_env_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials.sys, "platform", "linux")
    monkeypatch.setattr(credentials, "gemini_credentials_path", lambda: tmp_path / "nope.json")
    monkeypatch.setenv(credentials.ANTIGRAVITY_ENV_TOKEN, "ENV_RT")
    creds = credentials.read_antigravity_credentials()
    assert creds == {"access_token": None, "refresh_token": "ENV_RT", "expiry_epoch": None}


def test_read_antigravity_from_gemini_file(tmp_path, monkeypatch):
    path = tmp_path / "oauth_creds.json"
    path.write_text(
        json.dumps({"access_token": "AT", "refresh_token": "RT", "expiry_date": 1900000000000}),
        encoding="utf-8",
    )
    monkeypatch.setattr(credentials.sys, "platform", "linux")
    monkeypatch.setattr(credentials, "gemini_credentials_path", lambda: path)
    monkeypatch.delenv(credentials.ANTIGRAVITY_ENV_TOKEN, raising=False)
    creds = credentials.read_antigravity_credentials()
    assert creds["access_token"] == "AT"
    assert creds["expiry_epoch"] == 1900000000.0
