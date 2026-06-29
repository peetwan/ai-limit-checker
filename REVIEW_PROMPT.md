# Code Review Request: ai-limit-checker

Review the ai-limit-checker project for security issues and bugs.

## Scope

1. **Secret leak check**: Scan ALL files (source, tests, git history, config) for any leaked secrets — OAuth client IDs, client secrets, API tokens, credentials, refresh tokens. Report exactly what leaked and where.

2. **Bug hunt**: Review the code for correctness bugs — edge cases, error handling, platform compatibility (Windows/macOS/Linux), credential discovery logic, HTTP error handling, output formatting, JSON output structure.

3. **Security review**: Check if credentials could be printed/logged accidentally. Verify tokens are only sent to official API endpoints.

## Files to review

```
src/ai_limit_checker/
  __init__.py
  antigravity.py
  claude.py
  cli.py
  credentials.py
  utils.py
tests/
  (all test files)
pyproject.toml
README.md
AGENTS.md
```

## Report format

Output a structured report:
- **Secrets found**: list each with file:line, or "none"
- **Bugs found**: list each with file:line, severity, description, suggested fix
- **Security issues**: list each with file:line, or "none"
- **Recommendation**: ship as-is or fix-first

## Constraints
- Read-only review. Do NOT modify any files.
- Do NOT push or commit.
- Be honest and thorough — this is a public open-source package.