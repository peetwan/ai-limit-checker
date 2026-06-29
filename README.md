# AI Limit Checker

<div align="center">

**Check usage limits for Claude Code and Antigravity CLI from your terminal.**

[![PyPI](https://img.shields.io/pypi/v/ai-limit-checker.svg)](https://pypi.org/project/ai-limit-checker/)
[![Python](https://img.shields.io/pypi/pyversions/ai-limit-checker.svg)](https://pypi.org/project/ai-limit-checker/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://img.shields.io/badge/tests-77%20passed-brightgreen.svg)](#testing)

</div>

---

## ✨ Features

- **Claude Code** — 5h & 7d usage windows with Sonnet/Opus breakdown
- **Antigravity CLI** — Per-group (Gemini / Claude+GPT) weekly + five-hour limits
- **Watch mode** — Automatically detect when a 5h limit resets and notify you
- **JSON output** — Structured output for AI agents (Hermes, Claude Code, etc.)
- **Zero dependencies** — Pure Python stdlib, no pip conflicts
- **Cross-platform** — Windows, macOS, and Linux
- **No credential leakage** — Tokens never printed; only official API endpoints are called

## Install

```bash
pip install ai-limit-checker
```

Requires Python 3.10+. No external dependencies.

## Quick Start

```bash
# Show all limits (default)
aichecker

# JSON output for AI agents / scripts
aichecker --json

# Compact one-liner (great for shell prompts / tmux status bars)
aichecker --oneline

# Check only one tool
aichecker --claude
aichecker --antigravity

# Ignore the 60s cache (force fresh API call)
aichecker --no-cache
```

Two command names are available — `aichecker` and `ailimits` — both invoke the same entry point.

## Watch Mode

Watch mode polls usage every 5 minutes and prints a message when a **5h limit window resets** — so you know the moment your quota refreshes and can resume work.

### CLI

```bash
# Run continuously (polls every 5 min, prints on reset)
aichecker --watch

# Single check — perfect for cron jobs
aichecker --watch --once

# Customise poll interval and post-reset delay
aichecker --watch --interval 60 --delay 30
```

**How it works:**

1. Every poll, the tool records each 5h window's `resets_at` timestamp and `used_pct`
2. When `now >= resets_at + delay` (default 120s) **and** the window had usage > 0% before, a reset is detected
3. A message is printed (or your callback is invoked)
4. State is persisted to `~/.cache/ai-limit-checker/watch_state.json` across restarts

The 2-minute delay ensures the server has fully refreshed before triggering.

### Cron setup

For scheduled use, run a single check with `--once`:

```bash
# crontab — every 5 minutes
*/5 * * * * /usr/local/bin/aichecker --watch --once
```

The tool stays silent when no reset has occurred (empty stdout = nothing to report).

### Programmatic API

```python
from ai_limit_checker.watch import watch_5h_resets

# Built-in: prints to stdout on reset
watch_5h_resets(once=True)

# Custom callback — send to Discord, Telegram, Slack, etc.
def on_reset(reset_labels: list[str]) -> None:
    msg = f"🔄 Limits reset: {', '.join(reset_labels)}"
    send_to_discord(msg)  # your notification function

watch_5h_resets(on_reset=on_reset, interval=300, delay=120, once=True)
```

| Parameter   | Type             | Default | Description                                          |
| ----------- | ---------------- | ------- | --------------------------------------------------- |
| `on_reset`  | `Callable \| None` | `None`  | Callback receiving a list of reset window labels. If `None`, prints to stdout. |
| `interval`  | `int`            | `300`   | Seconds between polls (when not `--once`).           |
| `delay`     | `int`            | `120`   | Seconds to wait after `resets_at` before triggering. |
| `once`      | `bool`           | `False` | Run a single check and exit (for cron/scheduled use). |

## JSON Output

```bash
aichecker --json
```

Returns structured JSON with all limits, remaining percentages, and reset timestamps. AI agents can parse this to plan task delegation based on remaining quota.

<details>
<summary><b>Example JSON structure</b></summary>

```json
{
  "claude": {
    "status": "ok",
    "plan": "max",
    "five_hour": {
      "used_pct": 1.0,
      "remaining_pct": 99.0,
      "resets_at": "2026-06-29T16:31:00Z"
    },
    "seven_day": {
      "used_pct": 56.0,
      "remaining_pct": 44.0,
      "resets_at": "2026-07-02T05:00:00Z"
    }
  },
  "antigravity": {
    "status": "ok",
    "tier": "Google AI Ultra",
    "is_paid": true,
    "project_id": "my-project-12345",
    "groups": [
      {
        "name": "Gemini Models",
        "buckets": [
          {
            "window": "weekly",
            "label": "Weekly Limit",
            "used_pct": 0.0,
            "remaining_pct": 100.0,
            "remaining_fraction": 1.0,
            "resets_at": "2026-07-06T12:00:00Z"
          },
          {
            "window": "5h",
            "label": "Five Hour Limit",
            "used_pct": 0.0,
            "remaining_pct": 100.0,
            "remaining_fraction": 1.0,
            "resets_at": "2026-06-29T18:31:00Z"
          }
        ]
      },
      {
        "name": "Claude and GPT models",
        "buckets": [
          {
            "window": "weekly",
            "label": "Weekly Limit",
            "used_pct": 93.0,
            "remaining_pct": 7.0,
            "remaining_fraction": 0.07,
            "resets_at": "2026-07-02T12:00:00Z"
          },
          {
            "window": "5h",
            "label": "Five Hour Limit",
            "used_pct": 95.0,
            "remaining_pct": 5.0,
            "remaining_fraction": 0.05,
            "resets_at": "2026-06-29T12:50:00Z"
          }
        ]
      }
    ],
    "highest_used_pct": 95.0
  }
}
```

</details>

## Example Output

```
🔍 AI CLI Usage Checker
2026-06-29 12:00:00

════════════════════════════════════════
  Claude Code (Max Plan)
════════════════════════════════════════
  ✅ Connected
  5h Window:  1.0% used (99.0% left) → resets in 4h 56m
  7d Window:  56.0% used (44.0% left) → resets in 2d 17h

════════════════════════════════════════
  Antigravity CLI
════════════════════════════════════════
  ✅ Connected
  Tier: Google AI Ultra
  Project: my-project-12345

  Gemini Models
    Weekly Limit:       0.0% used → resets in 6d 23h
    Five Hour Limit:    0.0% used → resets in 4h 59m

  Claude and GPT models
    Weekly Limit:      93.0% used → resets in 2d 20h
    Five Hour Limit:   95.0% used → resets in 19m
```

One-liner mode (`--oneline`):

```
Claude: 1.0% (5h) 🟢 | 56.0% (7d) 🟡 | Antigravity: 95.0% used 🔴
```

## How It Works

### Claude Code

1. Reads OAuth credentials from `~/.claude/.credentials.json` (Windows/Linux) or macOS Keychain
2. Calls the official Anthropic usage API to get 5h and 7d window data

### Antigravity CLI

1. Reads OAuth credentials from Windows Credential Manager (`gemini:antigravity`) or `~/.gemini/oauth_creds.json`
2. Calls `daily-cloudcode-pa.googleapis.com` — the same endpoint the Antigravity desktop app uses
3. Fetches tier info via `loadCodeAssist`, then per-model-group quota buckets

> **Why `daily-` prefix?** The base endpoint `cloudcode-pa.googleapis.com` always returns `remainingFraction: 1` (100% remaining) regardless of actual usage. The `daily-` prefixed host returns real-time usage data that matches the desktop app's "Weekly Limit" / "Five Hour Limit" readouts.

### Antigravity usage readouts

Usage is reported as **% used**, matching the Antigravity desktop app. Models are grouped (Gemini vs. Claude/GPT); within a group the weekly and five-hour windows are shared.

**Tier note:** `loadCodeAssist` returns two tiers. `currentTier` is the Cloud Code Assist *API* tier — always `free-tier` for consumer (non-GCP) accounts, regardless of any Google One AI subscription. `paidTier` carries the real subscription (e.g. *Google AI Ultra*) and only appears when one exists, so the tool prefers it. The raw API tier is still available as `api_tier_id` in `--json` output. Accounts with no Google One AI plan correctly show `Antigravity (free-tier)`.

**Why "Gemini Models" can sit at `0.0%`:** on a *Google AI Ultra* account the Gemini group is effectively unmetered — the server reports `remainingFraction` of exactly `1` no matter how much you use Gemini (verified against a run that consumed millions of Gemini tokens). Only the third-party group (*Claude and GPT*) is metered and moves. So a Gemini group stuck at `0.0% used` after heavy Antigravity use is expected, not a bug. Genuinely tiny usage (under 0.1%) is shown as `<0.1% used` to distinguish it from an untouched `0.0%` limit, and the raw `remaining_fraction` (0–1, full precision) is included per bucket in `--json` output.

## Supported Tools

| Tool             | Metrics                                                    |
| ---------------- | --------------------------------------------------------- |
| Claude Code      | 5h window, 7d window, Sonnet/Opus breakdown               |
| Antigravity CLI  | Per-group weekly + five-hour limits, % used, reset time   |

## Programmatic API

All functions are importable from `ai_limit_checker`:

```python
from ai_limit_checker import check_claude, check_antigravity

# Check Claude Code usage
claude_result = check_claude()
print(claude_result["five_hour"]["used_pct"])

# Check Antigravity usage
agy_result = check_antigravity()
for group in agy_result.get("groups", []):
    print(group["name"])
    for bucket in group["buckets"]:
        print(f"  {bucket['label']}: {bucket['used_pct']}% used")
```

```python
from ai_limit_checker.cli import gather, format_json, format_oneline

# Gather both tools at once (with 60s caching)
result = gather(do_claude=True, do_antigravity=True)
print(format_json(result))
```

## CLI Reference

```
aichecker [OPTIONS]

Options:
  --json              Output structured JSON
  --oneline           Output a compact one-liner
  --claude            Check only Claude Code
  --antigravity       Check only Antigravity CLI
  --no-cache          Ignore the 60s result cache
  --watch             Watch mode: poll and print on 5h limit reset
  --once              Watch mode: single check (for cron)
  --interval SECONDS  Watch mode: poll interval (default 300)
  --delay SECONDS     Watch mode: delay after reset before triggering (default 120)
  --version           Show version
  -h, --help          Show help
```

## Development

```bash
git clone https://github.com/peetwan/ai-limit-checker.git
cd ai-limit-checker

# Install in editable mode with test dependencies
pip install -e ".[test]"
# or: pip install -e . && pip install pytest ruff

# Run tests
pytest

# Lint
ruff check src/ tests/

# Run locally
python -m ai_limit_checker --json
```

### Testing

The test suite uses `pytest` with 77 tests covering:

- Credential parsing (Claude & Antigravity)
- API response parsing and normalization
- Output formatting (human, JSON, one-liner)
- Watch mode: reset detection, state persistence, callback invocation
- Edge cases: missing credentials, API errors, unmetered groups, zero-usage rounding

```bash
pytest          # run all tests
pytest -q       # quiet mode
pytest -k watch # run only watch-mode tests
```

## License

MIT © [Peet Chanut](https://github.com/peetwan)

## Links

- [PyPI](https://pypi.org/project/ai-limit-checker/)
- [GitHub](https://github.com/peetwan/ai-limit-checker)
- [Issue Tracker](https://github.com/peetwan/ai-limit-checker/issues)