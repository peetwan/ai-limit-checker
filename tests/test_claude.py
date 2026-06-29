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
