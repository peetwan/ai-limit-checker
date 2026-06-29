# AI Limit Checker: Research and Development Roadmap

This report explores future development opportunities for the `ai-limit-checker` project. It analyzes the current implementation, evaluates the feasibility of integrating other popular AI coding tools, proposes agent-intelligence features, surveys the competitive landscape, and outlines a recommended product roadmap.

---

## Current State

The `ai-limit-checker` project is a zero-dependency, cross-platform CLI tool and Python library designed to query and display usage limits for Claude Code and the Antigravity CLI.

### Core Features Today

*   `Claude Code Check`: Reads credentials from local files or macOS Keychain. It performs proactive token refreshes (before expiry) and reactive retries (on HTTP 401) using the platform.claude.com OAuth endpoint. It queries the official Anthropic usage API (`https://api.anthropic.com/api/oauth/usage`) to obtain 5h, 7d, and Sonnet-specific usage windows.
*   `Antigravity CLI Check`: Scans the locally installed `agy` binary to extract OAuth client credentials (or loads from env vars). It proactively manages token expiration and reactively retries on 401 errors. It queries the Google Daily Cloud Code API (`https://daily-cloudcode-pa.googleapis.com`) to extract current tier, project info, and real-time usage metrics grouped by model types (such as Gemini vs. Claude/GPT).
*   `Watch Mode`: A background loop (`--watch`) or single-run cron tool (`--once`) that monitors 5-hour usage metrics. If a window that had active usage is reset (current time passes `resets_at + delay`), it sends a trivial prompt (e.g. "hi") to the relevant CLI to trigger a new 5h usage window on the server.
*   `Output Modes`: Renders human-readable formatted CLI output with color-coded status emojis, compact one-liners for shell integration, and structured JSON output for easy programmatic ingestion by other scripts or AI agents.
*   `Zero Dependencies`: Written purely in Python (using only standard libraries) to guarantee compatibility and prevent package conflicts.

---

## New Tool Integrations

The table below outlines the integration feasibility of other prominent AI coding assistants and API providers.

| Tool | Feasibility (1-5) | Auth Method | Quotas/Limits Tracked | API Endpoint / Details | Programmatic Viability |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `OpenRouter` | 5/5 | API Key | Credit limit, remaining credits, daily/weekly/monthly usage | `GET https://openrouter.ai/api/v1/key` | `High`: Officially documented and public. Easy to integrate. |
| `DeepSeek` | 5/5 | API Key | Total balance, granted balance, topped-up balance | `GET https://api.deepseek.com/user/balance` | `High`: Officially documented and public. Easy to integrate. |
| `Cursor IDE` | 3/5 | Session Cookie | Fast requests (e.g., 500/mo), monthly budget, on-demand spend | `GET https://cursor.com/api/usage` (unofficial) | `Medium`: Requires extracting the `WorkosCursorSessionToken` cookie. Fragile to dashboard changes. |
| `OpenAI API` | 2.5/5 | Admin API Key / Session Token | Daily spending cost in USD, remaining credit grants | `GET /v1/organization/costs` (official) or `/v1/dashboard/...` (unofficial) | `Low-Medium`: Direct individual credit checks are undocumented. Organization cost tracking is stable. |
| `Sourcegraph Cody` | 2/5 | Access Token / API Key | Daily chat/completions limits on free/Pro, Cody Gateway limits | `GET /.api/modelconfig/supported-models.json` (model configs) | `Low`: No dedicated personal quota tracking endpoints. Mostly managed via server logs or Gateway. |
| `Windsurf / Codeium` | 1.5/5 | Service Keys / Cookies | Cascade agent credits, think-model quotas, team usage | `/api/CascadeAnalytics` (enterprise only) | `Very Low`: Personal account quotas are only visible in-IDE; private APIs are highly protected. |
| `GitHub Copilot` | 1/5 | GitHub PAT / OAuth | Seat metrics, aggregate team stats (Enterprise/Org only) | `/orgs/{org}/copilot/metrics/reports/user-teams-1-day` | `Very Low` (for individuals): No individual real-time limit API exists. |

### Feasibility Insights

*   `OpenRouter and DeepSeek`: These represent low-hanging fruit. They provide stable, official, and authenticated JSON REST endpoints to retrieve usage.
*   `Cursor`: Although Cursor is highly popular, integration requires the user to manually extract a session cookie (`WorkosCursorSessionToken`) from their browser. Since the endpoint is internal and undocumented, it is subject to silent failure.
*   `GitHub Copilot, Windsurf, Cody`: These tools primarily enforce limits client-side within their IDE extensions or on enterprise gateway middleware. They lack public, individual-level quota check APIs, making them poor targets for a lightweight CLI limit checker.

---

## Agent Intelligence Features

AI agents (such as Hermes, Claude Code, and Antigravity) can utilize structured JSON usage data to make smarter, self-directed decisions:

### 1. Proactive Task Routing and Model Tiering
*   `Multi-CLI Redirection`: If an agent is running low on its Claude Code 5h window (e.g., > 90% used), but still has plenty of quota on the Antigravity Claude/GPT group, it can shift execution away from `claude -p` to the `agy` CLI to continue tasks.
*   `Premium Model Preservation`: Agents can evaluate sub-tasks and run low-complexity operations (such as code formatting, search, or linting) using cheaper, unmetered models (like Gemini Flash or Haiku) while reserving expensive Sonnet or Gemini Pro quotas for complex debugging or architecture changes.

### 2. Auto-Pause and Resume (Smart Pacing)
*   `Graceful Hibernation`: Instead of hitting a hard rate limit mid-task and failing catastrophically, an agent can check the usage limits before starting a large operation. If it detects a limit is imminent, it can output its current state, write a progress file, sleep until the reset timestamp (`resets_at`), and automatically resume.

### 3. Predictive Burn-Rate Modeling
*   `Token Burn Rate Tracking`: The agent can compute its average token usage per turn. If it calculates that a task requires approximately 10 turns but the remaining quota only allows for 3, it can proactively warn the user or prompt for model downgrades before wasting credits.

### 4. Pre-Reset Task Queueing
*   `Watch Daemon Coordination`: A background script can monitor the resets. When a reset is 5 minutes away, it can notify the agent to begin staging next-turn requests (such as re-indexing the workspace or compiling large codebases), executing them immediately once the reset is triggered.

### 5. Context Optimization
*   `Dynamic History Trimming`: As quotas deplete, the agent can decrease the number of historical messages or disable auto-reading of large files (using options like `no-context` or clearing workspace cache) to minimize the token footprint per command.

---

## Competitive Landscape

Several tools exist in the ecosystem that track AI tool limits and token consumption:

*   `claude-pace` (GitHub: Han/claude-pace): A single-file Bash script that tracks the burn rate of Claude Code. It displays a status line predicting if the user's coding speed will hit the 5h limit.
*   `Claude-Code-Usage-Monitor` (GitHub: Maciek-roboblog/Claude-Code-Usage-Monitor): A native macOS menu bar app that monitors Claude Code limits using a rich UI, predicting session durations.
*   `CodexBar / OpenUsage / mimir` (macOS Utilities): Lightweight menu bar apps that track local session limits for Cursor, Claude Code, and OpenAI Codex.
*   `ai-usage-monitor` (VS Code Extension): A sidebar dashboard that integrates multiple API providers (OpenAI, Claude, DeepSeek, Zhipu, OpenRouter) and tracks credit balances directly in VS Code.
*   `new-api` (GitHub: QuantumNous/new-api): An enterprise API gateway wrapper that distributes, controls, and accounts for token usage across development teams.

### Competitive Gaps for ai-limit-checker
1.  `Lack of IDE Integrations`: Competitors run as VS Code extensions or macOS menu bar apps. `ai-limit-checker` is terminal-only.
2.  `Lack of Burn-Rate Analysis`: Other tools compute real-time pacing (burn rate per minute) rather than just reporting the current raw percentage.
3.  `Limited Platform Support`: The tool is currently restricted to Claude Code and Antigravity, while competitors support standard API endpoints (OpenRouter, DeepSeek).

---

## Recommended Roadmap

Here are the top 5 features recommended for `ai-limit-checker`, ranked by impact versus effort:

### 1. Model Context Protocol (MCP) Server Wrapper
*   `Impact`: High | `Effort`: Medium
*   `Description`: Wrap the Python CLI as an MCP server. This allows agents (such as Hermes or Claude Code) to naturally query `aichecker` endpoints using standard tool calling.
*   `Implementation`: Create a `src/ai_limit_checker/mcp.py` using the `mcp` SDK to expose `get_limits` and `get_watch_status` tools.

### 2. OpenRouter and DeepSeek API Support
*   `Impact`: High | `Effort`: Low
*   `Description`: Integrate official quota endpoints for OpenRouter and DeepSeek. Many developers use these providers as cheaper backends for coding extensions.
*   `Implementation`: Add standard `check_openrouter()` and `check_deepseek()` functions in the core library, loading API keys from environment variables or standard config files.

### 3. Burn-Rate and Pacing Calculator
*   `Impact`: Medium | `Effort`: Low
*   `Description`: Calculate usage velocity between CLI runs. Report whether the current rate of use is sustainable (e.g. "Usage velocity is +12%/hr; you will hit limit in 1.5h").
*   `Implementation`: Track the last three query timestamps and percentages in `~/.cache/ai-limit-checker/usage.json` to calculate the slope of the usage curve.

### 4. VS Code Status Bar Extension
*   `Impact`: High | `Effort`: Medium-High
*   `Description`: Wrap the CLI in a simple VS Code extension that displays the compact `--oneline` output in the VS Code status bar.
*   `Implementation`: Write a lightweight TypeScript extension that spawns the `aichecker --oneline` process periodically and updates a status bar item.

### 5. Cursor IDE Session Token Integration (Experimental)
*   `Impact`: Medium-High | `Effort`: Medium
*   `Description`: Add basic support for Cursor dashboard tracking by reading the `WorkosCursorSessionToken` environment variable or local config.
*   `Implementation`: Implement `check_cursor()`, performing a `GET` request to `https://cursor.com/api/usage` with the token cookie and parsing the JSON response. Include warnings about the undocumented nature of the API.
