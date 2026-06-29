# AI Limit Checker

Check usage limits for Claude Code and Antigravity CLI from your terminal.

## Install

```bash
pip install ai-limit-checker
```

## Usage

```bash
# Show all limits (one-liner)
aichecker

# JSON output for AI agents
aichecker --json

# Compact one-liner
aichecker --oneline
```

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
  Project: melodic-component-26v41

  Gemini Models
    Weekly Limit:      0.0% used → resets in 6d 23h
    Five Hour Limit:   0.0% used → resets in 4h 59m

  Claude and GPT models
    Weekly Limit:     93.0% used → resets in 2d 20h
    Five Hour Limit:  95.0% used → resets in 19m
```

> Antigravity usage matches the desktop app's "Weekly Limit" / "Five Hour Limit"
> readouts. Models are grouped (Gemini vs. Claude/GPT); within a group the
> weekly and five-hour windows are shared. Reported as **% used**, like the app.
>
> **Tier note:** `loadCodeAssist` returns two tiers. `currentTier` is the Cloud
> Code Assist *API* tier — always `free-tier` for consumer (non-GCP) accounts,
> regardless of any Google One AI subscription. `paidTier` carries the real
> subscription (e.g. *Google AI Ultra*) and only appears when one exists, so the
> tool prefers it. The raw API tier is still available as `api_tier_id` in
> `--json` output. Accounts with no Google One AI plan correctly show
> `Antigravity (free-tier)`.
>
> **Why "Gemini Models" can sit at `0.0%`:** on a *Google AI Ultra* account the
> Gemini group is effectively unmetered — the server reports `remainingFraction`
> of exactly `1` no matter how much you use Gemini (verified against a run that
> consumed millions of Gemini tokens). Only the third-party group (*Claude and
> GPT*) is metered and moves. So a Gemini group stuck at `0.0% used` after heavy
> Antigravity use is expected, not a bug. Genuinely tiny usage (under 0.1%) is
> shown as `<0.1% used` to distinguish it from an untouched `0.0%` limit, and the
> raw `remaining_fraction` (0-1, full precision) is included per bucket in
> `--json` output.

## JSON Output (for AI agents)

```bash
aichecker --json
```

Returns structured JSON with all limits, remaining percentages, and reset times.
AI agents (Hermes, Claude Code, etc.) can parse this to plan task delegation.

## Supported Tools

| Tool | Metrics |
|------|---------|
| Claude Code | 5h window, 7d window, Sonnet/Opus breakdown |
| Antigravity CLI | Per-group weekly + five-hour limits, % used, reset time |

## How It Works

- Claude Code: Reads `~/.claude/.credentials.json` (Windows/Linux) or macOS Keychain, calls `api.anthropic.com/api/oauth/usage`
- Antigravity: Reads Windows Credential Manager (`gemini:antigravity`) or `~/.gemini/oauth_creds.json`, calls `cloudcode-pa.googleapis.com`

No credentials are sent anywhere except the official API endpoints of each tool.

## License

MIT