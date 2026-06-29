"""Minimal MCP (Model Context Protocol) server for ai-limit-checker.

Exposes two tools that AI agents (Claude Code, Hermes, etc.) can call
via the MCP JSON-RPC protocol over stdio:

- ``get_limits`` — returns current usage data (JSON, same as ``aichecker --json``)
- ``get_burn_rate`` — returns burn-rate analysis (velocity + ETA to 100%)

This is a lightweight stdio-only implementation with **zero external
dependencies** — it speaks the MCP wire protocol (JSON-RPC 2.0) directly
using only the Python standard library. No ``mcp`` SDK required.

To start the server::

    python -m ai_limit_checker.mcp_server

Or from the CLI::

    aichecker --mcp

Agents connect by spawning this process as an MCP server (stdio transport).
"""

from __future__ import annotations

import json
import sys
from typing import Any

# --- MCP protocol constants -----------------------------------------------

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "ai-limit-checker"
SERVER_VERSION = "0.9.0"

# Tool definitions exposed to the agent
TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_limits",
        "description": (
            "Check current usage limits for Claude Code and Antigravity CLI. "
            "Returns structured JSON with usage percentages, remaining quota, "
            "and reset timestamps for 5h and 7d windows."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "no_cache": {
                    "type": "boolean",
                    "description": "Ignore the 60s result cache and force fresh API calls.",
                    "default": False,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_burn_rate",
        "description": (
            "Analyze usage burn rate — how fast each limit window is being consumed "
            "and estimated time until the limit is hit. Requires multiple calls over "
            "time to build history; first call returns 'insufficient data'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "fresh": {
                    "type": "boolean",
                    "description": "Gather fresh usage data before calculating (default true).",
                    "default": True,
                },
            },
            "required": [],
        },
    },
]


# --- Tool handlers ---------------------------------------------------------


def _handle_get_limits(args: dict) -> dict:
    from .cli import gather

    no_cache = args.get("no_cache", False)
    result = gather(do_claude=True, do_antigravity=True, use_cache=not no_cache)
    return {"result": result}


def _handle_get_burn_rate(args: dict) -> dict:
    from .burn_rate import get_burn_rate

    fresh = args.get("fresh", True)
    rates = get_burn_rate(fresh=fresh)
    return {"result": rates}


_TOOL_HANDLERS = {
    "get_limits": _handle_get_limits,
    "get_burn_rate": _handle_get_burn_rate,
}


# --- JSON-RPC message handling --------------------------------------------


def _make_response(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _make_error(req_id: Any, code: int, message: str, data: Any = None) -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": error}


def _handle_request(msg: dict) -> dict | None:
    """Process a single JSON-RPC request and return a response (or None for notifications)."""
    method = msg.get("method", "")
    req_id = msg.get("id")
    params = msg.get("params", {})

    # --- lifecycle methods ---
    if method == "initialize":
        return _make_response(
            req_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    if method == "initialized" or method == "notifications/initialized":
        # Notification — no response needed
        return None

    if method == "ping":
        return _make_response(req_id, {})

    # --- tool methods ---
    if method == "tools/list":
        return _make_response(req_id, {"tools": TOOLS})

    if method == "tools/call":
        tool_name = params.get("name", "")
        handler = _TOOL_HANDLERS.get(tool_name)
        if handler is None:
            return _make_error(req_id, -32601, f"Unknown tool: {tool_name}")
        try:
            tool_args = params.get("arguments", {})
            if not isinstance(tool_args, dict):
                tool_args = {}
            output = handler(tool_args)
            # MCP expects content blocks in the result
            text = json.dumps(output["result"], indent=2)
            return _make_response(
                req_id,
                {"content": [{"type": "text", "text": text}]},
            )
        except Exception as exc:  # noqa: BLE001 — surface all errors to the agent
            return _make_error(req_id, -32603, f"Tool execution error: {exc}")

    return _make_error(req_id, -32601, f"Unknown method: {method}")


def _send(msg: dict) -> None:
    """Write a JSON-RPC message to stdout (MCP stdio transport)."""
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


# --- Main loop -------------------------------------------------------------


def serve() -> None:
    """Run the MCP server, reading JSON-RPC messages from stdin."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _send(_make_error(None, -32700, "Parse error"))
            continue

        if not isinstance(msg, dict):
            _send(_make_error(None, -32600, "Invalid Request"))
            continue

        response = _handle_request(msg)
        if response is not None:
            _send(response)


if __name__ == "__main__":
    serve()
