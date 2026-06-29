"""Usage history (timeseries view).

Where :mod:`ai_limit_checker.burn_rate` answers "how fast am I consuming each
limit?", this module exposes the *raw* snapshot series it stores — so a user or
agent can look at the actual trend of ``used_pct`` over time rather than just a
single velocity number.

Both modules share the same on-disk store
(``~/.cache/ai-limit-checker/burn_rate.json``); this one simply reads it back
with optional filtering and renders it as a human-readable timeseries.
"""

from __future__ import annotations

from datetime import datetime

from .burn_rate import _load_history, _save_history


def load_history() -> dict[str, list[dict]]:
    """Load persisted history from ``burn_rate.json``.

    Public alias of :func:`burn_rate._load_history` so callers don't have to
    reach into a private name.
    """
    return _load_history()


def get_history(
    window_id: str | None = None,
    since: float | None = None,
    limit: int | None = None,
) -> dict[str, list[dict]]:
    """Return history snapshots, optionally filtered.

    - ``window_id``: filter to a single window (e.g. ``"claude_five_hour"``).
      ``None`` returns all windows.
    - ``since``: unix timestamp; only snapshots strictly after this are
      returned. ``None`` returns all.
    - ``limit``: max snapshots per window (the most recent N). ``None`` returns
      all; ``0`` returns none.

    Each snapshot has the keys ``label``, ``used_pct``, ``resets_at`` and
    ``timestamp``.
    """
    history = _load_history()
    result: dict[str, list[dict]] = {}

    for wid, snaps in history.items():
        if window_id is not None and wid != window_id:
            continue
        filtered = snaps
        if since is not None:
            filtered = [s for s in filtered if s.get("timestamp", 0) > since]
        if limit is not None:
            filtered = filtered[-limit:] if limit > 0 else []
        result[wid] = filtered

    return result


def format_history(history: dict[str, list[dict]], window_id: str | None = None) -> str:
    """Render history as a human-readable timeseries.

    For each window, the label and sample count are printed, followed by one
    line per snapshot with its timestamp, ``used_pct`` and the delta from the
    previous sample::

        Claude 5h  (3 samples)
          2026-06-29 12:00  45.0% used
          2026-06-29 12:30  52.0% used  (+7.0)
          2026-06-29 13:00  58.0% used  (+6.0)
    """
    if window_id is not None:
        windows = {window_id: history[window_id]} if window_id in history else {}
    else:
        windows = history

    if not any(snaps for snaps in windows.values()):
        return "No history yet. Run aichecker a few times to build history."

    lines: list[str] = []
    for wid, snaps in windows.items():
        if not snaps:
            continue
        label = snaps[-1].get("label", wid)
        plural = "s" if len(snaps) != 1 else ""
        lines.append(f"{label}  ({len(snaps)} sample{plural})")

        prev: float | None = None
        for snap in snaps:
            ts = snap.get("timestamp")
            time_str = (
                datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
                if isinstance(ts, (int, float))
                else "?"
            )
            used = snap.get("used_pct")
            if used is None:
                lines.append(f"  {time_str}  ? used")
                continue
            line = f"  {time_str}  {used:.1f}% used"
            if prev is not None:
                line += f"  ({used - prev:+.1f})"
            lines.append(line)
            prev = used
        lines.append("")

    return "\n".join(lines).rstrip()


def clear_history(window_id: str | None = None) -> int:
    """Clear stored history.

    If ``window_id`` is given, only that window is removed. Returns the number
    of windows cleared (``0`` if there was nothing to clear).
    """
    history = _load_history()
    if not history:
        return 0

    if window_id is not None:
        if window_id not in history:
            return 0
        del history[window_id]
        _save_history(history)
        return 1

    count = len(history)
    _save_history({})
    return count
