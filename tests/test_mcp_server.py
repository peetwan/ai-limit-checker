"""Tests for the MCP server module."""

import json

from ai_limit_checker import mcp_server


def test_initialize():
    """The initialize method should return protocol version and server info."""
    msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    assert resp["id"] == 1
    result = resp["result"]
    assert result["protocolVersion"] == mcp_server.PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "ai-limit-checker"
    assert "tools" in result["capabilities"]


def test_initialized_notification_no_response():
    """The notifications/initialized notification should return None (no response)."""
    msg = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    resp = mcp_server._handle_request(msg)
    assert resp is None


def test_ping():
    msg = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    assert resp["id"] == 2
    assert resp["result"] == {}


def test_tools_list():
    msg = {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    tools = resp["result"]["tools"]
    assert len(tools) == 2
    names = {t["name"] for t in tools}
    assert names == {"get_limits", "get_burn_rate"}
    # Each tool must have a name, description, and inputSchema
    for t in tools:
        assert "name" in t
        assert "description" in t
        assert "inputSchema" in t


def test_unknown_method_returns_error():
    msg = {"jsonrpc": "2.0", "id": 4, "method": "nonexistent"}
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32601


def test_unknown_tool_returns_error():
    msg = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"name": "nonexistent_tool", "arguments": {}},
    }
    resp = mcp_server._handle_request(msg)
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32601


def test_get_limits_tool(monkeypatch):
    """The get_limits tool should return JSON content with usage data."""
    fake_result = {
        "claude": {"status": "ok", "five_hour": {"used_pct": 30.0}},
        "antigravity": {"status": "ok"},
    }

    def fake_gather(do_claude, do_antigravity, use_cache=True):
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


def test_get_burn_rate_tool(monkeypatch):
    """The get_burn_rate tool should return JSON content with burn-rate data."""
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

    def fake_get_burn_rate(fresh=True):
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


def test_tool_handler_exception_returns_internal_error(monkeypatch):
    """If a tool handler raises, the error should be surfaced, not crash the server."""
    def fake_get_burn_rate(fresh=True):
        raise RuntimeError("API down")

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
    assert "error" in resp
    assert resp["error"]["code"] == -32603
    assert "API down" in resp["error"]["message"]


def test_make_response_structure():
    resp = mcp_server._make_response(42, {"data": "test"})
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 42
    assert resp["result"] == {"data": "test"}


def test_make_error_structure():
    resp = mcp_server._make_error(99, -32600, "Bad request", {"extra": "info"})
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 99
    assert resp["error"]["code"] == -32600
    assert resp["error"]["message"] == "Bad request"
    assert resp["error"]["data"]["extra"] == "info"
