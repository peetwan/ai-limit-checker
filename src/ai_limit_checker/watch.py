"""Watch mode — monitor 5h limit windows and trigger a ping on reset.

Runs as a foreground loop (``aichecker --watch``) or can be used
programmatically via :func:`watch_5h_resets`.

The watcher polls usage every ``interval`` seconds, tracks 5h reset
timestamps in a state file, and triggers a ping when a window resets
(current time passes ``reset_time + delay``).  By default it sends a
trivial prompt to the matching CLI (``claude -p`` or ``agy -p``) to
start a new 5h usage window.  Pass a custom ``on_reset`` callback to
integrate with Discord, Telegram, or any notification system.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .cli import gather

STATE_DIR = Path.home() / ".cache" / "ai-limit-checker"
STATE_FILE = STATE_DIR / "watch_state.json"
DEFAULT_INTERVAL = 300  # 5 minutes
DEFAULT_DELAY = 120  # 2 minutes after reset

# Trivial prompts sent to each CLI to trigger a new 5h usage window.
# Kept intentionally minimal — the goal is just to make one API call
# so the server starts counting a fresh 5h window.
_PING_PROMPTS: dict[str, str] = {
    "claude": "hi",
    "antigravity": "hi",
}

# Timeout for the ping subprocess (seconds).  We only need the request
# to land; we don't care about the response.
_PING_TIMEOUT = 30


def collect_5h_windows(data: dict) -> dict[str, dict]:
    """Extract all 5h windows from an aichecker JSON result.

    Returns a dict keyed by a unique window identifier, each containing::

        {"label": str, "resets_at": str | None, "used_pct": float,
         "tool": str}
    """
    windows: dict[str, dict] = {}

    claude = data.get("claude", {})
    if claude.get("status") == "ok":
        f5 = claude.get("five_hour") or {}
        if f5.get("resets_at"):
            windows["claude_5h"] = {
                "label": "Claude Code 5h",
                "resets_at": f5.get("resets_at"),
                "used_pct": f5.get("used_pct", 0),
                "tool": "claude",
            }

    agy = data.get("antigravity", {})
    if agy.get("status") == "ok":
        for grp in agy.get("groups", []):
            gname = grp.get("name", "?")
            for b in grp.get("buckets", []):
                if b.get("window") == "5h" and b.get("resets_at"):
                    key = f"agy_{gname}_5h"
                    windows[key] = {
                        "label": f"Antigravity {gname} 5h",
                        "resets_at": b.get("resets_at"),
                        "used_pct": b.get("used_pct", 0),
                        "tool": "antigravity",
                    }

    return windows


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def check_resets(
    current: dict[str, dict],
    state: dict[str, dict],
    now: datetime | None = None,
    delay: int = DEFAULT_DELAY,
) -> list[str]:
    """Return labels of windows that have reset since the last check.

    A reset is detected when:
    - The window had ``used_pct > 0`` in the previous state (it was being used)
    - The current time is past ``prev_resets_at + delay`` seconds
    """
    ref = now or datetime.now(timezone.utc)
    reset_labels: list[str] = []

    for key, window in current.items():
        prev = state.get(key)
        if not prev or prev.get("used_pct", 0) <= 0:
            continue
        prev_reset = _parse_iso(prev.get("resets_at"))
        if not prev_reset:
            continue
        if ref >= prev_reset + timedelta(seconds=delay):
            reset_labels.append(window["label"])

    return reset_labels


def _ping_cli(tool: str, dry_run: bool = False) -> str:
    """Send a trivial prompt to the CLI to trigger a new 5h window.

    Returns a short status string suitable for logging.
    """
    if dry_run:
        return f"{tool}: dry-run (would ping)"

    binary = shutil.which(tool)
    if not binary:
        return f"{tool}: binary not found"

    prompt = _PING_PROMPTS.get(tool, "hi")
    try:
        subprocess.run(
            [binary, "-p", prompt],
            timeout=_PING_TIMEOUT,
            capture_output=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"{tool}: ping sent (timeout, expected)"
    except OSError as exc:
        return f"{tool}: error — {exc}"

    return f"{tool}: pinged"


def trigger_pings(
    current: dict[str, dict],
    reset_keys: list[str],
    dry_run: bool = False,
) -> dict[str, str]:
    """Send a ping to the CLI for each reset window.

    Returns a mapping of ``window_label → status_string``.
    """
    results: dict[str, str] = {}
    seen_tools: set[str] = set()

    for key in reset_keys:
        window = current.get(key)
        if not window:
            continue
        tool = window.get("tool", "")
        label = window["label"]

        # Only ping each tool once per cycle — multiple groups (e.g.
        # Gemini + Claude/GPT on Antigravity) share the same 5h window
        # on the same CLI, so one ping covers all of them.
        if tool in seen_tools:
            results[label] = f"{tool}: already pinged this cycle"
            continue
        seen_tools.add(tool)

        results[label] = _ping_cli(tool, dry_run=dry_run)

    return results


def watch_5h_resets(
    on_reset: Callable[[list[str]], None] | None = None,
    interval: int = DEFAULT_INTERVAL,
    delay: int = DEFAULT_DELAY,
    once: bool = False,
    dry_run: bool = False,
) -> None:
    """Poll usage and trigger pings when 5h windows reset.

    Args:
        on_reset: Called with a list of reset window labels. If ``None``,
            prints a message to stdout.
        interval: Seconds between polls (default 300 = 5 min).
        delay: Seconds to wait after reset_time before triggering (default 120).
        once: If ``True``, run a single check and exit (for cron/scheduled use).
        dry_run: If ``True``, log what would happen without calling the CLIs.
    """
    state = _load_state()

    while True:
        result = gather(do_claude=True, do_antigravity=True, use_cache=False)
        current = collect_5h_windows(result)

        # Find which windows reset by comparing current keys against state.
        reset_keys: list[str] = []
        ref = datetime.now(timezone.utc)
        for key, _window in current.items():
            prev = state.get(key)
            if not prev or prev.get("used_pct", 0) <= 0:
                continue
            prev_reset = _parse_iso(prev.get("resets_at"))
            if not prev_reset:
                continue
            if ref >= prev_reset + timedelta(seconds=delay):
                reset_keys.append(key)

        # Update state with current windows before pinging.
        state.update(current)
        _save_state(state)

        if reset_keys:
            labels = [current[k]["label"] for k in reset_keys]
            ping_results = trigger_pings(current, reset_keys, dry_run=dry_run)

            if on_reset:
                on_reset(labels)
            else:
                timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
                if len(labels) == 1:
                    print(f"🔄 [{timestamp}] 5h limit reset: {labels[0]}")
                else:
                    listing = ", ".join(labels)
                    print(f"🔄 [{timestamp}] 5h limits reset: {listing}")
                for label, status in ping_results.items():
                    print(f"  → {label}: {status}")

        if once:
            return
        time.sleep(interval)
