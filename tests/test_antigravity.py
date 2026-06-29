"""Tests for Antigravity model parsing, token refresh, and the check flow."""

import pytest

from ai_limit_checker import antigravity, utils

MODELS_RESPONSE = {
    "models": {
        "gemini-3.5-flash": {
            "displayName": "Gemini 3.5 Flash",
            "quotaInfo": {"remainingFraction": 1.0, "resetTime": "2026-06-29T09:53Z"},
        },
        "claude-opus-4-5": {
            "displayName": "Claude Opus 4.5",
            "quotaInfo": {"remainingFraction": 0.65, "resetTime": "2026-06-29T18:00Z"},
        },
        "no-quota-model": {"displayName": "No Quota"},
    }
}


def test_parse_models_dict():
    models = antigravity.parse_models(MODELS_RESPONSE)
    assert len(models) == 2  # no-quota-model filtered out
    by_name = {m["name"]: m for m in models}
    assert by_name["gemini-3.5-flash"]["remaining_pct"] == 100.0
    assert by_name["claude-opus-4-5"]["remaining_pct"] == 65.0
    assert by_name["claude-opus-4-5"]["resets_at"] == "2026-06-29T18:00:00Z"  # normalized


def test_parse_models_list():
    data = {
        "models": [
            {"name": "m1", "quotaInfo": {"remainingFraction": 0.5}},
            {"name": "m2"},
        ]
    }
    models = antigravity.parse_models(data)
    assert len(models) == 1
    assert models[0]["name"] == "m1"
    assert models[0]["remaining_pct"] == 50.0


def test_parse_models_empty():
    assert antigravity.parse_models({}) == []
    assert antigravity.parse_models({"models": None}) == []


def test_tightest_remaining():
    models = antigravity.parse_models(MODELS_RESPONSE)
    assert antigravity.tightest_remaining(models) == 65.0
    assert antigravity.tightest_remaining([]) is None


def test_get_access_token_valid_not_refreshed(monkeypatch):
    def boom(_):
        raise AssertionError("refresh must not be called for a valid token")

    monkeypatch.setattr(antigravity, "refresh_access_token", boom)
    creds = {"access_token": "AT", "refresh_token": "RT", "expiry_epoch": 2000.0}
    assert antigravity.get_access_token(creds, now=1000.0) == "AT"


def test_get_access_token_expired_refreshes(monkeypatch):
    monkeypatch.setattr(antigravity, "refresh_access_token", lambda rt: f"NEW:{rt}")
    creds = {"access_token": "OLD", "refresh_token": "RT", "expiry_epoch": 1000.0}
    assert antigravity.get_access_token(creds, now=2000.0) == "NEW:RT"


def test_get_access_token_missing_refreshes(monkeypatch):
    monkeypatch.setattr(antigravity, "refresh_access_token", lambda rt: "NEW")
    creds = {"access_token": None, "refresh_token": "RT", "expiry_epoch": None}
    assert antigravity.get_access_token(creds) == "NEW"


def test_refresh_access_token_ok(monkeypatch):
    monkeypatch.setattr(utils, "http_form", lambda *a, **k: (200, {"access_token": "NEW"}))
    assert antigravity.refresh_access_token("RT") == "NEW"


def test_refresh_access_token_failure(monkeypatch):
    monkeypatch.setattr(utils, "http_form", lambda *a, **k: (400, {"error": "invalid_grant"}))
    with pytest.raises(RuntimeError):
        antigravity.refresh_access_token("RT")


def test_check_antigravity_no_credentials(monkeypatch):
    monkeypatch.setattr(antigravity, "read_antigravity_credentials", lambda: None)
    assert antigravity.check_antigravity()["status"] == "no_credentials"


def test_check_antigravity_ok(monkeypatch):
    monkeypatch.setattr(antigravity, "get_access_token", lambda creds: "TOKEN")
    monkeypatch.setattr(
        antigravity,
        "fetch_load_code_assist",
        lambda token: {
            "cloudaicompanionProject": "melodic-component-26v41",
            "currentTier": {"id": "ultra", "name": "Ultra"},
        },
    )
    monkeypatch.setattr(antigravity, "fetch_models", lambda token, project: MODELS_RESPONSE)
    result = antigravity.check_antigravity(creds={"refresh_token": "RT"})
    assert result["status"] == "ok"
    assert result["tier"] == "Ultra"
    assert result["project_id"] == "melodic-component-26v41"
    assert result["model_count"] == 2
    assert result["tightest_remaining_pct"] == 65.0


def test_check_antigravity_error(monkeypatch):
    def boom(token):
        raise RuntimeError("loadCodeAssist HTTP 503")

    monkeypatch.setattr(antigravity, "get_access_token", lambda creds: "TOKEN")
    monkeypatch.setattr(antigravity, "fetch_load_code_assist", boom)
    result = antigravity.check_antigravity(creds={"refresh_token": "RT"})
    assert result["status"] == "error"
    assert "503" in result["error"]
