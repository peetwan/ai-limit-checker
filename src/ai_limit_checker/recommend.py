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
    """Reduce a Claude result to its single most-constrained window."""
    windows: list[tuple[float, str, str | None]] = []
    if c and c.get("status") == "ok":
        for key, wlabel in (("five_hour", "5h"), ("seven_day", "7d")):
            w = c.get(key)
            if w and w.get("used_pct") is not None:
                windows.append((w["used_pct"], wlabel, w.get("resets_at")))

    if not windows:
        return {
            "status": STATUS_UNKNOWN,
            "highest_used_pct": None,
            "bottleneck_window": None,
            "resets_at": None,
        }

    used, wlabel, resets = max(windows, key=lambda t: t[0])
    return {
        "status": classify(used),
        "highest_used_pct": used,
        "bottleneck_window": wlabel,
        "resets_at": resets,
    }


def _analyze_antigravity(a: dict | None) -> dict:
    """Reduce an Antigravity result to its single most-constrained bucket."""
    buckets: list[tuple[float, str | None, str | None, str | None]] = []
    if a and a.get("status") == "ok":
        for grp in a.get("groups", []):
            gname = grp.get("name")
            for b in grp.get("buckets", []):
                if b.get("used_pct") is not None:
                    buckets.append((b["used_pct"], b.get("window"), gname, b.get("resets_at")))

    if not buckets:
        return {
            "status": STATUS_UNKNOWN,
            "highest_used_pct": None,
            "bottleneck_window": None,
            "bottleneck_group": None,
            "resets_at": None,
        }

    used, window, gname, resets = max(buckets, key=lambda t: t[0])
    return {
        "status": classify(used),
        "highest_used_pct": used,
        "bottleneck_window": window,
        "bottleneck_group": gname,
        "resets_at": resets,
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


def _provider_line(name: str, info: dict) -> str:
    status = info.get("status", STATUS_UNKNOWN)
    icon = _STATUS_EMOJI.get(status, "❓")
    used = info.get("highest_used_pct")
    if used is None:
        return f"{name}: {icon} {status} (no data)"

    parts = [f"{used:.1f}% used"]
    bottleneck = _bottleneck_desc(name, info)
    if bottleneck:
        parts.append(f"{bottleneck} bottleneck")
    if info.get("resets_at"):
        parts.append(f"resets in {format_reset_in(info['resets_at'])}")
    return f"{name}: {icon} {status} ({', '.join(parts)})"


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
        "  " + _provider_line("Claude Code", providers.get("claude", {})),
        "  " + _provider_line("Antigravity", providers.get("antigravity", {})),
        "",
        f"Reason: {rec.get('reason', '')}",
    ]
    alternatives = rec.get("alternatives") or []
    if alternatives:
        lines.append(f"Alternatives: {', '.join(alternatives)}")
    return "\n".join(lines)
