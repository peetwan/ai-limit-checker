"""Tests for the burn-rate calculator."""

import time

from ai_limit_checker import burn_rate

SAMPLE_DATA = {
    "claude": {
        "status": "ok",
        "five_hour": {"used_pct": 30.0, "remaining_pct": 70.0, "resets_at": "2026-06-29T20:00:00Z"},
        "seven_day": {"used_pct": 58.0, "remaining_pct": 42.0, "resets_at": "2026-07-02T22:00:00Z"},
        "seven_day_sonnet": {
            "used_pct": 8.0,
            "remaining_pct": 92.0,
            "resets_at": "2026-07-02T22:00:00Z",
        },
    },
    "antigravity": {
        "status": "ok",
        "groups": [
            {
                "name": "Gemini Models",
                "buckets": [
                    {
                        "window": "weekly",
                        "label": "Weekly Limit",
                        "used_pct": 7.1,
                        "resets_at": "2026-07-02T03:27:28Z",
                    },
                    {
                        "window": "5h",
                        "label": "Five Hour Limit",
                        "used_pct": 6.5,
                        "resets_at": "2026-06-29T11:31:58Z",
                    },
                ],
            },
        ],
    },
}


def test_extract_windows_claude():
    windows = burn_rate._extract_windows(SAMPLE_DATA)
    assert "claude_five_hour" in windows
    assert "claude_seven_day" in windows
    assert windows["claude_five_hour"]["used_pct"] == 30.0
    assert windows["claude_five_hour"]["label"] == "Claude 5h"


def test_extract_windows_antigravity():
    windows = burn_rate._extract_windows(SAMPLE_DATA)
    assert "agy_Gemini Models_weekly" in windows
    assert "agy_Gemini Models_5h" in windows
    assert windows["agy_Gemini Models_5h"]["used_pct"] == 6.5


def test_extract_windows_skips_error_status():
    data = {"claude": {"status": "error", "error": "HTTP 401"}}
    windows = burn_rate._extract_windows(data)
    assert windows == {}


def test_calculate_burn_rate_insufficient_data():
    history = {
        "claude_five_hour": [
            {"label": "Claude 5h", "used_pct": 30.0, "timestamp": time.time()},
        ],
    }
    rates = burn_rate.calculate_burn_rate(history)
    assert rates["claude_five_hour"]["velocity_pct_per_hour"] is None
    assert rates["claude_five_hour"]["eta_text"] == "insufficient data"
    assert rates["claude_five_hour"]["samples"] == 1


def test_calculate_burn_rate_positive_velocity():
    now = time.time()
    history = {
        "claude_five_hour": [
            {"label": "Claude 5h", "used_pct": 20.0, "timestamp": now - 3600},  # 1h ago
            {"label": "Claude 5h", "used_pct": 40.0, "timestamp": now},  # now
        ],
    }
    rates = burn_rate.calculate_burn_rate(history)
    r = rates["claude_five_hour"]
    assert r["velocity_pct_per_hour"] == 20.0  # 20% increase over 1 hour
    assert r["eta_seconds"] is not None
    assert r["eta_seconds"] > 0
    # 60% remaining / 20% per hour = 3 hours = 10800 seconds
    assert abs(r["eta_seconds"] - 10800) < 100


def test_calculate_burn_rate_negative_velocity():
    now = time.time()
    history = {
        "claude_five_hour": [
            {"label": "Claude 5h", "used_pct": 50.0, "timestamp": now - 3600},
            {"label": "Claude 5h", "used_pct": 30.0, "timestamp": now},  # usage went down (reset)
        ],
    }
    rates = burn_rate.calculate_burn_rate(history)
    r = rates["claude_five_hour"]
    assert r["velocity_pct_per_hour"] == -20.0
    assert r["eta_seconds"] is None
    assert r["eta_text"] == "not increasing"


def test_calculate_burn_rate_zero_velocity():
    now = time.time()
    history = {
        "claude_five_hour": [
            {"label": "Claude 5h", "used_pct": 30.0, "timestamp": now - 3600},
            {"label": "Claude 5h", "used_pct": 30.0, "timestamp": now},
        ],
    }
    rates = burn_rate.calculate_burn_rate(history)
    r = rates["claude_five_hour"]
    assert r["velocity_pct_per_hour"] == 0.0
    assert r["eta_seconds"] is None


def test_calculate_burn_rate_uses_last_10_samples():
    """When more than 10 samples exist, only the last 10 should be used."""
    now = time.time()
    # Create 20 samples: first 10 are 0%, last 10 go from 10% to 100%
    snaps = []
    for i in range(20):
        if i < 10:
            pct = 0.0
            ts = now - (20 - i) * 60
        else:
            pct = float((i - 10) * 10)  # 0, 10, 20, ... 90
            ts = now - (20 - i) * 60
        snaps.append({"label": "Claude 5h", "used_pct": pct, "timestamp": ts})

    history = {"claude_five_hour": snaps}
    rates = burn_rate.calculate_burn_rate(history)
    # The last sample should be used_pct=90
    assert rates["claude_five_hour"]["used_pct"] == 90.0
    assert rates["claude_five_hour"]["samples"] == 20


def test_record_snapshot_appends_and_trims(tmp_path, monkeypatch):
    """record_snapshot should append to history and trim to MAX_HISTORY."""
    monkeypatch.setattr(burn_rate, "HISTORY_FILE", tmp_path / "burn_rate.json")
    monkeypatch.setattr(burn_rate, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(burn_rate, "MAX_HISTORY_PER_WINDOW", 3)

    # Record 5 snapshots — only last 3 should be kept
    for _ in range(5):
        burn_rate.record_snapshot(data=SAMPLE_DATA)

    history = burn_rate._load_history()
    for snaps in history.values():
        assert len(snaps) <= 3


def test_format_burn_rate_empty():
    text = burn_rate.format_burn_rate({})
    assert "No burn-rate data" in text


def test_format_burn_rate_with_data():
    rates = {
        "claude_five_hour": {
            "label": "Claude 5h",
            "used_pct": 40.0,
            "velocity_pct_per_hour": 20.0,
            "eta_seconds": 10800,
            "eta_text": "3h 0m",
            "samples": 5,
        },
    }
    text = burn_rate.format_burn_rate(rates)
    assert "Claude 5h" in text
    assert "40.0%" in text
    assert "+20.0%/h" in text
    assert "3h 0m" in text


def test_get_burn_rate_no_fresh_uses_history(tmp_path, monkeypatch):
    """get_burn_rate(fresh=False) should not call gather, only use existing history."""
    monkeypatch.setattr(burn_rate, "HISTORY_FILE", tmp_path / "burn_rate.json")
    monkeypatch.setattr(burn_rate, "CACHE_DIR", tmp_path)

    # Pre-populate history
    now = time.time()
    history = {
        "claude_five_hour": [
            {"label": "Claude 5h", "used_pct": 20.0, "timestamp": now - 3600},
            {"label": "Claude 5h", "used_pct": 40.0, "timestamp": now},
        ],
    }
    burn_rate._save_history(history)

    rates = burn_rate.get_burn_rate(fresh=False)
    assert "claude_five_hour" in rates
    assert rates["claude_five_hour"]["velocity_pct_per_hour"] == 20.0
