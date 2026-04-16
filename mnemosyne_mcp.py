"""
mnemosyne_mcp.py — minimal Model Context Protocol client + server.

Purpose
-------
Mnemosyne already has a skill registry (agentskills.io-compatible).
MCP is the ecosystem's other standard — Anthropic's JSON-RPC-over-stdio
contract that Claude Desktop, IDE extensions, and many framework
integrations speak. Supporting both means Mnemosyne skills work for
third-party agents and external MCP servers (filesystem, database,
browser) work inside Mnemosyne.

This module implements the minimum useful subset:

  Client (Mnemosyne consumes external MCP servers as skills):
    - spawn the server subprocess
    - send `initialize`
    - call `tools/list`
    - register each tool as a Mnemosyne Skill
    - `tools/call` on invoke

  Server (Mnemosyne exposes its skills as MCP tools):
    - read JSON-RPC over stdin
    - handle `initialize`, `tools/list`, `tools/call`
    - emit responses to stdout (one JSON object per line)

We support the core methods only. No prompts, no resources, no
sampling callbacks. Those are roadmap — call out if you need them.

Wire format (spec subset)
-------------------------
Request:
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {...}}
Response:
    {"jsonrpc": "2.0", "id": 1, "result": {...}}
Error:
    {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "..."}}

One message per line. No framing beyond the newline.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import Any


# ---- JSON-RPC helpers -------------------------------------------------------

def _jsonrpc_request(method: str, params: dict[str, Any] | None,
                      request_id: int) -> dict[str, Any]:
    msg = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def _jsonrpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str,
                    data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": err}


# ---- MCP client: consume external servers ----------------------------------

@dataclass
class MCPClient:
    """Wrap an external MCP server subprocess."""

    command: list[str]
    env: dict[str, str] | None = None
    timeout_s: float = 30.0

    _proc: subprocess.Popen | None = field(default=None, init=False, repr=False)
    _next_id: int = field(default=1, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __enter__(self) -> "MCPClient":
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()

    def start(self) -> None:
        self._proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
            text=True,
            bufsize=1,
        )
        # Initialize handshake
        self._call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "mnemosyne", "version": "0.2.0"},
        })

    def stop(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.stdin.close()
            self._proc.wait(timeout=2.0)
        except Exception:
            self._proc.kill()
        self._proc = None

    def _call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        with self._lock:
            if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
                raise RuntimeError("mcp client not started")
            rid = self._next_id
            self._next_id += 1
            req = _jsonrpc_request(method, params, rid)
            self._proc.stdin.write(json.dumps(req) + "\n")
            self._proc.stdin.flush()

            # Read responses until we get the one matching our id
            while True:
                line = self._proc.stdout.readline()
                if not line:
                    raise RuntimeError("mcp server closed stdout unexpectedly")
                try:
                    msg = json.loads(line.strip())
                except json.JSONDecodeError:
                    continue
                if msg.get("id") != rid:
                    # Ignore notifications / out-of-order messages
                    continue
                if "error" in msg:
                    raise RuntimeError(f"mcp error: {msg['error']}")
                return msg.get("result")

    # High-level API used by the Mnemosyne skill adapter

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._call("tools/list") or {}
        return list(result.get("tools") or [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return self._call("tools/call", {"name": name, "arguments": arguments})


def attach_mcp_as_skills(
    registry: Any,
    client: MCPClient,
    *,
    prefix: str = "mcp_",
) -> int:
    """Register every tool exposed by `client` as a Mnemosyne Skill.

    Returns the number of skills registered. Tool names are prefixed
    to avoid collisions with existing skills.
    """
    from mnemosyne_skills import Skill

    tools = client.list_tools()
    n = 0
    for t in tools:
        tool_name = t.get("name")
        if not tool_name:
            continue
        skill_name = f"{prefix}{tool_name}"
        desc = t.get("description") or f"MCP tool: {tool_name}"
        # Parameters in MCP are a JSON-schema-flavored dict; translate to
        # our Skill.parameters list.
        params_schema = t.get("inputSchema") or {}
        params_list: list[dict[str, Any]] = []
        for pname, pspec in (params_schema.get("properties") or {}).items():
            params_list.append({
                "name": pname,
                "type": pspec.get("type", "string"),
                "description": pspec.get("description", ""),
                "required": pname in (params_schema.get("required") or []),
            })

        def make_callable(t_name: str, c: MCPClient):
            def invoke(**kwargs: Any) -> Any:
                return c.call_tool(t_name, kwargs)
            return invoke

        registry.register(Skill(
            name=skill_name,
            description=desc,
            parameters=params_list,
            invocation="python",
            callable=make_callable(tool_name, client),
        ))
        n += 1
    return n


# ---- MCP server: expose Mnemosyne skills ------------------------------------

def serve_stdio(
    registry: Any | None = None,
    *,
    name: str = "mnemosyne",
    version: str = "0.2.0",
) -> int:
    """Run an MCP server reading JSON-RPC from stdin, writing to stdout.

    Blocks until stdin closes. Exposes every runnable (python/subprocess)
    skill in the given `registry`. Returns 0 on clean shutdown.
    """
    if registry is None:
        from mnemosyne_skills import default_registry
        registry = default_registry()

    def tool_spec(skill: Any) -> dict[str, Any]:
        # MCP uses inputSchema (JSON Schema) for parameter shapes.
        properties: dict[str, Any] = {}
        required: list[str] = []
        for p in skill.parameters:
            properties[p["name"]] = {
                "type": p.get("type", "string"),
                "description": p.get("description", ""),
            }
            if p.get("required"):
                required.append(p["name"])
        schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return {
            "name": skill.name,
            "description": skill.description,
            "inputSchema": schema,
        }

    def handle(msg: dict[str, Any]) -> dict[str, Any] | None:
        method = msg.get("method")
        rid = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            return _jsonrpc_result(rid, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": name, "version": version},
            })
        if method == "initialized" or method == "notifications/initialized":
            return None  # notification, no response
        if method == "tools/list":
            skills = [
                tool_spec(s) for s in registry.all()
                if s.invocation in ("python", "subprocess")
            ]
            return _jsonrpc_result(rid, {"tools": skills})
        if method == "tools/call":
            tool_name = params.get("name")
            args = params.get("arguments") or {}
            skill = registry.get(tool_name)
            if skill is None:
                return _jsonrpc_error(rid, -32601, f"unknown tool: {tool_name}")
            try:
                out = skill.invoke(**(args if isinstance(args, dict) else {}))
            except Exception as e:
                return _jsonrpc_error(rid, -32000,
                                        f"{type(e).__name__}: {e}")
            # MCP convention: content is a list of content-items
            content = [{"type": "text",
                         "text": json.dumps(out, default=str)
                                 if not isinstance(out, str) else out}]
            return _jsonrpc_result(rid, {"content": content})

        return _jsonrpc_error(rid, -32601, f"method not found: {method}")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0


# ---- CLI --------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="mnemosyne-mcp",
        description="MCP (Model Context Protocol) bridge. Serve Mnemosyne "
                    "skills to external MCP clients, or attach external "
                    "MCP servers into the Mnemosyne skill registry.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve",
                            help="run as an MCP server on stdin/stdout")
    serve.add_argument("--name", default="mnemosyne")
    serve.add_argument("--version", default="0.2.0")

    attach = sub.add_parser("attach",
                             help="attach an external MCP server and list its tools")
    attach.add_argument("cmd", nargs="+",
                        help="command to spawn the MCP server (e.g. "
                             "`npx @modelcontextprotocol/server-filesystem /tmp`)")

    args = p.parse_args(argv)

    if args.cmd == "serve":
        return serve_stdio(name=args.name, version=args.version)
    if args.cmd == "attach":
        with MCPClient(command=args.cmd) as client:
            tools = client.list_tools()
            print(f"connected: {len(tools)} tools")
            for t in tools:
                print(f"  - {t.get('name')}: {t.get('description', '')[:80]}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(_main())
