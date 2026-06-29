"""Tests for CLI argument parsing, output formats, and caching."""

import json

from ai_limit_checker import cli, utils

SAMPLE = {
    "claude": {
        "status": "ok",
        "error": None,
        "plan": "Max",
        "five_hour": {"used_pct": 1.0, "remaining_pct": 99.0, "resets_at": "2026-06-29T16:56:00Z"},
        "seven_day": {"used_pct": 56.0, "remaining_pct": 44.0, "resets_at": "2026-07-01T22:00:00Z"},
        "seven_day_sonnet": {"used_pct": 8.0, "remaining_pct": 92.0, "resets_at": None},
    },
    "antigravity": {
        "status": "ok",
        "error": None,
        "tier": "Antigravity",
        "tier_id": "free-tier",
        "project_id": "my-project-12345",
        "groups": [
            {
                "name": "Gemini Models",
                "models": "Gemini Flash, Gemini Pro",
                "buckets": [
                    {
                        "label": "Weekly Limit",
                        "window": "weekly",
                        "used_pct": 0.0,
                        "remaining_pct": 100.0,
                        "resets_at": "2026-07-06T06:28:57Z",
                        "note": None,
                    },
                    {
                        "label": "Five Hour Limit",
                        "window": "5h",
                        "used_pct": 0.0,
                        "remaining_pct": 100.0,
                        "resets_at": "2026-06-29T16:56:00Z",
                        "note": None,
                    },
                ],
            },
            {
                "name": "Claude and GPT models",
                "models": "Claude Opus, Claude Sonnet, GPT-OSS",
                "buckets": [
                    {
                        "label": "Weekly Limit",
                        "window": "weekly",
                        "used_pct": 93.0,
                        "remaining_pct": 7.0,
                        "resets_at": "2026-07-02T03:28:55Z",
                        "note": "You have used some of your weekly limit.",
                    },
                ],
            },
        ],
        "highest_used_pct": 93.0,
        "group_count": 2,
    },
}


def test_parser_defaults():
    args = cli.build_parser().parse_args([])
    assert not args.json and not args.oneline and not args.claude and not args.antigravity


def test_parser_flags():
    args = cli.build_parser().parse_args(["--json", "--claude"])
    assert args.json and args.claude


def test_format_json_is_valid():
    parsed = json.loads(cli.format_json(SAMPLE))
    assert parsed["claude"]["plan"] == "Max"
    assert parsed["antigravity"]["highest_used_pct"] == 93.0


def test_format_oneline():
    line = cli.format_oneline(SAMPLE)
    assert "Claude: 1.0% (5h)" in line
    assert "56.0% (7d)" in line
    assert "Antigravity: 93.0% used" in line
    assert utils.OK in line
    assert "\n" not in line


def test_format_oneline_no_credentials():
    line = cli.format_oneline({"claude": {"status": "no_credentials"}})
    assert "no creds" in line


def test_format_human_sections():
    out = cli.format_human(SAMPLE)
    assert "🔍 AI CLI Usage Checker" in out
    assert "Claude Code (Max Plan)" in out
    assert "5h Window:  1.0% used (99.0% left)" in out
    assert "Antigravity CLI" in out
    assert "Tier: Antigravity (free-tier)" in out
    assert "Project: my-project-12345" in out
    assert "Gemini Models" in out
    assert "Claude and GPT models" in out
    assert "Weekly Limit:" in out
    assert "93.0% used" in out


def test_used_text_true_zero():
    # remaining_fraction exactly 1 -> the limit is genuinely untouched.
    assert cli._used_text({"used_pct": 0.0, "remaining_fraction": 1}) == "  0.0% used"


def test_used_text_tiny_nonzero_shows_threshold():
    # Real-but-tiny usage (rounds to 0.0%) must not look identical to an
    # untouched limit.
    assert cli._used_text({"used_pct": 0.0, "remaining_fraction": 0.9996}) == " <0.1% used"


def test_used_text_normal_and_missing():
    assert cli._used_text({"used_pct": 1.6, "remaining_fraction": 0.984}) == "  1.6% used"
    assert cli._used_text({"used_pct": None}) == "    ? used"


def test_tier_label_free_shows_id():
    assert (
        cli._tier_label({"tier": "Antigravity", "tier_id": "free-tier"})
        == "Antigravity (free-tier)"
    )


def test_tier_label_paid_hides_id():
    # A Google One subscription shows its name only, never the internal id.
    label = cli._tier_label(
        {"tier": "Google AI Ultra", "tier_id": "g1-ultra-lite-tier", "is_paid": True}
    )
    assert label == "Google AI Ultra"


def test_tier_label_unknown():
    assert cli._tier_label({"tier": None}) == "unknown"


def test_format_human_error_state():
    out = cli.format_human({"claude": {"status": "error", "error": "HTTP 429"}})
    assert "Error: HTTP 429" in out


def _patch_cache(monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(cli, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(cli, "CACHE_FILE", cache_dir / "usage.json")


def test_gather_uses_cache(monkeypatch, tmp_path):
    _patch_cache(monkeypatch, tmp_path)
    calls = {"claude": 0}

    def fake_check_claude():
        calls["claude"] += 1
        return SAMPLE["claude"]

    monkeypatch.setattr(cli, "check_claude", fake_check_claude)

    first = cli.gather(do_claude=True, do_antigravity=False)
    second = cli.gather(do_claude=True, do_antigravity=False)
    assert first == second
    assert calls["claude"] == 1  # second call served from cache


def test_gather_no_cache_flag(monkeypatch, tmp_path):
    _patch_cache(monkeypatch, tmp_path)
    calls = {"claude": 0}

    def fake_check_claude():
        calls["claude"] += 1
        return SAMPLE["claude"]

    monkeypatch.setattr(cli, "check_claude", fake_check_claude)
    cli.gather(do_claude=True, do_antigravity=False, use_cache=False)
    cli.gather(do_claude=True, do_antigravity=False, use_cache=False)
    assert calls["claude"] == 2


def test_main_json_output(monkeypatch, tmp_path, capsys):
    _patch_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "check_claude", lambda: SAMPLE["claude"])
    monkeypatch.setattr(cli, "check_antigravity", lambda: SAMPLE["antigravity"])
    rc = cli.main(["--json", "--no-cache"])
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out)
    assert parsed["claude"]["status"] == "ok"
    assert parsed["antigravity"]["status"] == "ok"


def test_parse_since_durations():
    now = 1_700_000_000.0
    assert cli._parse_since("30m", now=now) == now - 1800
    assert cli._parse_since("2h", now=now) == now - 7200
    assert cli._parse_since("1d", now=now) == now - 86400
    assert cli._parse_since("90s", now=now) == now - 90


def test_parse_since_epoch_and_invalid():
    assert cli._parse_since("1700000000") == 1700000000.0
    assert cli._parse_since("garbage") is None


def test_main_history_routes_to_history(monkeypatch, capsys):
    import ai_limit_checker.history as hist_mod

    captured = {}

    def fake_get_history(window_id=None, since=None, limit=None):
        captured["window_id"] = window_id
        captured["since"] = since
        return {"claude_five_hour": [{"label": "Claude 5h", "used_pct": 30.0, "timestamp": 1.0}]}

    monkeypatch.setattr(hist_mod, "get_history", fake_get_history)

    rc = cli.main(["--history", "--window", "claude_five_hour", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert captured["window_id"] == "claude_five_hour"
    parsed = json.loads(out)
    assert parsed["claude_five_hour"][0]["used_pct"] == 30.0


def test_main_history_clear(monkeypatch, capsys):
    import ai_limit_checker.history as hist_mod

    monkeypatch.setattr(hist_mod, "clear_history", lambda window_id=None: 2)
    rc = cli.main(["--history", "--clear"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Cleared history for 2 windows" in out


def test_main_recommend_routes_to_recommend(monkeypatch, capsys):
    import ai_limit_checker.recommend as rec_mod

    fake_rec = {
        "providers": {"claude": {}, "antigravity": {}},
        "recommended_provider": "antigravity",
        "reason": "x",
        "alternatives": [],
    }
    monkeypatch.setattr(rec_mod, "get_recommendation", lambda fresh=True: fake_rec)
    rc = cli.main(["--recommend", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out)
    assert parsed["recommended_provider"] == "antigravity"


def test_main_claude_only(monkeypatch, tmp_path, capsys):
    _patch_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "check_claude", lambda: SAMPLE["claude"])

    def fail():
        raise AssertionError("antigravity must not be checked with --claude")

    monkeypatch.setattr(cli, "check_antigravity", fail)
    rc = cli.main(["--claude", "--json", "--no-cache"])
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out)
    assert "claude" in parsed
    assert "antigravity" not in parsed
