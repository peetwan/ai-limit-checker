"""Tests for the auto-switch recommendation module."""

from ai_limit_checker import recommend

# --- builders --------------------------------------------------------------


def _claude(five=None, seven=None, status="ok") -> dict:
    c: dict = {"status": status, "error": None}
    if five is not None:
        c["five_hour"] = {
            "used_pct": five,
            "remaining_pct": 100 - five,
            "resets_at": "2026-06-29T20:00:00Z",
        }
    if seven is not None:
        c["seven_day"] = {
            "used_pct": seven,
            "remaining_pct": 100 - seven,
            "resets_at": "2026-07-02T22:00:00Z",
        }
    return c


def _agy(used=None, group="Gemini Models", window="5h", status="ok") -> dict:
    if used is None:
        return {"status": status, "groups": [], "highest_used_pct": None}
    return {
        "status": status,
        "highest_used_pct": used,
        "groups": [
            {
                "name": group,
                "buckets": [
                    {
                        "label": "Five Hour Limit",
                        "window": window,
                        "used_pct": used,
                        "remaining_pct": 100 - used,
                        "resets_at": "2026-06-29T20:00:00Z",
                    }
                ],
            }
        ],
    }


def _patch(monkeypatch, claude: dict, agy: dict) -> None:
    import ai_limit_checker.cli as cli_mod

    def fake_gather(do_claude: bool, do_antigravity: bool, use_cache: bool = True) -> dict:
        return {"claude": claude, "antigravity": agy}

    monkeypatch.setattr(cli_mod, "gather", fake_gather)


# --- classify --------------------------------------------------------------


def test_classify_levels():
    assert recommend.classify(None) == recommend.STATUS_UNKNOWN
    assert recommend.classify(0.0) == recommend.STATUS_SAFE
    assert recommend.classify(69.9) == recommend.STATUS_SAFE
    assert recommend.classify(70.0) == recommend.STATUS_WARNING
    assert recommend.classify(89.9) == recommend.STATUS_WARNING
    assert recommend.classify(90.0) == recommend.STATUS_CRITICAL
    assert recommend.classify(99.9) == recommend.STATUS_CRITICAL
    assert recommend.classify(100.0) == recommend.STATUS_EXHAUSTED
    assert recommend.classify(150.0) == recommend.STATUS_EXHAUSTED


# --- get_recommendation ----------------------------------------------------


def test_recommend_both_safe(monkeypatch):
    _patch(monkeypatch, _claude(30, 40), _agy(20))
    rec = recommend.get_recommendation()
    assert rec["recommended_provider"] == "either"
    assert rec["providers"]["claude"]["status"] == "safe"
    assert rec["providers"]["antigravity"]["status"] == "safe"
    assert rec["alternatives"] == []


def test_recommend_claude_critical_switch_to_agy(monkeypatch):
    _patch(monkeypatch, _claude(92, 50), _agy(45))
    rec = recommend.get_recommendation()
    assert rec["recommended_provider"] == "antigravity"
    claude = rec["providers"]["claude"]
    assert claude["status"] == "critical"
    assert claude["highest_used_pct"] == 92
    assert claude["bottleneck_window"] == "5h"
    assert "Use Antigravity" in rec["reason"]


def test_recommend_warning_prefers_safe(monkeypatch):
    _patch(monkeypatch, _claude(79, 50), _agy(45))
    rec = recommend.get_recommendation()
    assert rec["recommended_provider"] == "antigravity"
    # The warning-level provider remains a documented fallback.
    assert rec["alternatives"] == ["Claude (warning)"]


def test_recommend_both_warning_lower_used_wins(monkeypatch):
    _patch(monkeypatch, _claude(72, 60), _agy(85))
    rec = recommend.get_recommendation()
    # Scores are close so "either" is fine, but Claude should score higher due to more headroom
    assert rec["recommended_provider"] == "either"
    assert rec["providers"]["claude"]["score"] > rec["providers"]["antigravity"]["score"]
    assert rec["providers"]["claude"]["bottleneck_window"] == "5h"


def test_recommend_both_critical_none(monkeypatch):
    _patch(monkeypatch, _claude(95, 91), _agy(93))
    rec = recommend.get_recommendation()
    assert rec["recommended_provider"] == "none"


def test_recommend_both_exhausted_none(monkeypatch):
    _patch(monkeypatch, _claude(100, 100), _agy(105))
    rec = recommend.get_recommendation()
    assert rec["recommended_provider"] == "none"
    assert rec["providers"]["claude"]["status"] == "exhausted"


def test_recommend_errors_are_unknown(monkeypatch):
    _patch(monkeypatch, {"status": "error", "error": "HTTP 401"}, {"status": "error"})
    rec = recommend.get_recommendation()
    assert rec["recommended_provider"] == "none"
    assert rec["providers"]["claude"]["status"] == "unknown"
    assert rec["providers"]["claude"]["highest_used_pct"] is None


def test_recommend_one_provider_errors(monkeypatch):
    _patch(monkeypatch, _claude(30, 40), {"status": "error"})
    rec = recommend.get_recommendation()
    assert rec["recommended_provider"] == "claude"
    assert rec["providers"]["antigravity"]["status"] == "unknown"


def test_recommend_antigravity_bottleneck_group(monkeypatch):
    _patch(monkeypatch, _claude(10, 20), _agy(95, group="Claude and GPT models", window="weekly"))
    rec = recommend.get_recommendation(exclude_groups=())
    agy = rec["providers"]["antigravity"]
    assert agy["status"] == "critical"
    assert agy["bottleneck_group"] == "Claude and GPT models"
    assert agy["bottleneck_window"] == "weekly"
    assert rec["recommended_provider"] == "claude"


# --- format_recommendation -------------------------------------------------


def test_format_recommendation_output(monkeypatch):
    _patch(monkeypatch, _claude(79, 50), _agy(45))
    rec = recommend.get_recommendation()
    text = recommend.format_recommendation(rec)
    assert "🎯 Recommendation: Switch to Antigravity" in text
    assert "Claude Code:" in text
    assert "Antigravity:" in text
    assert "Reason:" in text
    assert "Alternatives: Claude (warning)" in text


def test_format_recommendation_short_group():
    rec = {
        "providers": {
            "claude": {
                "status": "warning",
                "highest_used_pct": 79.0,
                "bottleneck_window": "5h",
                "resets_at": None,
                "windows": [
                    {"label": "5h", "used_pct": 79.0, "status": "warning", "resets_at": None},
                    {"label": "7d", "used_pct": 50.0, "status": "safe", "resets_at": None},
                ],
            },
            "antigravity": {
                "status": "safe",
                "highest_used_pct": 45.0,
                "bottleneck_window": "5h",
                "bottleneck_group": "Gemini Models",
                "resets_at": None,
                "windows": [
                    {
                        "label": "Five Hour Limit",
                        "window": "5h",
                        "group": "Gemini Models",
                        "used_pct": 45.0,
                        "status": "safe",
                        "resets_at": None,
                    },
                ],
            },
        },
        "recommended_provider": "antigravity",
        "reason": "Claude 5h at 79% (warning), Antigravity at 45% (safe). Switch to Antigravity.",
        "alternatives": ["Claude (warning)"],
    }
    text = recommend.format_recommendation(rec)
    # "Gemini Models" is shortened to "Gemini" and paired with its window.
    assert "Gemini 5h bottleneck" in text
    assert "5h bottleneck" in text  # Claude's window
    # Per-window sub-lines are rendered.
    assert "5h: ⚠️ warning (79.0% used)" in text
    assert "7d: ✅ safe (50.0% used)" in text
    assert "Gemini Five Hour Limit: ✅ safe (45.0% used)" in text


def test_format_recommendation_none(monkeypatch):
    _patch(monkeypatch, _claude(95, 91), _agy(93))
    rec = recommend.get_recommendation()
    text = recommend.format_recommendation(rec)
    assert "All providers near their limit" in text


def test_recommend_per_window_status(monkeypatch):
    """Each window gets its own status, not just the bottleneck."""
    # Claude: 5h=92% (critical), 7d=50% (safe)
    # Antigravity: Gemini 5h=45% (safe), Gemini weekly=80% (warning)
    agy = {
        "status": "ok",
        "highest_used_pct": 80,
        "groups": [
            {
                "name": "Gemini Models",
                "buckets": [
                    {
                        "label": "Five Hour Limit",
                        "window": "5h",
                        "used_pct": 45,
                        "remaining_pct": 55,
                        "resets_at": "2026-06-29T20:00:00Z",
                    },
                    {
                        "label": "Weekly Limit",
                        "window": "weekly",
                        "used_pct": 80,
                        "remaining_pct": 20,
                        "resets_at": "2026-07-06T12:00:00Z",
                    },
                ],
            }
        ],
    }
    _patch(monkeypatch, _claude(92, 50), agy)
    rec = recommend.get_recommendation()

    # Claude windows: 5h critical, 7d safe
    claude_windows = rec["providers"]["claude"]["windows"]
    assert len(claude_windows) == 2
    by_label = {w["label"]: w for w in claude_windows}
    assert by_label["5h"]["status"] == "critical"
    assert by_label["5h"]["used_pct"] == 92
    assert by_label["7d"]["status"] == "safe"
    assert by_label["7d"]["used_pct"] == 50

    # Antigravity windows: 5h safe, weekly warning
    agy_windows = rec["providers"]["antigravity"]["windows"]
    assert len(agy_windows) == 2
    by_w = {w["window"]: w for w in agy_windows}
    assert by_w["5h"]["status"] == "safe"
    assert by_w["weekly"]["status"] == "warning"

    # Provider-level status = worst window's status
    assert rec["providers"]["claude"]["status"] == "critical"
    assert rec["providers"]["antigravity"]["status"] == "warning"

    # Format renders per-window lines
    text = recommend.format_recommendation(rec)
    assert "5h: 🔴 critical (92.0% used" in text
    assert "7d: ✅ safe (50.0% used" in text
    assert "Gemini Five Hour Limit: ✅ safe (45.0% used" in text
    assert "Gemini Weekly Limit: ⚠️ warning (80.0% used" in text


def test_exclude_groups(monkeypatch):
    # Excludes "Claude and GPT models" by default, includes "Gemini Models"
    _patch(
        monkeypatch,
        _claude(10, 20),
        {
            "status": "ok",
            "groups": [
                {
                    "name": "Claude and GPT models",
                    "buckets": [{"label": "Five Hour Limit", "window": "5h", "used_pct": 15.0}],
                },
                {
                    "name": "Gemini Models",
                    "buckets": [{"label": "Weekly Limit", "window": "weekly", "used_pct": 25.0}],
                },
            ],
        },
    )
    rec = recommend.get_recommendation()
    agy = rec["providers"]["antigravity"]
    # "Claude and GPT models" should be excluded, so only 1 window from "Gemini Models"
    assert len(agy["windows"]) == 1
    assert agy["windows"][0]["group"] == "Gemini Models"


def test_exclude_groups_custom(monkeypatch):
    # Custom exclude_groups parameter works
    _patch(
        monkeypatch,
        _claude(10, 20),
        {
            "status": "ok",
            "groups": [
                {
                    "name": "Claude and GPT models",
                    "buckets": [{"label": "Five Hour Limit", "window": "5h", "used_pct": 15.0}],
                },
                {
                    "name": "Gemini Models",
                    "buckets": [{"label": "Weekly Limit", "window": "weekly", "used_pct": 25.0}],
                },
            ],
        },
    )
    rec = recommend.get_recommendation(exclude_groups=("Gemini Models",))
    agy = rec["providers"]["antigravity"]
    assert len(agy["windows"]) == 1
    assert agy["windows"][0]["group"] == "Claude and GPT models"


def test_exclude_groups_empty(monkeypatch):
    # Passing exclude_groups=() keeps both
    _patch(
        monkeypatch,
        _claude(10, 20),
        {
            "status": "ok",
            "groups": [
                {
                    "name": "Claude and GPT models",
                    "buckets": [{"label": "Five Hour Limit", "window": "5h", "used_pct": 15.0}],
                },
                {
                    "name": "Gemini Models",
                    "buckets": [{"label": "Weekly Limit", "window": "weekly", "used_pct": 25.0}],
                },
            ],
        },
    )
    rec = recommend.get_recommendation(exclude_groups=())
    agy = rec["providers"]["antigravity"]
    assert len(agy["windows"]) == 2


def test_scoring_severity():
    assert recommend._score_severity("safe") == 100.0
    assert recommend._score_severity("warning") == 50.0
    assert recommend._score_severity("critical") == 15.0
    assert recommend._score_severity("exhausted") == 0.0
    assert recommend._score_severity("unknown") == 0.0


def test_scoring_headroom():
    # min remaining headroom
    assert recommend._score_headroom([]) == 0.0
    assert recommend._score_headroom([{"used_pct": 20.0}, {"used_pct": 40.0}]) == 60.0
    assert recommend._score_headroom([{"used_pct": 95.0}]) == 5.0


def test_scoring_reset_proximity():
    from datetime import datetime, timezone

    now = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)

    # 1. No windows/resets_at -> 30.0
    assert recommend._score_reset_proximity({}, now=now) == 30.0
    assert recommend._score_reset_proximity({"windows": []}, now=now) == 30.0

    # 2. Worst window used < 70% -> 100.0 (safe)
    info_safe = {"windows": [{"used_pct": 50.0, "resets_at": "2026-06-29T13:00:00Z"}]}
    assert recommend._score_reset_proximity(info_safe, now=now) == 100.0

    # 3. Worst window >= 70% and resets in < 1h -> 80.0
    info_soon = {"windows": [{"used_pct": 80.0, "resets_at": "2026-06-29T12:30:00Z"}]}
    assert recommend._score_reset_proximity(info_soon, now=now) == 80.0

    # 4. Worst window >= 70% and resets in < 4h -> 60.0
    info_4h = {"windows": [{"used_pct": 80.0, "resets_at": "2026-06-29T15:30:00Z"}]}
    assert recommend._score_reset_proximity(info_4h, now=now) == 60.0

    # 5. Worst window >= 70% and resets in < 12h -> 40.0
    info_12h = {"windows": [{"used_pct": 80.0, "resets_at": "2026-06-29T23:30:00Z"}]}
    assert recommend._score_reset_proximity(info_12h, now=now) == 40.0

    # 6. Worst window >= 70% and resets in < 24h -> 20.0
    info_24h = {"windows": [{"used_pct": 80.0, "resets_at": "2026-06-30T11:30:00Z"}]}
    assert recommend._score_reset_proximity(info_24h, now=now) == 20.0

    # 7. Worst window >= 70% and resets in > 24h -> 10.0
    info_late = {"windows": [{"used_pct": 80.0, "resets_at": "2026-07-01T12:00:00Z"}]}
    assert recommend._score_reset_proximity(info_late, now=now) == 10.0


def test_scoring_burn_rate(monkeypatch):
    import ai_limit_checker.burn_rate as br_mod

    # 1. No data -> 50.0
    def fake_load_history_empty():
        return {}

    monkeypatch.setattr(br_mod, "_load_history", fake_load_history_empty)
    assert recommend._score_burn_rate("claude") == 50.0

    # 2. Velocity <= 10%/h -> 40.0
    def fake_load_history_10():
        return {
            "claude_five_hour": [
                {"label": "Claude 5h", "used_pct": 10.0, "timestamp": 1000.0},
                {"label": "Claude 5h", "used_pct": 20.0, "timestamp": 4600.0},
            ]
        }

    monkeypatch.setattr(br_mod, "_load_history", fake_load_history_10)
    assert recommend._score_burn_rate("claude") == 40.0


def test_score_in_result(monkeypatch):
    _patch(monkeypatch, _claude(30, 40), _agy(20))
    rec = recommend.get_recommendation()
    claude = rec["providers"]["claude"]
    agy = rec["providers"]["antigravity"]

    assert "score" in claude
    assert "score_breakdown" in claude
    assert "severity" in claude["score_breakdown"]
    assert "headroom" in claude["score_breakdown"]
    assert "reset_proximity" in claude["score_breakdown"]
    assert "burn_rate" in claude["score_breakdown"]

    assert "score" in agy
    assert "score_breakdown" in agy


def test_format_shows_score(monkeypatch):
    _patch(monkeypatch, _claude(79, 50), _agy(45))
    rec = recommend.get_recommendation()
    text = recommend.format_recommendation(rec)
    assert "— score: " in text
