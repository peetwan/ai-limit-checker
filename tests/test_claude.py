"""Tests for Claude Code usage parsing and the check flow."""

from ai_limit_checker import claude, utils

SAMPLE_RESPONSE = {
    "five_hour": {"utilization": 1.0, "resets_at": "2026-06-29T09:30:00Z"},
    "seven_day": {"utilization": 56.0, "resets_at": "2026-07-01T22:00:00Z"},
    "seven_day_sonnet": {"utilization": 8.0, "resets_at": "2026-07-01T22:00:00Z"},
}


def test_parse_claude_usage():
    parsed = claude.parse_claude_usage(SAMPLE_RESPONSE)
    assert parsed["five_hour"] == {
        "used_pct": 1.0,
        "remaining_pct": 99.0,
        "resets_at": "2026-06-29T09:30:00Z",
    }
    assert parsed["seven_day"]["used_pct"] == 56.0
    assert parsed["seven_day"]["remaining_pct"] == 44.0
    assert parsed["seven_day_sonnet"]["used_pct"] == 8.0
    assert parsed["seven_day_sonnet"]["remaining_pct"] == 92.0


def test_parse_claude_usage_missing_window():
    parsed = claude.parse_claude_usage({"five_hour": {"utilization": 5.0}})
    assert parsed["seven_day"] is None
    assert parsed["seven_day_sonnet"] is None
    assert parsed["five_hour"]["resets_at"] is None


def test_check_claude_no_credentials():
    assert claude.check_claude(creds={})["status"] == "no_credentials"
    assert claude.check_claude(creds={"refreshToken": "x"})["status"] == "no_credentials"


def test_check_claude_ok(monkeypatch):
    monkeypatch.setattr(utils, "http_json", lambda *a, **k: (200, SAMPLE_RESPONSE))
    result = claude.check_claude(creds={"accessToken": "AT", "subscriptionType": "max"})
    assert result["status"] == "ok"
    assert result["plan"] == "Max"
    assert result["five_hour"]["used_pct"] == 1.0
    assert result["error"] is None


def test_check_claude_http_error(monkeypatch):
    monkeypatch.setattr(utils, "http_json", lambda *a, **k: (429, {}))
    result = claude.check_claude(creds={"accessToken": "AT"})
    assert result["status"] == "error"
    assert "429" in result["error"]


def test_check_claude_401_triggers_refresh_then_retry(monkeypatch):
    """On 401, the token should be refreshed and the request retried once."""
    call_count = {"usage": 0}

    def fake_http_json(method, url, headers=None, **kwargs):
        call_count["usage"] += 1
        # First call returns 401, second call (after refresh) returns 200
        if call_count["usage"] == 1:
            return 401, {}
        return 200, SAMPLE_RESPONSE

    monkeypatch.setattr(utils, "http_json", fake_http_json)
    monkeypatch.setattr(
        claude,
        "refresh_claude_token",
        lambda rt: {"access_token": "NEW_TOKEN"},
    )
    result = claude.check_claude(creds={"accessToken": "OLD", "refreshToken": "RT"})
    assert result["status"] == "ok"
    assert call_count["usage"] == 2


def test_check_claude_401_refresh_fails_returns_error(monkeypatch):
    """If refresh fails on 401, the original error is returned."""
    monkeypatch.setattr(utils, "http_json", lambda *a, **k: (401, {}))
    monkeypatch.setattr(
        claude,
        "refresh_claude_token",
        lambda rt: (_ for _ in ()).throw(RuntimeError("refresh failed")),
    )
    result = claude.check_claude(creds={"accessToken": "OLD", "refreshToken": "RT"})
    assert result["status"] == "error"
    assert "401" in result["error"]


def test_check_claude_proactive_refresh_on_expired_token(monkeypatch):
    """If the token is expired, refresh *before* making the usage request."""
    refresh_called = {"count": 0}

    def fake_refresh(rt):
        refresh_called["count"] += 1
        return {"access_token": "FRESH"}

    monkeypatch.setattr(claude, "refresh_claude_token", fake_refresh)
    monkeypatch.setattr(utils, "http_json", lambda *a, **k: (200, SAMPLE_RESPONSE))

    # expiresAt is in the past → should trigger proactive refresh
    expired_creds = {
        "accessToken": "OLD",
        "refreshToken": "RT",
        "expiresAt": 1_000_000,  # way in the past
    }
    result = claude.check_claude(creds=expired_creds)
    assert result["status"] == "ok"
    assert refresh_called["count"] == 1


def test_check_claude_no_proactive_refresh_on_valid_token(monkeypatch):
    """A token that hasn't expired should not trigger a refresh."""

    def boom(rt):
        raise AssertionError("refresh must not be called for a valid token")

    monkeypatch.setattr(claude, "refresh_claude_token", boom)
    monkeypatch.setattr(utils, "http_json", lambda *a, **k: (200, SAMPLE_RESPONSE))

    import time as _time

    future_ms = int(_time.time() * 1000) + 3_600_000  # 1 hour from now
    valid_creds = {
        "accessToken": "AT",
        "refreshToken": "RT",
        "expiresAt": future_ms,
    }
    result = claude.check_claude(creds=valid_creds)
    assert result["status"] == "ok"


def test_check_claude_only_refresh_token_no_access(monkeypatch):
    """If only a refresh token is available, use it to get an access token."""
    monkeypatch.setattr(
        claude,
        "refresh_claude_token",
        lambda rt: {"access_token": "FROM_REFRESH"},
    )
    captured = {}

    def fake_http_json(method, url, headers=None, **kwargs):
        captured["headers"] = headers
        return 200, SAMPLE_RESPONSE

    monkeypatch.setattr(utils, "http_json", fake_http_json)
    result = claude.check_claude(creds={"refreshToken": "RT"})
    assert result["status"] == "ok"
    assert captured["headers"]["Authorization"] == "Bearer FROM_REFRESH"


def test_refresh_claude_token_ok(monkeypatch):
    monkeypatch.setattr(utils, "http_form", lambda *a, **k: (200, {"access_token": "NEW"}))
    result = claude.refresh_claude_token("RT")
    assert result["access_token"] == "NEW"


def test_refresh_claude_token_failure(monkeypatch):
    monkeypatch.setattr(utils, "http_form", lambda *a, **k: (400, {"error": "invalid_grant"}))
    import pytest

    with pytest.raises(RuntimeError):
        claude.refresh_claude_token("RT")


def test_check_claude_connection_error(monkeypatch):
    monkeypatch.setattr(utils, "http_json", lambda *a, **k: (0, {"raw": "timed out"}))
    result = claude.check_claude(creds={"accessToken": "AT"})
    assert result["status"] == "error"


def test_fetch_claude_usage_sends_user_agent(monkeypatch):
    captured = {}

    def fake_http_json(method, url, headers=None, **kwargs):
        captured["headers"] = headers
        return 200, {}

    monkeypatch.setattr(utils, "http_json", fake_http_json)
    claude.fetch_claude_usage("AT")
    assert captured["headers"]["User-Agent"] == claude.USER_AGENT
    assert captured["headers"]["anthropic-beta"] == claude.OAUTH_BETA
    assert captured["headers"]["Authorization"] == "Bearer AT"
