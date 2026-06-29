"""Tests for watch mode — 5h reset detection and state tracking."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ai_limit_checker import watch

SAMPLE_AICHECKER = {
    "claude": {
        "status": "ok",
        "five_hour": {
            "used_pct": 30.0,
            "remaining_pct": 70.0,
            "resets_at": "2026-06-29T09:30:00Z",
        },
        "seven_day": {
            "used_pct": 55.0,
            "remaining_pct": 45.0,
            "resets_at": "2026-07-01T22:00:00Z",
        },
        "seven_day_sonnet": None,
        "plan": "Max",
        "error": None,
    },
    "antigravity": {
        "status": "ok",
        "groups": [
            {
                "name": "Gemini Models",
                "buckets": [
                    {
                        "label": "Weekly Limit",
                        "window": "weekly",
                        "used_pct": 7.5,
                        "remaining_pct": 92.5,
                        "remaining_fraction": 0.925,
                        "resets_at": "2026-07-02T03:27:00Z",
                    },
                    {
                        "label": "Five Hour Limit",
                        "window": "5h",
                        "used_pct": 6.5,
                        "remaining_pct": 93.5,
                        "remaining_fraction": 0.935,
                        "resets_at": "2026-06-29T11:31:00Z",
                    },
                ],
            },
            {
                "name": "Claude and GPT models",
                "buckets": [
                    {
                        "label": "Weekly Limit",
                        "window": "weekly",
                        "used_pct": 1.6,
                        "remaining_pct": 98.4,
                        "remaining_fraction": 0.984,
                        "resets_at": "2026-07-02T03:28:00Z",
                    },
                    {
                        "label": "Five Hour Limit",
                        "window": "5h",
                        "used_pct": 0.0,
                        "remaining_pct": 100.0,
                        "remaining_fraction": 1.0,
                        "resets_at": "2026-06-29T12:30:00Z",
                    },
                ],
            },
        ],
        "highest_used_pct": 7.5,
        "tier": "Google AI Ultra",
        "tier_id": "g1-ultra-lite-tier",
        "project_id": "test-project",
        "group_count": 2,
        "error": None,
    },
}


def test_collect_5h_windows():
    windows = watch.collect_5h_windows(SAMPLE_AICHECKER)
    assert "claude_5h" in windows
    assert windows["claude_5h"]["used_pct"] == 30.0
    assert "agy_Gemini Models_5h" in windows
    assert windows["agy_Gemini Models_5h"]["used_pct"] == 6.5
    # Claude+GPT 5h is 0% used but still collected (has resets_at)
    assert "agy_Claude and GPT models_5h" in windows


def test_collect_5h_windows_empty():
    windows = watch.collect_5h_windows(
        {"claude": {"status": "error"}, "antigravity": {"status": "error"}}
    )
    assert windows == {}


def test_collect_5h_windows_no_claude():
    data = {"antigravity": {"status": "ok", "groups": []}}
    assert watch.collect_5h_windows(data) == {}


def test_check_resets_no_prior_usage():
    """Windows with 0% used before should not trigger."""
    current = {
        "claude_5h": {
            "label": "Claude Code 5h",
            "resets_at": "2026-06-29T09:30:00Z",
            "used_pct": 0.0,
        }
    }
    state = {
        "claude_5h": {
            "label": "Claude Code 5h",
            "resets_at": "2026-06-29T09:30:00Z",
            "used_pct": 0.0,
        }
    }
    now = datetime(2026, 6, 29, 9, 35, tzinfo=timezone.utc)
    assert watch.check_resets(current, state, now=now) == []


def test_check_resets_reset_detected():
    """Window with prior usage past reset_time + delay should trigger."""
    current = {
        "claude_5h": {
            "label": "Claude Code 5h",
            "resets_at": "2026-06-29T09:30:00Z",
            "used_pct": 0.0,  # now reset
        }
    }
    state = {
        "claude_5h": {
            "label": "Claude Code 5h",
            "resets_at": "2026-06-29T09:30:00Z",
            "used_pct": 30.0,  # was used before
        }
    }
    now = datetime(2026, 6, 29, 9, 33, tzinfo=timezone.utc)  # 3 min after reset
    resets = watch.check_resets(current, state, now=now, delay=120)
    assert len(resets) == 1
    assert "Claude Code 5h" in resets[0]


def test_check_resets_before_reset_time():
    """Should not trigger if before reset_time + delay."""
    current = {
        "claude_5h": {
            "label": "Claude Code 5h",
            "resets_at": "2026-06-29T09:30:00Z",
            "used_pct": 0.0,
        }
    }
    state = {
        "claude_5h": {
            "label": "Claude Code 5h",
            "resets_at": "2026-06-29T09:30:00Z",
            "used_pct": 30.0,
        }
    }
    now = datetime(2026, 6, 29, 9, 29, tzinfo=timezone.utc)  # before reset
    assert watch.check_resets(current, state, now=now, delay=120) == []


def test_check_resets_within_delay():
    """Should not trigger if within delay window."""
    current = {
        "claude_5h": {
            "label": "Claude Code 5h",
            "resets_at": "2026-06-29T09:30:00Z",
            "used_pct": 0.0,
        }
    }
    state = {
        "claude_5h": {
            "label": "Claude Code 5h",
            "resets_at": "2026-06-29T09:30:00Z",
            "used_pct": 30.0,
        }
    }
    now = datetime(2026, 6, 29, 9, 31, tzinfo=timezone.utc)  # 1 min after, delay=120
    assert watch.check_resets(current, state, now=now, delay=120) == []


def test_check_resets_multiple_windows():
    """Multiple windows can reset simultaneously."""
    reset_time = "2026-06-29T09:30:00Z"
    current = {
        "claude_5h": {"label": "Claude Code 5h", "resets_at": reset_time, "used_pct": 0},
        "agy_Gemini Models_5h": {
            "label": "Antigravity Gemini 5h",
            "resets_at": reset_time,
            "used_pct": 0,
        },
    }
    state = {
        "claude_5h": {"label": "Claude Code 5h", "resets_at": reset_time, "used_pct": 30},
        "agy_Gemini Models_5h": {
            "label": "Antigravity Gemini 5h",
            "resets_at": reset_time,
            "used_pct": 7,
        },
    }
    now = datetime(2026, 6, 29, 9, 35, tzinfo=timezone.utc)
    resets = watch.check_resets(current, state, now=now, delay=120)
    assert len(resets) == 2


def test_check_resets_no_state():
    """No prior state = no reset detected (first run just records)."""
    current = {
        "claude_5h": {"label": "Claude Code 5h", "resets_at": "2026-06-29T09:30:00Z", "used_pct": 0}
    }
    now = datetime(2026, 6, 29, 9, 35, tzinfo=timezone.utc)
    assert watch.check_resets(current, {}, now=now) == []


def test_check_resets_custom_delay():
    """Custom delay affects trigger timing."""
    current = {"claude_5h": {"label": "C", "resets_at": "2026-06-29T09:30:00Z", "used_pct": 0}}
    state = {"claude_5h": {"label": "C", "resets_at": "2026-06-29T09:30:00Z", "used_pct": 30}}
    # 30s after reset, delay=60 → not triggered
    now = datetime(2026, 6, 29, 9, 30, 30, tzinfo=timezone.utc)
    assert watch.check_resets(current, state, now=now, delay=60) == []
    # 61s after reset, delay=60 → triggered
    now = datetime(2026, 6, 29, 9, 31, 1, tzinfo=timezone.utc)
    assert len(watch.check_resets(current, state, now=now, delay=60)) == 1


def test_watch_once_with_callback(tmp_path, monkeypatch):
    """--once mode calls callback and exits."""
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(watch, "STATE_FILE", state_file)
    monkeypatch.setattr(watch, "STATE_DIR", tmp_path)

    # Pre-populate state with a past reset
    state = {
        "claude_5h": {
            "label": "Claude Code 5h",
            "resets_at": "2026-06-29T07:00:00Z",
            "used_pct": 30.0,
        }
    }
    state_file.write_text(json.dumps(state))

    called_with: list[list[str]] = []

    def fake_gather(**kwargs):
        return SAMPLE_AICHECKER

    monkeypatch.setattr(watch, "gather", fake_gather)

    watch.watch_5h_resets(on_reset=lambda labels: called_with.append(labels), once=True)

    assert len(called_with) == 1
    # Claude 5h in state had 30% used, reset at 07:00, current time is way past
    # But SAMPLE_AICHECKER shows Claude 5h reset at 09:30Z — the state has 07:00Z
    # The reset detection uses the PREVIOUS state's reset time (07:00Z) which has passed
    assert any("Claude" in label for labels in called_with for label in labels)


def test_watch_once_silent_no_reset(tmp_path, monkeypatch):
    """--once mode is silent when no reset has occurred."""
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(watch, "STATE_FILE", state_file)
    monkeypatch.setattr(watch, "STATE_DIR", tmp_path)

    # State with future reset time
    future = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    state = {"claude_5h": {"label": "Claude Code 5h", "resets_at": future, "used_pct": 30.0}}
    state_file.write_text(json.dumps(state))

    called = []

    def fake_gather(**kwargs):
        return SAMPLE_AICHECKER

    monkeypatch.setattr(watch, "gather", fake_gather)

    watch.watch_5h_resets(on_reset=lambda labels: called.append(labels), once=True)

    assert called == []
