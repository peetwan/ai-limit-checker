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

from datetime import datetime, timezone

from .utils import CRIT, FAIL, OK, WARN, format_reset_in, parse_iso

STATUS_SAFE = "safe"  # < 70% used
STATUS_WARNING = "warning"  # 70-90% used
STATUS_CRITICAL = "critical"  # >= 90% used
STATUS_EXHAUSTED = "exhausted"  # >= 100% or error
STATUS_UNKNOWN = "unknown"  # no data / could not be measured

DEFAULT_EXCLUDE_GROUPS = ("Claude and GPT models",)

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


def _analyze_antigravity(a: dict | None, exclude_groups: tuple[str, ...] = ()) -> dict:
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
            if gname in exclude_groups:
                continue
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


def _score_severity(status: str) -> float:
    """Map provider status to a 0-100 score."""
    return {
        STATUS_SAFE: 100.0,
        STATUS_WARNING: 50.0,
        STATUS_CRITICAL: 15.0,
        STATUS_EXHAUSTED: 0.0,
        STATUS_UNKNOWN: 0.0,
    }.get(status, 0.0)


def _score_headroom(windows: list[dict]) -> float:
    """min(remaining_pct) across all windows. No windows = 0."""
    if not windows:
        return 0.0
    remaining = [100.0 - w["used_pct"] for w in windows if w.get("used_pct") is not None]
    return min(remaining) if remaining else 0.0


def _score_reset_proximity(info: dict, now: datetime | None = None) -> float:
    """Score based on how soon the worst window resets."""
    windows = info.get("windows") or []
    if not windows:
        return 30.0
    # Find worst window (highest used_pct)
    worst = max(windows, key=lambda w: w.get("used_pct", 0))
    used = worst.get("used_pct", 0)

    # If the worst window is safe, no urgency concern
    if used < 70:
        return 100.0

    # Warning/critical — how soon does it reset?
    resets_at = worst.get("resets_at")
    if not resets_at:
        return 30.0

    target = parse_iso(resets_at)
    if target is None:
        return 30.0
    if now is None:
        now = datetime.now(timezone.utc)
    hours_until = max(0, (target - now).total_seconds() / 3600)

    if hours_until < 1:
        return 80.0
    if hours_until < 4:
        return 60.0
    if hours_until < 12:
        return 40.0
    if hours_until < 24:
        return 20.0
    return 10.0


def _score_burn_rate(provider_name: str) -> float:
    """Score based on burn rate velocity from history."""
    from .burn_rate import _load_history, calculate_burn_rate

    history = _load_history()
    rates = calculate_burn_rate(history)

    # Find ALL windows for this provider, pick the highest velocity (worst burn rate)
    provider_rates = []
    for wid, rate in rates.items():
        if (
            provider_name == "claude"
            and wid.startswith("claude_")
            or provider_name == "antigravity"
            and wid.startswith("agy_")
        ):
            velocity = rate.get("velocity_pct_per_hour")
            if velocity is not None:
                provider_rates.append(velocity)

    if not provider_rates:
        return 50.0  # no data — neutral

    worst_velocity = max(provider_rates)  # highest burn rate = worst
    if worst_velocity < 0:
        return 90.0  # usage going down
    if worst_velocity <= 2:
        return 80.0
    if worst_velocity <= 5:
        return 60.0
    if worst_velocity <= 10:
        return 40.0
    return 20.0


def _compute_score(
    info: dict, provider_name: str, now: datetime | None = None
) -> tuple[float, dict]:
    """Compute composite score. Returns (score, breakdown dict)."""
    status = info.get("status", STATUS_UNKNOWN)
    severity = _score_severity(status)
    headroom = _score_headroom(info.get("windows") or [])
    reset_prox = _score_reset_proximity(info, now=now)
    burn = _score_burn_rate(provider_name)

    score = severity * 0.35 + headroom * 0.30 + reset_prox * 0.20 + burn * 0.15

    if status in (STATUS_EXHAUSTED, STATUS_UNKNOWN):
        score = 0.0
    elif status == STATUS_CRITICAL:
        score = 1.0 + (score / 100.0) * 8.9

    return score, {
        "severity": round(severity, 1),
        "headroom": round(headroom, 1),
        "reset_proximity": round(reset_prox, 1),
        "burn_rate": round(burn, 1),
    }


def _decide_by_score(
    claude_score: float, agy_score: float, claude: dict, agy: dict
) -> tuple[str, str]:
    """Returns (recommended_provider, reason_text)."""

    # Both exhausted/unavailable
    if claude_score < 10 and agy_score < 10:
        return "none", "Both providers are nearly exhausted. Wait for a reset."

    # One is unavailable, other is usable
    if claude_score < 10:
        return (
            "antigravity",
            f"Claude is exhausted/unavailable (score {claude_score:.0f}). Use Antigravity.",
        )
    if agy_score < 10:
        return (
            "claude",
            f"Antigravity is exhausted/unavailable (score {agy_score:.0f}). Use Claude.",
        )

    diff = abs(claude_score - agy_score)

    if diff < 10:
        # Close — either is fine
        return (
            "either",
            f"Both are comparable (Claude {claude_score:.0f}, Antigravity {agy_score:.0f}). Use either.",
        )

    # Clear winner
    if claude_score > agy_score:
        return (
            "claude",
            f"Claude scores higher ({claude_score:.0f} vs {agy_score:.0f}). Antigravity is {diff:.0f} points lower.",
        )
    else:
        return (
            "antigravity",
            f"Antigravity scores higher ({agy_score:.0f} vs {claude_score:.0f}). Claude is {diff:.0f} points lower.",
        )


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


def get_recommendation(
    fresh: bool = True,
    exclude_groups: tuple[str, ...] = DEFAULT_EXCLUDE_GROUPS,
    now: datetime | None = None,
) -> dict:
    """Analyze all providers and recommend which to use next.

    When ``fresh`` is ``True`` the usage data is gathered without the result
    cache; when ``False`` the 60s cache is used if available.
    """
    from .cli import gather

    data = gather(do_claude=True, do_antigravity=True, use_cache=not fresh)
    claude = _analyze_claude(data.get("claude"))
    agy = _analyze_antigravity(data.get("antigravity"), exclude_groups=exclude_groups)

    claude_score, claude_breakdown = _compute_score(claude, "claude", now=now)
    agy_score, agy_breakdown = _compute_score(agy, "antigravity", now=now)

    claude["score"] = round(claude_score, 1)
    claude["score_breakdown"] = claude_breakdown
    agy["score"] = round(agy_score, 1)
    agy["score_breakdown"] = agy_breakdown

    recommended, reason = _decide_by_score(claude_score, agy_score, claude, agy)

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
    score_suffix = ""
    if "score" in info:
        score_suffix = f" — score: {info['score']:.0f}"

    if used is None:
        return [f"  {name}: {icon} {status} (no data){score_suffix}"]

    header_parts = [f"{used:.1f}% used"]
    bottleneck = _bottleneck_desc(name, info)
    if bottleneck:
        header_parts.append(f"{bottleneck} bottleneck")
    if info.get("resets_at"):
        header_parts.append(f"resets in {format_reset_in(info['resets_at'])}")
    lines = [f"  {name}: {icon} {status} ({', '.join(header_parts)}){score_suffix}"]

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
