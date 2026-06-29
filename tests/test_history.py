"""Tests for the usage-history timeseries module."""

from ai_limit_checker import burn_rate, history

# A fixed reference time keeps the snapshot timestamps deterministic.
NOW = 1_700_000_000.0


def _sample_history() -> dict[str, list[dict]]:
    return {
        "claude_five_hour": [
            {"label": "Claude 5h", "used_pct": 45.0, "resets_at": None, "timestamp": NOW - 3600},
            {"label": "Claude 5h", "used_pct": 52.0, "resets_at": None, "timestamp": NOW - 1800},
            {"label": "Claude 5h", "used_pct": 58.0, "resets_at": None, "timestamp": NOW},
        ],
        "claude_seven_day": [
            {"label": "Claude 7d", "used_pct": 10.0, "resets_at": None, "timestamp": NOW - 1800},
            {"label": "Claude 7d", "used_pct": 12.0, "resets_at": None, "timestamp": NOW},
        ],
    }


def _seed(monkeypatch, tmp_path, data: dict) -> None:
    monkeypatch.setattr(burn_rate, "HISTORY_FILE", tmp_path / "burn_rate.json")
    monkeypatch.setattr(burn_rate, "CACHE_DIR", tmp_path)
    burn_rate._save_history(data)


# --- get_history -----------------------------------------------------------


def test_get_history_all(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _sample_history())
    result = history.get_history()
    assert set(result) == {"claude_five_hour", "claude_seven_day"}
    assert len(result["claude_five_hour"]) == 3
    assert len(result["claude_seven_day"]) == 2


def test_get_history_filter_window(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _sample_history())
    result = history.get_history(window_id="claude_five_hour")
    assert set(result) == {"claude_five_hour"}
    assert len(result["claude_five_hour"]) == 3


def test_get_history_filter_window_missing(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _sample_history())
    assert history.get_history(window_id="does_not_exist") == {}


def test_get_history_since(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _sample_history())
    result = history.get_history(since=NOW - 1000)
    # Only snapshots strictly after NOW-1000 remain (the most recent of each).
    assert len(result["claude_five_hour"]) == 1
    assert result["claude_five_hour"][0]["used_pct"] == 58.0
    assert len(result["claude_seven_day"]) == 1


def test_get_history_limit(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _sample_history())
    result = history.get_history(limit=2)
    assert len(result["claude_five_hour"]) == 2
    # Most recent two, in order.
    assert [s["used_pct"] for s in result["claude_five_hour"]] == [52.0, 58.0]


def test_get_history_limit_zero(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _sample_history())
    result = history.get_history(limit=0)
    assert result["claude_five_hour"] == []
    assert result["claude_seven_day"] == []


def test_get_history_empty(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, {})
    assert history.get_history() == {}


def test_load_history_alias(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _sample_history())
    assert history.load_history() == burn_rate._load_history()


# --- format_history --------------------------------------------------------


def test_format_history_basic():
    text = history.format_history(_sample_history())
    assert "Claude 5h  (3 samples)" in text
    assert "45.0% used" in text
    assert "52.0% used" in text
    # Deltas from the previous sample are shown in parentheses.
    assert "(+7.0)" in text
    assert "(+6.0)" in text
    # The first sample of a window has no delta.
    first_line = next(line for line in text.splitlines() if "45.0% used" in line)
    assert "(" not in first_line


def test_format_history_singular_sample():
    text = history.format_history(
        {"claude_five_hour": [{"label": "Claude 5h", "used_pct": 5.0, "timestamp": NOW}]}
    )
    assert "(1 sample)" in text


def test_format_history_window_filter():
    text = history.format_history(_sample_history(), window_id="claude_seven_day")
    assert "Claude 7d" in text
    assert "Claude 5h" not in text


def test_format_history_empty():
    assert "No history yet" in history.format_history({})


def test_format_history_empty_after_filter():
    # Window exists but filtering removed all of its samples.
    assert "No history yet" in history.format_history({"claude_five_hour": []})


# --- clear_history ---------------------------------------------------------


def test_clear_history_all(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _sample_history())
    cleared = history.clear_history()
    assert cleared == 2
    assert burn_rate._load_history() == {}


def test_clear_history_one_window(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _sample_history())
    cleared = history.clear_history(window_id="claude_five_hour")
    assert cleared == 1
    remaining = burn_rate._load_history()
    assert set(remaining) == {"claude_seven_day"}


def test_clear_history_missing_window(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _sample_history())
    assert history.clear_history(window_id="nope") == 0
    assert set(burn_rate._load_history()) == {"claude_five_hour", "claude_seven_day"}


def test_clear_history_empty(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, {})
    assert history.clear_history() == 0
