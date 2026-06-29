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
    assert "Switch to Antigravity" in rec["reason"]


def test_recommend_warning_prefers_safe(monkeypatch):
    _patch(monkeypatch, _claude(79, 50), _agy(45))
    rec = recommend.get_recommendation()
    assert rec["recommended_provider"] == "antigravity"
    # The warning-level provider remains a documented fallback.
    assert rec["alternatives"] == ["Claude (warning)"]


def test_recommend_both_warning_lower_used_wins(monkeypatch):
    _patch(monkeypatch, _claude(72, 60), _agy(85))
    rec = recommend.get_recommendation()
    assert rec["recommended_provider"] == "claude"
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
    rec = recommend.get_recommendation()
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
            },
            "antigravity": {
                "status": "safe",
                "highest_used_pct": 45.0,
                "bottleneck_window": "5h",
                "bottleneck_group": "Gemini Models",
                "resets_at": None,
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


def test_format_recommendation_none(monkeypatch):
    _patch(monkeypatch, _claude(95, 91), _agy(93))
    rec = recommend.get_recommendation()
    text = recommend.format_recommendation(rec)
    assert "All providers near their limit" in text
