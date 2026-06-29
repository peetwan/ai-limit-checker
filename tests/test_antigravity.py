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


def test_parse_quota_summary_keeps_raw_fraction():
    # The raw 0-1 fraction is preserved at full precision so callers can tell a
    # genuinely-empty bucket (exactly 1) from a tiny-but-nonzero one.
    groups = antigravity.parse_quota_summary(QUOTA_SUMMARY)
    gemini, third_party = groups
    assert gemini["buckets"][0]["remaining_fraction"] == 1  # untouched bucket
    assert third_party["buckets"][0]["remaining_fraction"] == 0.07
    assert third_party["buckets"][1]["remaining_fraction"] == 0.05


def test_parse_quota_summary_tiny_usage_not_rounded_away():
    # A fraction just under 1 rounds to 0.0% used, but the raw fraction must stay
    # below 1 so the distinction survives to the display layer.
    data = {
        "groups": [
            {
                "displayName": "Gemini Models",
                "buckets": [
                    {"displayName": "Weekly Limit", "window": "weekly", "remainingFraction": 0.9996},
                ],
            }
        ]
    }
    bucket = antigravity.parse_quota_summary(data)[0]["buckets"][0]
    assert bucket["used_pct"] == 0.0  # 0.04% rounds down
    assert bucket["remaining_fraction"] == 0.9996  # but the truth is preserved


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
    assert result["is_paid"] is False
    assert result["api_tier_id"] == "free-tier"
    assert result["project_id"] == "melodic-component-26v41"
    assert result["group_count"] == 2
    assert result["highest_used_pct"] == 95.0


def test_check_antigravity_paid_tier_wins(monkeypatch):
    # A consumer Ultra account: currentTier is always "free-tier", but paidTier
    # carries the real Google One subscription. The subscription must win.
    monkeypatch.setattr(antigravity, "get_access_token", lambda creds: "TOKEN")
    monkeypatch.setattr(
        antigravity,
        "fetch_load_code_assist",
        lambda token: {
            "cloudaicompanionProject": "melodic-component-26v41",
            "currentTier": {"id": "free-tier", "name": "Antigravity"},
            "paidTier": {"id": "g1-ultra-lite-tier", "name": "Google AI Ultra"},
        },
    )
    monkeypatch.setattr(antigravity, "fetch_quota_summary", lambda token, project: QUOTA_SUMMARY)
    result = antigravity.check_antigravity(creds={"refresh_token": "RT"})
    assert result["tier"] == "Google AI Ultra"
    assert result["tier_id"] == "g1-ultra-lite-tier"
    assert result["is_paid"] is True
    assert result["api_tier_id"] == "free-tier"  # raw API tier still surfaced


def test_extract_tier_free_only():
    tier = antigravity._extract_tier({"currentTier": {"id": "free-tier", "name": "Antigravity"}})
    assert tier == {
        "tier": "Antigravity",
        "tier_id": "free-tier",
        "is_paid": False,
        "api_tier_id": "free-tier",
    }


def test_extract_tier_prefers_paid():
    tier = antigravity._extract_tier(
        {
            "currentTier": {"id": "free-tier", "name": "Antigravity"},
            "paidTier": {"id": "g1-ultra-lite-tier", "name": "Google AI Ultra"},
        }
    )
    assert tier["tier"] == "Google AI Ultra"
    assert tier["tier_id"] == "g1-ultra-lite-tier"
    assert tier["is_paid"] is True
    assert tier["api_tier_id"] == "free-tier"


def test_extract_tier_empty_paid_ignored():
    # An empty paidTier object must not be treated as a subscription.
    tier = antigravity._extract_tier(
        {"currentTier": {"id": "free-tier", "name": "Antigravity"}, "paidTier": {}}
    )
    assert tier["is_paid"] is False
    assert tier["tier_id"] == "free-tier"


def test_extract_tier_missing():
    tier = antigravity._extract_tier({})
    assert tier == {"tier": None, "tier_id": None, "is_paid": False, "api_tier_id": None}


def test_check_antigravity_error(monkeypatch):
    def boom(token):
        raise RuntimeError("loadCodeAssist HTTP 503")

    monkeypatch.setattr(antigravity, "get_access_token", lambda creds: "TOKEN")
    monkeypatch.setattr(antigravity, "fetch_load_code_assist", boom)
    result = antigravity.check_antigravity(creds={"refresh_token": "RT"})
    assert result["status"] == "error"
    assert "503" in result["error"]
