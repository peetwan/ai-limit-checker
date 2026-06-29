"""Tests for the MCP server module."""

import io
import json
import sys
from typing import Any

import pytest

from ai_limit_checker import mcp_server


@pytest.fixture(autouse=True)
def reset_mcp_initialized() -> None:
    """Reset the MCP server initialization state before each test."""
    mcp_server._initialized = False


def test_initialize() -> None:
    """The initialize method should return protocol version and server info."""
    msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    assert resp["id"] == 1
    result = resp["result"]
    assert result["protocolVersion"] == mcp_server.PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "ai-limit-checker"
    assert "tools" in result["capabilities"]


def test_initialized_notification_no_response() -> None:
    """The notifications/initialized notification should return None (no response)."""
    assert not mcp_server._initialized
    msg = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    resp = mcp_server._handle_request(msg)
    assert resp is None
    assert mcp_server._initialized


def test_ping() -> None:
    """The ping method should respond even if not initialized."""
    msg = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    assert resp["id"] == 2
    assert resp["result"] == {}


def test_tools_list() -> None:
    """The tools/list method should return list of tools after initialization."""
    mcp_server._initialized = True
    msg = {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    tools = resp["result"]["tools"]
    assert len(tools) == 4
    names = {t["name"] for t in tools}
    assert names == {"get_limits", "get_burn_rate", "get_history", "get_recommendation"}
    # Each tool must have a name, description, and inputSchema
    for t in tools:
        assert "name" in t
        assert "description" in t
        assert "inputSchema" in t


def test_unknown_method_returns_error() -> None:
    """An unknown method should return Method not found (-32601) after initialization."""
    mcp_server._initialized = True
    msg = {"jsonrpc": "2.0", "id": 4, "method": "nonexistent"}
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32601


def test_get_limits_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """The get_limits tool should return JSON content with usage data."""
    mcp_server._initialized = True
    fake_result = {
        "claude": {"status": "ok", "five_hour": {"used_pct": 30.0}},
        "antigravity": {"status": "ok"},
    }

    def fake_gather(
        do_claude: bool, do_antigravity: bool, use_cache: bool = True
    ) -> dict[str, Any]:
        return fake_result

    # Patch the gather function that _handle_get_limits imports at call time
    import ai_limit_checker.cli as cli_mod

    monkeypatch.setattr(cli_mod, "gather", fake_gather)

    msg = {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {"name": "get_limits", "arguments": {"no_cache": True}},
    }
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    assert "result" in resp
    content = resp["result"]["content"]
    assert len(content) == 1
    assert content[0]["type"] == "text"
    # The text content should be valid JSON containing the usage data
    parsed = json.loads(content[0]["text"])
    assert parsed["claude"]["status"] == "ok"
    assert parsed["claude"]["five_hour"]["used_pct"] == 30.0


def test_get_burn_rate_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """The get_burn_rate tool should return JSON content with burn-rate data."""
    mcp_server._initialized = True
    fake_rates = {
        "claude_five_hour": {
            "label": "Claude 5h",
            "used_pct": 40.0,
            "velocity_pct_per_hour": 20.0,
            "eta_seconds": 10800,
            "eta_text": "3h 0m",
            "samples": 5,
        },
    }

    def fake_get_burn_rate(fresh: bool = True) -> dict[str, Any]:
        return fake_rates

    import ai_limit_checker.burn_rate as br_mod

    monkeypatch.setattr(br_mod, "get_burn_rate", fake_get_burn_rate)

    msg = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "get_burn_rate", "arguments": {"fresh": True}},
    }
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    content = resp["result"]["content"]
    parsed = json.loads(content[0]["text"])
    assert parsed["claude_five_hour"]["velocity_pct_per_hour"] == 20.0


def test_get_history_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """The get_history tool should return per-window snapshot arrays as JSON."""
    mcp_server._initialized = True
    fake_history = {
        "claude_five_hour": [
            {"label": "Claude 5h", "used_pct": 30.0, "resets_at": None, "timestamp": 1.0},
            {"label": "Claude 5h", "used_pct": 40.0, "resets_at": None, "timestamp": 2.0},
        ],
    }

    def fake_get_history(
        window_id: Any = None, since: Any = None, limit: Any = None
    ) -> dict[str, Any]:
        return fake_history

    import ai_limit_checker.history as hist_mod

    monkeypatch.setattr(hist_mod, "get_history", fake_get_history)

    msg = {
        "jsonrpc": "2.0",
        "id": 20,
        "method": "tools/call",
        "params": {"name": "get_history", "arguments": {"window_id": "claude_five_hour"}},
    }
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    content = resp["result"]["content"]
    parsed = json.loads(content[0]["text"])
    assert parsed["claude_five_hour"][1]["used_pct"] == 40.0


def test_get_recommendation_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """The get_recommendation tool should return the recommendation dict as JSON."""
    mcp_server._initialized = True
    fake_rec = {
        "providers": {"claude": {}, "antigravity": {}},
        "recommended_provider": "antigravity",
        "reason": "Claude 5h at 92% (critical), Antigravity at 45% (safe). Switch to Antigravity.",
        "alternatives": [],
    }

    called_args = []

    def fake_get_recommendation(
        fresh: bool = True, exclude_groups: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        called_args.append((fresh, exclude_groups))
        return fake_rec

    import ai_limit_checker.recommend as rec_mod

    monkeypatch.setattr(rec_mod, "get_recommendation", fake_get_recommendation)

    msg = {
        "jsonrpc": "2.0",
        "id": 21,
        "method": "tools/call",
        "params": {
            "name": "get_recommendation",
            "arguments": {"fresh": False, "exclude_groups": ["Test Group"]},
        },
    }
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    content = resp["result"]["content"]
    parsed = json.loads(content[0]["text"])
    assert parsed["recommended_provider"] == "antigravity"
    assert len(called_args) == 1
    assert called_args[0] == (False, ("Test Group",))


def test_get_history_invalid_window_id() -> None:
    """get_history rejects a non-string window_id with -32602."""
    mcp_server._initialized = True
    msg = {
        "jsonrpc": "2.0",
        "id": 22,
        "method": "tools/call",
        "params": {"name": "get_history", "arguments": {"window_id": 123}},
    }
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    assert resp["error"]["code"] == -32602
    assert "window_id must be a string" in resp["error"]["message"]


def test_get_history_invalid_since() -> None:
    """get_history rejects a non-numeric since with -32602."""
    mcp_server._initialized = True
    msg = {
        "jsonrpc": "2.0",
        "id": 23,
        "method": "tools/call",
        "params": {"name": "get_history", "arguments": {"since": "soon"}},
    }
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    assert resp["error"]["code"] == -32602
    assert "since must be a number" in resp["error"]["message"]


def test_get_history_invalid_limit() -> None:
    """get_history rejects a non-integer limit with -32602."""
    mcp_server._initialized = True
    msg = {
        "jsonrpc": "2.0",
        "id": 24,
        "method": "tools/call",
        "params": {"name": "get_history", "arguments": {"limit": "10"}},
    }
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    assert resp["error"]["code"] == -32602
    assert "limit must be an integer" in resp["error"]["message"]


def test_get_recommendation_invalid_fresh() -> None:
    """get_recommendation rejects a non-boolean fresh with -32602."""
    mcp_server._initialized = True
    msg = {
        "jsonrpc": "2.0",
        "id": 25,
        "method": "tools/call",
        "params": {"name": "get_recommendation", "arguments": {"fresh": "yes"}},
    }
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    assert resp["error"]["code"] == -32602
    assert "fresh must be a boolean" in resp["error"]["message"]


def test_get_recommendation_invalid_exclude_groups() -> None:
    """get_recommendation rejects a non-array exclude_groups with -32602."""
    mcp_server._initialized = True
    msg = {
        "jsonrpc": "2.0",
        "id": 26,
        "method": "tools/call",
        "params": {"name": "get_recommendation", "arguments": {"exclude_groups": "not-an-array"}},
    }
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    assert resp["error"]["code"] == -32602
    assert "exclude_groups must be an array of strings" in resp["error"]["message"]


def test_make_response_structure() -> None:
    """Validate helper for response structures."""
    resp = mcp_server._make_response(42, {"data": "test"})
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 42
    assert resp["result"] == {"data": "test"}


def test_make_error_structure() -> None:
    """Validate helper for error structures."""
    resp = mcp_server._make_error(99, -32600, "Bad request", {"extra": "info"})
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 99
    assert resp["error"]["code"] == -32600
    assert resp["error"]["message"] == "Bad request"
    assert resp["error"]["data"]["extra"] == "info"


# --- New MCP compliance tests ---------------------------------------------


def test_parse_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed JSON returns a -32700 Parse error response."""
    stdin = io.StringIO("invalid json\n")
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)

    mcp_server.serve()

    output = stdout.getvalue().strip()
    resp = json.loads(output)
    assert resp["error"]["code"] == -32700


def test_invalid_request_non_dict() -> None:
    """Non-dictionary input returns a -32600 Invalid Request response."""
    resp = mcp_server._handle_request([])  # type: ignore
    assert resp is not None
    assert resp["error"]["code"] == -32600


def test_notification_no_response() -> None:
    """Any notification with an unknown method must return None (no response)."""
    msg = {"jsonrpc": "2.0", "method": "unknown/notification"}
    resp = mcp_server._handle_request(msg)
    assert resp is None


def test_notification_initialized_no_response() -> None:
    """The notifications/initialized and initialized notifications set state and return None."""
    assert not mcp_server._initialized
    msg = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    resp = mcp_server._handle_request(msg)
    assert resp is None
    assert mcp_server._initialized

    mcp_server._initialized = False
    msg_alt = {"jsonrpc": "2.0", "method": "initialized"}
    resp_alt = mcp_server._handle_request(msg_alt)
    assert resp_alt is None
    assert mcp_server._initialized


def test_unknown_tool_invalid_params() -> None:
    """An unknown tool name returns -32602 (Invalid params) error, not -32601."""
    mcp_server._initialized = True
    msg = {
        "jsonrpc": "2.0",
        "id": 12,
        "method": "tools/call",
        "params": {"name": "nonexistent", "arguments": {}},
    }
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32602


def test_missing_tool_name() -> None:
    """Call to tools/call without a name parameter returns -32602 error."""
    mcp_server._initialized = True
    msg = {
        "jsonrpc": "2.0",
        "id": 14,
        "method": "tools/call",
        "params": {"arguments": {}},
    }
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32602
    assert "name parameter is required" in resp["error"]["message"]


def test_invalid_arguments_type() -> None:
    """Passing arguments as a non-dictionary (e.g. list) returns -32602 error."""
    mcp_server._initialized = True
    msg = {
        "jsonrpc": "2.0",
        "id": 13,
        "method": "tools/call",
        "params": {"name": "get_limits", "arguments": ["no_cache"]},
    }
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32602
    assert "arguments must be a dictionary" in resp["error"]["message"]


def test_tool_failure_returns_isError(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tool handler throwing an exception returns a success response with isError: true."""
    mcp_server._initialized = True

    def fake_get_burn_rate(fresh: bool = True) -> Any:
        raise RuntimeError("API connection failure")

    import ai_limit_checker.burn_rate as br_mod

    monkeypatch.setattr(br_mod, "get_burn_rate", fake_get_burn_rate)

    msg = {
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {"name": "get_burn_rate", "arguments": {}},
    }
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    assert "result" in resp
    assert resp["result"]["isError"] is True
    assert "content" in resp["result"]
    assert "Tool execution error: API connection failure" in resp["result"]["content"][0]["text"]


def test_not_initialized_error() -> None:
    """Any request (except initialize/ping) before handshake completion returns -32002."""
    assert not mcp_server._initialized
    msg = {
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/list",
        "params": {},
    }
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32002
    assert "Server not initialized" in resp["error"]["message"]


def test_missing_jsonrpc_field() -> None:
    """A request message missing the jsonrpc field returns a -32600 Invalid Request response."""
    msg = {
        "id": 15,
        "method": "ping",
    }
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32600
    assert "Invalid Request" in resp["error"]["message"]


def test_invalid_jsonrpc_version() -> None:
    """A request message with an invalid jsonrpc version returns a -32600 Invalid Request response."""
    msg = {
        "jsonrpc": "1.0",
        "id": 16,
        "method": "ping",
    }
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32600
    assert "Invalid Request" in resp["error"]["message"]
