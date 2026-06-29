"""Auto-switch recommendation.

Looks at the current usage across every provider/window and answers the
practical question an agent actually cares about: *which provider should I use
next?* When Claude's 5h window is nearly spent it suggests switching to
Antigravity (or vice-versa); when both have headroom it says either is fine;
when everything is near its limit it tells you to wait for a reset.

The thresholds mirror :func:`utils.status_icon`: < 70% is *safe*, 70-90% is
*warning*, 90-100% is *critical*, and >= 100% is *exhausted*.
"""

from __future__ import annotations

from .utils import CRIT, FAIL, OK, WARN, format_reset_in

STATUS_SAFE = "safe"  # < 70% used
STATUS_WARNING = "warning"  # 70-90% used
STATUS_CRITICAL = "critical"  # >= 90% used
STATUS_EXHAUSTED = "exhausted"  # >= 100% or error
STATUS_UNKNOWN = "unknown"  # no data / could not be measured

# Lower is better. Only the usable statuses appear here; "exhausted" and
# "unknown" are handled separately (a provider in either state is not a
# candidate for recommendation).
_SEVERITY = {STATUS_SAFE: 0, STATUS_WARNING: 1, STATUS_CRITICAL: 2}
_USABLE = (STATUS_SAFE, STATUS_WARNING, STATUS_CRITICAL)

_DISPLAY = {"claude": "Claude Code", "antigravity": "Antigravity"}
_STATUS_EMOJI = {
    STATUS_SAFE: OK,
    STATUS_WARNING: WARN,
    STATUS_CRITICAL: CRIT,
    STATUS_EXHAUSTED: FAIL,
    STATUS_UNKNOWN: "❓",
}


def classify(used_pct: float | None) -> str:
    """Classify a usage percentage into a status level.

    ``None`` (no data / error) maps to ``"unknown"``.
    """
    if used_pct is None:
        return STATUS_UNKNOWN
    if used_pct >= 100:
        return STATUS_EXHAUSTED
    if used_pct >= 90:
        return STATUS_CRITICAL
    if used_pct >= 70:
        return STATUS_WARNING
    return STATUS_SAFE


def _analyze_claude(c: dict | None) -> dict:
    """Reduce a Claude result, keeping per-window status (5h + 7d separately).

    ``windows`` is a list of ``{label, used_pct, status, resets_at}`` dicts —
    one per window that had data. The provider-level ``status`` is the *worst*
    window's status, and ``bottleneck_window`` is the window with the highest
    ``used_pct``.
    """
    windows: list[dict] = []
    if c and c.get("status") == "ok":
        for key, wlabel in (("five_hour", "5h"), ("seven_day", "7d")):
            w = c.get(key)
            if w and w.get("used_pct") is not None:
                used = w["used_pct"]
                windows.append(
                    {
                        "label": wlabel,
                        "used_pct": used,
                        "status": classify(used),
                        "resets_at": w.get("resets_at"),
                    }
                )

    if not windows:
        return {
            "status": STATUS_UNKNOWN,
            "highest_used_pct": None,
            "bottleneck_window": None,
            "resets_at": None,
            "windows": [],
        }

    bottleneck = max(windows, key=lambda x: x["used_pct"])
    return {
        "status": bottleneck["status"],
        "highest_used_pct": bottleneck["used_pct"],
        "bottleneck_window": bottleneck["label"],
        "resets_at": bottleneck["resets_at"],
        "windows": windows,
    }


def _analyze_antigravity(a: dict | None) -> dict:
    """Reduce an Antigravity result, keeping per-window status separately.

    ``windows`` is a list of ``{label, group, used_pct, status, resets_at}`` —
    one per group×bucket that had data. The provider-level ``status`` is the
    *worst* window's status, and ``bottleneck_window`` is the bucket with the
    highest ``used_pct``.
    """
    windows: list[dict] = []
    if a and a.get("status") == "ok":
        for grp in a.get("groups", []):
            gname = grp.get("name")
            for b in grp.get("buckets", []):
                if b.get("used_pct") is not None:
                    used = b["used_pct"]
                    windows.append(
                        {
                            "label": b.get("label") or b.get("window", "?"),
                            "window": b.get("window"),
                            "group": gname,
                            "used_pct": used,
                            "status": classify(used),
                            "resets_at": b.get("resets_at"),
                        }
                    )

    if not windows:
        return {
            "status": STATUS_UNKNOWN,
            "highest_used_pct": None,
            "bottleneck_window": None,
            "bottleneck_group": None,
            "resets_at": None,
            "windows": [],
        }

    bottleneck = max(windows, key=lambda x: x["used_pct"])
    return {
        "status": bottleneck["status"],
        "highest_used_pct": bottleneck["used_pct"],
        "bottleneck_window": bottleneck["window"],
        "bottleneck_group": bottleneck["group"],
        "resets_at": bottleneck["resets_at"],
        "windows": windows,
    }


def _decide(claude: dict, agy: dict) -> str:
    """Pick the recommended provider from two analyzed provider dicts."""
    cs, cu = claude["status"], claude["highest_used_pct"]
    as_, au = agy["status"], agy["highest_used_pct"]

    c_ok = cs in _USABLE
    a_ok = as_ in _USABLE

    if not c_ok and not a_ok:
        return "none"
    if c_ok and not a_ok:
        return "claude"
    if a_ok and not c_ok:
        return "antigravity"

    # Both are usable.
    if cs == STATUS_SAFE and as_ == STATUS_SAFE:
        return "either"
    if cs == STATUS_CRITICAL and as_ == STATUS_CRITICAL:
        return "none"  # both near-exhausted — nothing comfortable to switch to

    c_rank, a_rank = _SEVERITY[cs], _SEVERITY[as_]
    if c_rank != a_rank:
        return "claude" if c_rank < a_rank else "antigravity"
    # Same severity (e.g. both warning) — the one with more headroom wins.
    return "claude" if (cu or 0.0) <= (au or 0.0) else "antigravity"


def _describe(name: str, info: dict, with_window: bool) -> str:
    used = info["highest_used_pct"]
    if used is None:
        return f"{name} unavailable"
    win = info.get("bottleneck_window")
    where = f" {win}" if with_window and win else ""
    return f"{name}{where} at {used:.0f}% ({info['status']})"


def _action(recommended: str) -> str:
    if recommended == "either":
        return "Both have headroom — use either."
    if recommended == "none":
        return "All providers are near their limits; wait for a reset."
    return f"Switch to {_DISPLAY[recommended]}."


def _alternatives(claude: dict, agy: dict, recommended: str) -> list[str]:
    """Other providers (warning or better) worth falling back to."""
    if recommended in ("either", "none"):
        return []
    out: list[str] = []
    for key, label, info in (
        ("claude", "Claude", claude),
        ("antigravity", "Antigravity", agy),
    ):
        if key == recommended:
            continue
        if info["status"] in (STATUS_SAFE, STATUS_WARNING):
            out.append(f"{label} ({info['status']})")
    return out


def get_recommendation(fresh: bool = True) -> dict:
    """Analyze all providers and recommend which to use next.

    When ``fresh`` is ``True`` the usage data is gathered without the result
    cache; when ``False`` the 60s cache is used if available.
    """
    from .cli import gather

    data = gather(do_claude=True, do_antigravity=True, use_cache=not fresh)
    claude = _analyze_claude(data.get("claude"))
    agy = _analyze_antigravity(data.get("antigravity"))

    recommended = _decide(claude, agy)
    reason = (
        f"{_describe('Claude', claude, with_window=True)}, "
        f"{_describe('Antigravity', agy, with_window=False)}. {_action(recommended)}"
    )

    return {
        "providers": {"claude": claude, "antigravity": agy},
        "recommended_provider": recommended,
        "reason": reason,
        "alternatives": _alternatives(claude, agy, recommended),
    }


def _short_group(name: str | None) -> str | None:
    """``"Gemini Models"`` -> ``"Gemini"`` (drop a trailing "Models")."""
    if not name:
        return None
    for suffix in (" Models", " models"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _bottleneck_desc(name: str, info: dict) -> str | None:
    window = info.get("bottleneck_window")
    if name == "Antigravity":
        group = _short_group(info.get("bottleneck_group"))
        if group and window:
            return f"{group} {window}"
        return group or window
    return window


def _window_line(win: dict, indent: str = "      ") -> str:
    """Render a single window as a sub-line.

    Claude:    ``5h: ⚠️ 79.0% used (resets in 2h 15m)``
    Antigravity (grouped): ``Gemini Weekly: ✅ 7.5% used (resets in 2d 14h)``
    """
    status = win.get("status", STATUS_UNKNOWN)
    icon = _STATUS_EMOJI.get(status, "❓")
    used = win.get("used_pct")
    if used is None:
        return f"{indent}{win.get('label', '?')}: {icon} no data"
    # Antigravity windows carry a "group" — prefix the label so the two
    # groups (Gemini Models / Claude and GPT models) are distinguishable.
    label = win.get("label", "?")
    group = win.get("group")
    if group:
        label = f"{_short_group(group)} {label}"
    parts = [f"{used:.1f}% used"]
    if win.get("resets_at"):
        parts.append(f"resets in {format_reset_in(win['resets_at'])}")
    return f"{indent}{label}: {icon} {status} ({', '.join(parts)})"


def _provider_block(name: str, info: dict) -> list[str]:
    """Render a provider as a header line + one sub-line per window."""
    status = info.get("status", STATUS_UNKNOWN)
    icon = _STATUS_EMOJI.get(status, "❓")
    used = info.get("highest_used_pct")
    if used is None:
        return [f"  {name}: {icon} {status} (no data)"]

    header_parts = [f"{used:.1f}% used"]
    bottleneck = _bottleneck_desc(name, info)
    if bottleneck:
        header_parts.append(f"{bottleneck} bottleneck")
    if info.get("resets_at"):
        header_parts.append(f"resets in {format_reset_in(info['resets_at'])}")
    lines = [f"  {name}: {icon} {status} ({', '.join(header_parts)})"]

    windows = info.get("windows") or []
    for win in windows:
        lines.append(_window_line(win))
    return lines


def _headline(recommended: str) -> str:
    if recommended == "either":
        return "Either provider works"
    if recommended == "none":
        return "All providers near their limit — consider waiting"
    return f"Switch to {_DISPLAY.get(recommended, recommended)}"


def format_recommendation(rec: dict) -> str:
    """Render a recommendation dict as a human-readable block."""
    providers = rec.get("providers", {})
    lines = [
        f"🎯 Recommendation: {_headline(rec.get('recommended_provider', 'none'))}",
        "",
    ]
    lines.extend(_provider_block("Claude Code", providers.get("claude", {})))
    lines.extend(_provider_block("Antigravity", providers.get("antigravity", {})))
    lines.append("")
    lines.append(f"Reason: {rec.get('reason', '')}")
    alternatives = rec.get("alternatives") or []
    if alternatives:
        lines.append(f"Alternatives: {', '.join(alternatives)}")
    return "\n".join(lines)
