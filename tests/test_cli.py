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
        "project_id": "melodic-component-26v41",
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
    assert "Project: melodic-component-26v41" in out
    assert "Gemini Models" in out
    assert "Claude and GPT models" in out
    assert "Weekly Limit:" in out
    assert "93.0% used" in out


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
