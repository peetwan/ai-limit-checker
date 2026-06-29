# AGENTS.md — ai-limit-checker workspace rules

## Project
ai-limit-checker — Python CLI tool to check Claude Code + Antigravity CLI usage limits.
PyPI package name: `ai-limit-checker`, commands: `aichecker`, `ailimits`.

## Rules
- Python 3.10+, type hints required on all public functions
- Zero external dependencies (stdlib only)
- Cross-platform: Windows, macOS, Linux
- Never print credentials/tokens in output
- ruff clean (E, F, W, I, UP, B, SIM)
- All tests pass with pytest
- Never push to main directly
- Comments/docs in English (this is an international open-source project)