#!/usr/bin/env python3
"""
MCP server adapter for Agent Commit.

Implements the Model Context Protocol (stdio transport) so any MCP-compatible
agent (Claude Code, Codex, OpenCode, Hermes, etc.) can use agent_commit
as a tool without any configuration.

Run with: python -m agent_commit.mcp_server
Or install and use via MCP client configuration.
"""

import json
import sys
import threading
from typing import Any, Dict, List, Optional

from agent_commit import (
    CommitStore,
    agent_commit_tool,
    AGENT_COMMIT_SCHEMA,
    compute_commit_id,
    generate_summary,
    get_store,
)


# ---------------------------------------------------------------------------
# MCP protocol types (simplified)
# ---------------------------------------------------------------------------

class MCPError(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _json_response(result: Any, id_val: Any) -> Dict:
    return {"jsonrpc": "2.0", "id": id_val, "result": result}


def _json_error(code: int, message: str, id_val: Any) -> Dict:
    return {"jsonrpc": "2.0", "id": id_val, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# MCP request handlers
# ---------------------------------------------------------------------------

def handle_initialize(params: Dict, id_val: Any) -> Dict:
    return _json_response({
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {
            "name": "agent_commit",
            "version": "0.1.0",
        },
    }, id_val)


def handle_tools_list(params: Dict, id_val: Any) -> Dict:
    return _json_response({
        "tools": [{
            "name": AGENT_COMMIT_SCHEMA["name"],
            "description": AGENT_COMMIT_SCHEMA["description"],
            "inputSchema": AGENT_COMMIT_SCHEMA["parameters"],
        }]
    }, id_val)


def handle_tools_call(params: Dict, id_val: Any) -> Dict:
    try:
        name = params.get("name", "")
        arguments = params.get("arguments", {})

        if name != "agent_commit":
            return _json_error(-32602, f"Unknown tool: {name}", id_val)

        # The tool handler handles the actual logic
        result_str = agent_commit_tool(**arguments)

        # Parse and re-serialize to ensure consistent output
        parsed = json.loads(result_str)

        # Wrap in MCP tool call response format
        return _json_response({
            "content": [{
                "type": "text",
                "text": json.dumps(parsed, indent=2),
            }]
        }, id_val)

    except Exception as e:
        return _json_error(-32603, f"Tool execution error: {e}", id_val)


def handle_request(method: str, params: Dict, id_val: Any) -> Dict:
    if method == "initialize":
        return handle_initialize(params, id_val)
    elif method == "tools/list":
        return handle_tools_list(params, id_val)
    elif method == "tools/call":
        return handle_tools_call(params, id_val)
    elif method == "shutdown":
        return _json_response({"message": "ok"}, id_val)
    elif method == "ping":
        return _json_response({"message": "pong"}, id_val)
    else:
        return _json_error(-32601, f"Method not found: {method}", id_val)


# ---------------------------------------------------------------------------
# Main loop — JSON-RPC over stdio
# ---------------------------------------------------------------------------

def main():
    """Read JSON-RPC requests from stdin, write responses to stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }), file=sys.stderr)
            continue

        method = request.get("method", "")
        params = request.get("params", {})
        req_id = request.get("id")

        response = handle_request(method, params, req_id)
        print(json.dumps(response))


if __name__ == "__main__":
    main()