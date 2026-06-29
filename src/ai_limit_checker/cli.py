"""Command-line entry point: argument parsing, caching, and output rendering."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .antigravity import check_antigravity
from .claude import check_claude
from .utils import (
    FAIL,
    OK,
    format_reset_in,
    status_icon,
)

BAR = "═" * 40
CACHE_DIR = Path.home() / ".cache" / "ai-limit-checker"
CACHE_FILE = CACHE_DIR / "usage.json"
CACHE_TTL = 60
_CACHE_KEY = "_cached_at"


# --- data gathering & caching ---------------------------------------------


def read_cache(ttl: int = CACHE_TTL, now: float | None = None) -> dict | None:
    """Return cached results if present and fresher than ``ttl`` seconds."""
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    reference = now if now is not None else time.time()
    if reference - data.get(_CACHE_KEY, 0) > ttl:
        return None
    return data


def write_cache(data: dict, now: float | None = None) -> None:
    """Persist results to the cache file (best effort; failures are ignored)."""
    reference = now if now is not None else time.time()
    payload = {k: v for k, v in data.items() if k != _CACHE_KEY}
    payload[_CACHE_KEY] = reference
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass


def gather(do_claude: bool, do_antigravity: bool, use_cache: bool = True) -> dict:
    """Collect results for the requested tools, using the cache when fresh."""
    cache = read_cache() if use_cache else None
    result: dict = {}
    fresh = False

    if do_claude:
        if cache and "claude" in cache:
            result["claude"] = cache["claude"]
        else:
            result["claude"] = check_claude()
            fresh = True
    if do_antigravity:
        if cache and "antigravity" in cache:
            result["antigravity"] = cache["antigravity"]
        else:
            result["antigravity"] = check_antigravity()
            fresh = True

    if fresh:
        merged = {k: v for k, v in (cache or {}).items() if k != _CACHE_KEY}
        merged.update(result)
        write_cache(merged)
    return result


# --- rendering -------------------------------------------------------------


def format_json(result: dict) -> str:
    return json.dumps(result, indent=2)


def format_human(result: dict, now: datetime | None = None) -> str:
    reference = now or datetime.now(timezone.utc)
    lines = ["🔍 AI CLI Usage Checker", reference.astimezone().strftime("%Y-%m-%d %H:%M:%S")]
    if "claude" in result:
        lines.append("")
        lines.extend(_claude_section(result["claude"]))
    if "antigravity" in result:
        lines.append("")
        lines.extend(_antigravity_section(result["antigravity"]))
    return "\n".join(lines)


def format_oneline(result: dict) -> str:
    parts = []
    if "claude" in result:
        parts.append(_claude_oneline(result["claude"]))
    if "antigravity" in result:
        parts.append(_antigravity_oneline(result["antigravity"]))
    return " | ".join(p for p in parts if p)


def _claude_section(c: dict) -> list[str]:
    plan = c.get("plan")
    title = f"Claude Code ({plan} Plan)" if plan else "Claude Code"
    out = [BAR, f"  {title}", BAR]
    if c.get("status") != "ok":
        out.append(f"  {FAIL} {_status_text(c)}")
        return out
    out.append(f"  {OK} Connected")
    if c.get("five_hour"):
        out.append(f"  5h Window:  {_window_text(c['five_hour'])}")
    if c.get("seven_day"):
        out.append(f"  7d Window:  {_window_text(c['seven_day'])}")
    if c.get("seven_day_sonnet"):
        out.append(f"  Sonnet 7d:  {_window_text(c['seven_day_sonnet'], show_reset=False)}")
    return out


def _antigravity_section(a: dict) -> list[str]:
    out = [BAR, "  Antigravity CLI", BAR]
    if a.get("status") != "ok":
        out.append(f"  {FAIL} {_status_text(a)}")
        return out
    out.append(f"  {OK} Connected")
    out.append(f"  Tier: {_tier_label(a)}")
    if a.get("project_id"):
        out.append(f"  Project: {a['project_id']}")
    groups = a.get("groups") or []
    if not groups:
        out.append("  No quota data available")
        return out
    for group in groups:
        out.append("")
        out.append(f"  {group['name']}")
        out.extend(_bucket_lines(group.get("buckets") or []))
    return out


def _tier_label(a: dict) -> str:
    tier = a.get("tier")
    if not tier:
        return "unknown"
    # A Google One subscription name (e.g. "Google AI Ultra") is self-explanatory;
    # the internal id ("g1-ultra-lite-tier") would just be noise. For the free API
    # tier, the id ("free-tier") is the informative part, so keep it.
    if a.get("is_paid"):
        return tier
    tier_id = a.get("tier_id")
    if tier_id and tier_id.lower() != tier.lower():
        return f"{tier} ({tier_id})"
    return tier


def _bucket_lines(buckets: list[dict]) -> list[str]:
    lines = []
    for b in buckets:
        label = (b.get("label", "Limit") + ":").ljust(17)
        line = f"    {label}{_used_text(b)}"
        if b.get("resets_at"):
            line += f" → resets in {format_reset_in(b['resets_at'])}"
        lines.append(line)
    return lines


def _used_text(b: dict) -> str:
    """Render the "X% used" cell, distinguishing true-zero from rounded-zero.

    A bucket that rounds to ``0.0%`` but has a nonzero raw fraction (any usage
    under 0.05%) is shown as ``<0.1% used`` so real-but-tiny consumption doesn't
    look identical to an untouched limit. ``0.0% used`` is reserved for a bucket
    the server reports as exactly full (``remainingFraction == 1``).
    """
    used = b.get("used_pct")
    if used is None:
        return "    ? used"
    fraction = b.get("remaining_fraction")
    if used == 0.0 and fraction is not None and fraction < 1.0:
        return f"{'<0.1%':>6} used"
    return f"{used:>5.1f}% used"


def _window_text(w: dict, show_reset: bool = True) -> str:
    text = f"{w['used_pct']:.1f}% used ({w['remaining_pct']:.1f}% left)"
    if show_reset and w.get("resets_at"):
        text += f" → resets in {format_reset_in(w['resets_at'])}"
    return text


def _claude_oneline(c: dict) -> str:
    if c.get("status") != "ok":
        return f"Claude: {FAIL} {_short_status(c)}"
    segments = []
    if c.get("five_hour"):
        used = c["five_hour"]["used_pct"]
        segments.append(f"{used:.1f}% (5h) {status_icon(used)}")
    if c.get("seven_day"):
        used = c["seven_day"]["used_pct"]
        segments.append(f"{used:.1f}% (7d) {status_icon(used)}")
    return "Claude: " + " | ".join(segments) if segments else f"Claude: {FAIL} no data"


def _antigravity_oneline(a: dict) -> str:
    if a.get("status") != "ok":
        return f"Antigravity: {FAIL} {_short_status(a)}"
    used = a.get("highest_used_pct")
    if used is None:
        return f"Antigravity: {FAIL} no data"
    return f"Antigravity: {used:.1f}% used {status_icon(used)}"


def _status_text(d: dict) -> str:
    status = d.get("status")
    if status == "no_credentials":
        return "No credentials found"
    if status == "error":
        return f"Error: {d.get('error') or 'unknown'}"
    return "Unavailable"


def _short_status(d: dict) -> str:
    return "no creds" if d.get("status") == "no_credentials" else "error"


# --- entry point -----------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aichecker",
        description="Check usage limits for Claude Code and Antigravity CLI.",
    )
    parser.add_argument("--json", action="store_true", help="Output structured JSON")
    parser.add_argument("--oneline", action="store_true", help="Output a compact one-liner")
    parser.add_argument("--claude", action="store_true", help="Check only Claude Code")
    parser.add_argument("--antigravity", action="store_true", help="Check only Antigravity CLI")
    parser.add_argument("--no-cache", action="store_true", help="Ignore the 60s result cache")
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch mode: poll every 5 min and print when a 5h limit window resets. "
        "Use --interval and --delay to customise. Use --once for a single check "
        "(suitable for cron jobs).",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        metavar="SECONDS",
        help="Watch mode: seconds between polls (default 300)",
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=120,
        metavar="SECONDS",
        help="Watch mode: seconds to wait after reset time before triggering (default 120)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Watch mode: run a single check and exit (for cron/scheduled use)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Watch mode: log what would happen without calling the CLIs",
    )
    parser.add_argument(
        "--burn-rate",
        action="store_true",
        help="Show burn-rate analysis: usage velocity and estimated time to limit.",
    )
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Start as an MCP server (JSON-RPC over stdio) for AI agent integration.",
    )
    parser.add_argument("--version", action="version", version=f"ai-limit-checker {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.mcp:
        from .mcp_server import serve

        serve()
        return 0

    if args.watch:
        from .watch import watch_5h_resets

        watch_5h_resets(
            interval=args.interval,
            delay=args.delay,
            once=args.once,
            dry_run=args.dry_run,
        )
        return 0

    if args.burn_rate:
        from .burn_rate import format_burn_rate, get_burn_rate

        rates = get_burn_rate(fresh=not args.no_cache)
        if args.json:
            print(json.dumps(rates, indent=2))
        else:
            print(format_burn_rate(rates))
        return 0

    both = not (args.claude or args.antigravity)
    result = gather(
        do_claude=args.claude or both,
        do_antigravity=args.antigravity or both,
        use_cache=not args.no_cache,
    )

    if args.json:
        print(format_json(result))
    elif args.oneline:
        print(format_oneline(result))
    else:
        print(format_human(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
