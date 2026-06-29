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
  Antigravity CLI (Ultra)
════════════════════════════════════════
  ✅ Connected
  Project: melodic-component-26v41
  Models: 8 | Tightest: 65% remaining
```

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
| Antigravity CLI | Per-model quota, remaining fraction, reset time |

## How It Works

- Claude Code: Reads `~/.claude/.credentials.json` (Windows/Linux) or macOS Keychain, calls `api.anthropic.com/api/oauth/usage`
- Antigravity: Reads Windows Credential Manager (`gemini:antigravity`) or `~/.gemini/oauth_creds.json`, calls `cloudcode-pa.googleapis.com`

No credentials are sent anywhere except the official API endpoints of each tool.

## License

MIT