"""Tests for time formatting, status icons, and ISO parsing."""

from datetime import datetime, timezone

from ai_limit_checker import utils


def test_format_duration_days():
    assert utils.format_duration(234000) == "2d 17h"


def test_format_duration_hours():
    assert utils.format_duration(17760) == "4h 56m"


def test_format_duration_minutes():
    assert utils.format_duration(300) == "5m"


def test_format_duration_clamps_negative():
    assert utils.format_duration(-50) == "0m"


def test_parse_iso_with_z():
    dt = utils.parse_iso("2026-06-29T09:30:00Z")
    assert dt == datetime(2026, 6, 29, 9, 30, tzinfo=timezone.utc)


def test_parse_iso_without_seconds():
    dt = utils.parse_iso("2026-06-29T09:53Z")
    assert dt == datetime(2026, 6, 29, 9, 53, tzinfo=timezone.utc)


def test_parse_iso_naive_assumes_utc():
    dt = utils.parse_iso("2026-06-29T09:30:00")
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_iso_none_and_garbage():
    assert utils.parse_iso(None) is None
    assert utils.parse_iso("not-a-date") is None


def test_format_reset_in():
    now = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)
    assert utils.format_reset_in("2026-06-29T16:56:00Z", now=now) == "4h 56m"


def test_format_reset_in_past_is_now():
    now = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)
    assert utils.format_reset_in("2026-06-29T11:00:00Z", now=now) == "now"


def test_format_reset_in_unknown():
    assert utils.format_reset_in(None) == "unknown"


def test_status_icon_thresholds():
    assert utils.status_icon(1.0) == utils.OK
    assert utils.status_icon(69.9) == utils.OK
    assert utils.status_icon(70.0) == utils.WARN
    assert utils.status_icon(89.9) == utils.WARN
    assert utils.status_icon(90.0) == utils.CRIT
    assert utils.status_icon(99.9) == utils.CRIT
    assert utils.status_icon(100.0) == utils.FAIL
    assert utils.status_icon(None) == utils.FAIL


def test_status_icon_remaining():
    assert utils.status_icon_remaining(99.0) == utils.OK  # 1% used
    assert utils.status_icon_remaining(65.0) == utils.OK  # 35% used
    assert utils.status_icon_remaining(20.0) == utils.WARN  # 80% used
    assert utils.status_icon_remaining(0.0) == utils.FAIL  # 100% used
    assert utils.status_icon_remaining(None) == utils.FAIL


def test_normalize_iso():
    assert utils.normalize_iso("2026-06-29T09:30:00.860699+00:00") == "2026-06-29T09:30:00Z"
    assert utils.normalize_iso("2026-06-29T18:00Z") == "2026-06-29T18:00:00Z"
    assert utils.normalize_iso(None) is None
    assert utils.normalize_iso("garbage") == "garbage"  # unparseable: passed through


def test_format_pct():
    assert utils.format_pct(56.0) == "56.0%"
    assert utils.format_pct(65.0, 0) == "65%"
    assert utils.format_pct(None) == "n/a"
