"""Burn-rate calculator — track usage velocity and predict time-to-limit.

Stores a rolling history of usage snapshots and computes how fast each
window is being consumed. This lets an agent answer questions like
"at the current rate, how long until the 5h limit is hit?" without
needing a background daemon — every ``aichecker`` call appends a data
point and the math is done on demand.

History is persisted to ``~/.cache/ai-limit-checker/burn_rate.json`` so
it survives across CLI invocations and cron runs.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .cli import gather
from .utils import format_duration

CACHE_DIR = Path.home() / ".cache" / "ai-limit-checker"
HISTORY_FILE = CACHE_DIR / "burn_rate.json"
MAX_HISTORY_PER_WINDOW = 50  # keep last 50 data points per window


def _extract_windows(data: dict) -> dict[str, dict]:
    """Flatten an aichecker JSON result into per-window snapshots.

    Returns ``{window_id: {"label": str, "used_pct": float,
    "resets_at": str | None, "timestamp": float}}``.
    """
    now = time.time()
    windows: dict[str, dict] = {}

    claude = data.get("claude", {})
    if claude.get("status") == "ok":
        for key, label in [
            ("five_hour", "Claude 5h"),
            ("seven_day", "Claude 7d"),
            ("seven_day_sonnet", "Claude Sonnet 7d"),
        ]:
            w = claude.get(key)
            if w and w.get("used_pct") is not None:
                windows[f"claude_{key}"] = {
                    "label": label,
                    "used_pct": w["used_pct"],
                    "resets_at": w.get("resets_at"),
                    "timestamp": now,
                }

    agy = data.get("antigravity", {})
    if agy.get("status") == "ok":
        for grp in agy.get("groups", []):
            gname = grp.get("name", "?")
            for b in grp.get("buckets", []):
                if b.get("used_pct") is None:
                    continue
                wid = f"agy_{gname}_{b.get('window', '?')}"
                windows[wid] = {
                    "label": f"Antigravity {gname} {b.get('label', b.get('window', '?'))}",
                    "used_pct": b["used_pct"],
                    "resets_at": b.get("resets_at"),
                    "timestamp": now,
                }

    return windows


def _load_history() -> dict[str, list[dict]]:
    """Load the persisted history (``{window_id: [snapshots]}``)."""
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_history(history: dict[str, list[dict]]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(history, indent=2), encoding="utf-8")
    except OSError:
        pass


def record_snapshot(data: dict | None = None) -> dict[str, list[dict]]:
    """Append current usage to history and return the full history dict.

    If ``data`` is ``None``, gathers fresh data (both tools, no cache).
    """
    if data is None:
        data = gather(do_claude=True, do_antigravity=True, use_cache=False)

    current = _extract_windows(data)
    history = _load_history()

    for wid, snap in current.items():
        snaps = history.setdefault(wid, [])
        snaps.append(snap)
        # Trim to last N to prevent unbounded growth
        if len(snaps) > MAX_HISTORY_PER_WINDOW:
            history[wid] = snaps[-MAX_HISTORY_PER_WINDOW:]

    _save_history(history)
    return history


def calculate_burn_rate(history: dict[str, list[dict]]) -> dict[str, dict]:
    """Compute burn rate and time-to-limit for each window.

    Returns ``{window_id: {"label": str, "used_pct": float,
    "velocity_pct_per_hour": float | None, "eta_seconds": float | None,
    "eta_text": str, "samples": int}}``.

    ``velocity_pct_per_hour`` is the slope of ``used_pct`` over time,
    computed from the most recent samples. ``eta_seconds`` is the
    estimated time until ``used_pct`` reaches 100 (``None`` if the rate
    is zero or negative).
    """
    results: dict[str, dict] = {}

    for wid, snaps in history.items():
        if len(snaps) < 2:
            latest = snaps[-1] if snaps else {"label": wid, "used_pct": 0.0}
            results[wid] = {
                "label": latest.get("label", wid),
                "used_pct": latest.get("used_pct", 0.0),
                "velocity_pct_per_hour": None,
                "eta_seconds": None,
                "eta_text": "insufficient data",
                "samples": len(snaps),
            }
            continue

        # Use the last N samples (up to 10) for the velocity calculation
        recent = snaps[-10:]
        first = recent[0]
        last = recent[-1]
        dt = last["timestamp"] - first["timestamp"]
        du = last["used_pct"] - first["used_pct"]

        velocity = None if dt <= 0 else (du / dt) * 3600  # pct per hour

        used = last["used_pct"]
        if velocity is not None and velocity > 0:
            remaining_pct = max(0.0, 100.0 - used)
            eta_seconds = remaining_pct / velocity * 3600
            eta_text = format_duration(int(eta_seconds))
        elif velocity is not None and velocity <= 0:
            eta_seconds = None
            eta_text = "not increasing"
        else:
            eta_seconds = None
            eta_text = "unknown"

        results[wid] = {
            "label": last.get("label", wid),
            "used_pct": used,
            "velocity_pct_per_hour": round(velocity, 2) if velocity is not None else None,
            "eta_seconds": round(eta_seconds, 0) if eta_seconds is not None else None,
            "eta_text": eta_text,
            "samples": len(snaps),
        }

    return results


def get_burn_rate(fresh: bool = True) -> dict[str, dict]:
    """Convenience: record a snapshot (if ``fresh``) and return burn rates.

    When ``fresh`` is ``True``, gathers current usage and appends it to
    history before calculating. When ``False``, calculates from the
    existing history only (no API calls).
    """
    history = record_snapshot() if fresh else _load_history()
    return calculate_burn_rate(history)


def format_burn_rate(rates: dict[str, dict]) -> str:
    """Render burn rates as a human-readable summary."""
    if not rates:
        return "No burn-rate data yet. Run aichecker a few times to build history."

    lines: list[str] = []
    now = datetime.now(timezone.utc)
    lines.append(f"📈 Burn Rate Analysis ({now.astimezone().strftime('%H:%M:%S')})")
    lines.append("")

    for _wid, r in rates.items():
        label = r["label"]
        used = r["used_pct"]
        vel = r["velocity_pct_per_hour"]
        eta = r["eta_text"]
        samples = r["samples"]

        vel_str = f"{vel:+.1f}%/h" if vel is not None else "n/a"
        lines.append(f"  {label}")
        lines.append(f"    Used: {used:.1f}%  |  Velocity: {vel_str}  |  ETA to 100%: {eta}")
        lines.append(f"    (based on {samples} sample{'s' if samples != 1 else ''})")
        lines.append("")

    return "\n".join(lines).rstrip()
