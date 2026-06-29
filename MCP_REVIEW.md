# MCP Server Implementation Review

This document contains the review of the Model Context Protocol (MCP) server implementation in the `ai-limit-checker` repository.

## What Works

The current MCP server implementation successfully handles standard stdio-based JSON-RPC communication:
* Stdio transport: Correctly reads JSON-RPC messages from `sys.stdin` and writes response JSON lines to `sys.stdout`.
* JSON-RPC 2.0 framing and parsing: Successfully handles basic JSON framing, parses incoming messages, and catches malformed JSON strings.
* Basic lifecycle: Supports the `initialize` method to negotiate protocol version, capabilities, and server metadata.
* Tool listing: Correctly exposes the `get_limits` and `get_burn_rate` tools via the `tools/list` method.
* Tool invocation: Runs registered tools through the `tools/call` method, gathers metrics, and formats output as valid MCP text content blocks.
* Clean error structure: Correctly returns standard JSON-RPC error formats for syntax errors, invalid requests, and unrecognized methods.
* Notification handling: Correctly ignores the lifecycle notification `notifications/initialized` without returning a response.
* Unit testing: The existing test suite provides test coverage for correct lifecycle flows and mocked tool execution.

## Protocol Compliance

The implementation implements a subset of the MCP specification and JSON-RPC 2.0 standard:

### Implemented Methods
* `initialize` (Request)
* `tools/list` (Request)
* `tools/call` (Request)
* `ping` (Request)
* `initialized` / `notifications/initialized` (Notifications)

### Missing Methods (Optional for Simple Tool Servers)
* `notifications/cancelled` (Request cancellation)
* `logging/setLevel` (Configuring server log levels)
* `resources/*` (Exposing resources)
* `prompts/*` (Exposing prompts/templates)

### Protocol Deviations & Non-Compliance Gaps
1. Response to Notifications: According to the JSON-RPC 2.0 specification, notifications (messages without an `id` member) must not receive any response from the server, even if they have errors. The current implementation returns a JSON-RPC error with `id: null` if a notification has an unknown method.
2. Tool Execution Failures: According to the MCP spec, if a tool's internal execution fails (e.g. throwing a runtime exception), the server should return a successful JSON-RPC response with `isError: true` inside the result block and include details in the content array. The current implementation returns a JSON-RPC error with code `-32603`.
3. Lifecycle Enforcement: The MCP specification requires that clients first complete the initialization handshake (`initialize` request followed by `initialized` notification) before sending any other requests. The current server processes `tools/list` or `tools/call` without enforcing this lifecycle state.
4. JSON-RPC 2.0 Validation: The server does not validate the presence and exact value of the `jsonrpc` member (which must be `"2.0"`).

## Issues Found

During manual testing with raw JSON-RPC messages and boundary conditions, the following bugs and gaps were identified:

1. Notification Response Bug
   * Input: `{"jsonrpc": "2.0", "method": "unknown/notification"}` (no `id`)
   * Observed: Server returns a JSON-RPC error response `{"jsonrpc": "2.0", "id": null, "error": {...}}`.
   * Standard: The server must never reply to a notification.

2. Wrong Error Code for Unknown Tools
   * Input: `{"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {"name": "nonexistent"}}`
   * Observed: Returns `-32601` (Method not found) with the message `Unknown tool: nonexistent`.
   * Standard: Since the method `tools/call` exists, the error lies in the parameter value. The correct error code is `-32602` (Invalid params).

3. Silent Fallback for Invalid Arguments
   * Input: `{"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": {"name": "get_limits", "arguments": ["no_cache"]}}` (where `arguments` is a list)
   * Observed: The server silently falls back to empty arguments `{}` and runs successfully.
   * Standard: The input schema requires an object. Silent coercion hides client errors. It should fail with an Invalid Params (`-32602`) error.

4. Tool Execution Errors
   * Input: A tool call that causes an exception (e.g. connection timeout or syntax error).
   * Observed: Returns a JSON-RPC error with code `-32603` (Internal error).
   * Standard: Should return `{"content": [{"type": "text", "text": "error details..."}], "isError": true}`.

5. Missing `params` / `name` Validation
   * Input: `{"jsonrpc": "2.0", "id": 14, "method": "tools/call"}`
   * Observed: Fails with `Unknown tool: ` and error code `-32601`.
   * Standard: Should return a clear Invalid Params (`-32602`) error indicating that the `name` parameter is required.

6. Untested Edge Cases in Unit Tests
   The existing `tests/test_mcp_server.py` does not verify:
   * Parse errors from malformed JSON.
   * Invalid requests (e.g. non-dictionary inputs).
   * Notification suppression (checking that no response is written).
   * Schema mismatch / invalid argument types.
   * Out-of-order lifecycle calls.

## Recommendations

To improve compliance and robustness of the MCP server, the following changes are recommended:

1. Suppress Notification Responses
   Update `serve()` or `_handle_request()` to check if the incoming request contains an `id` field. If `id` is missing, it is a notification; any generated error response should be dropped silently rather than written to `sys.stdout`.

2. Return CallToolResult for Tool Failures
   Refactor the `tools/call` handler block:
   ```python
   except Exception as exc:
       return _make_response(
           req_id,
           {
               "content": [{"type": "text", "text": f"Tool execution error: {exc}"}],
               "isError": True
           }
       )
   ```

3. Standardize Error Codes
   * Use `-32602` (Invalid params) when the tool name is unknown, missing, or when argument types do not match expectations.
   * Enforce JSON-RPC `"2.0"` validation and return `-32600` (Invalid Request) if missing or incorrect.

4. Track Initialization State
   Introduce a state variable (e.g., `_initialized = False`) that is set to `True` upon receiving `initialized` / `notifications/initialized`. For any other request received while `_initialized` is `False`, return error code `-32002` with the message `Server not initialized`.

5. Enhance Input Argument Validation
   Implement lightweight type-checking for parameters (e.g., verifying that `arguments` is a dict, and checking that options like `no_cache` or `fresh` are indeed booleans if provided), rather than silently ignoring invalid types.

6. Expand Test Coverage
   Add tests in `tests/test_mcp_server.py` covering malformed input, invalid JSON-RPC requests, notification behavior, and parameter validation.
