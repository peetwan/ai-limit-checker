"""Tests for Antigravity quota parsing, token refresh, and the check flow."""

import pytest

from ai_limit_checker import antigravity, utils

# Shape mirrors a real ``retrieveUserQuotaSummary`` response: model groups, each
# with a weekly and a five-hour bucket. ``remainingFraction`` is 0-1.
QUOTA_SUMMARY = {
    "groups": [
        {
            "displayName": "Gemini Models",
            "description": "Models within this group: Gemini Flash, Gemini Pro",
            "buckets": [
                {
                    "bucketId": "gemini-weekly",
                    "displayName": "Weekly Limit",
                    "window": "weekly",
                    "resetTime": "2026-07-06T06:28:57Z",
                    "remainingFraction": 1,
                },
                {
                    "bucketId": "gemini-5h",
                    "displayName": "Five Hour Limit",
                    "window": "5h",
                    "resetTime": "2026-06-29T11:28:57Z",
                    "remainingFraction": 1,
                },
            ],
        },
        {
            "displayName": "Claude and GPT models",
            "description": "Models within this group: Claude Opus, Claude Sonnet, GPT-OSS",
            "buckets": [
                {
                    "bucketId": "3p-weekly",
                    "displayName": "Weekly Limit",
                    "window": "weekly",
                    "resetTime": "2026-07-02T03:28:55Z",
                    "description": "You have used some of your weekly limit.",
                    "remainingFraction": 0.07,
                },
                {
                    "bucketId": "3p-5h",
                    "displayName": "Five Hour Limit",
                    "window": "5h",
                    "resetTime": "2026-06-29T11:28:57Z",
                    "remainingFraction": 0.05,
                },
            ],
        },
    ]
}


def test_parse_quota_summary_groups():
    groups = antigravity.parse_quota_summary(QUOTA_SUMMARY)
    assert len(groups) == 2
    gemini, third_party = groups
    assert gemini["name"] == "Gemini Models"
    assert gemini["models"] == "Gemini Flash, Gemini Pro"
    assert len(gemini["buckets"]) == 2


def test_parse_quota_summary_used_pct():
    groups = antigravity.parse_quota_summary(QUOTA_SUMMARY)
    third_party = groups[1]
    weekly = third_party["buckets"][0]
    assert weekly["label"] == "Weekly Limit"
    assert weekly["window"] == "weekly"
    assert weekly["used_pct"] == 93.0  # 1 - 0.07
    assert weekly["remaining_pct"] == 7.0
    assert weekly["resets_at"] == "2026-07-02T03:28:55Z"  # normalized
    assert weekly["note"] == "You have used some of your weekly limit."
    five_hour = third_party["buckets"][1]
    assert five_hour["used_pct"] == 95.0  # 1 - 0.05


def test_parse_quota_summary_empty():
    assert antigravity.parse_quota_summary({}) == []
    assert antigravity.parse_quota_summary({"groups": None}) == []
    # Buckets without remainingFraction are skipped; empty groups dropped.
    assert antigravity.parse_quota_summary({"groups": [{"buckets": [{"window": "5h"}]}]}) == []


def test_highest_used():
    groups = antigravity.parse_quota_summary(QUOTA_SUMMARY)
    assert antigravity.highest_used(groups) == 95.0  # 3p five-hour, most constrained
    assert antigravity.highest_used([]) is None


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
            "currentTier": {"id": "free-tier", "name": "Antigravity"},
        },
    )
    monkeypatch.setattr(antigravity, "fetch_quota_summary", lambda token, project: QUOTA_SUMMARY)
    result = antigravity.check_antigravity(creds={"refresh_token": "RT"})
    assert result["status"] == "ok"
    assert result["tier"] == "Antigravity"
    assert result["tier_id"] == "free-tier"
    assert result["project_id"] == "melodic-component-26v41"
    assert result["group_count"] == 2
    assert result["highest_used_pct"] == 95.0


def test_check_antigravity_error(monkeypatch):
    def boom(token):
        raise RuntimeError("loadCodeAssist HTTP 503")

    monkeypatch.setattr(antigravity, "get_access_token", lambda creds: "TOKEN")
    monkeypatch.setattr(antigravity, "fetch_load_code_assist", boom)
    result = antigravity.check_antigravity(creds={"refresh_token": "RT"})
    assert result["status"] == "error"
    assert "503" in result["error"]
