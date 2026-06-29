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
  Tier: Antigravity (free-tier)
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