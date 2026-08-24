"""stdio MCP server (+ optional localhost GitLab CORS bridge) for Copilot."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from aird.plugins.gitlab.board_stats import busyness_for_issues
from aird.plugins.gitlab.client import (
    GitlabError,
    list_mrs,
    list_open_issues,
    list_pipelines,
)
from aird.plugins.gitlab.paths import extract_file_paths
from aird.plugins.gitlab.token import load_owner_token

TOOLS = [
    {
        "name": "list_file_comments",
        "description": "List Aird file comments for a workspace path",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "add_file_comment",
        "description": "Add an Aird file comment",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["path", "body"],
        },
    },
    {
        "name": "list_binding",
        "description": "GitLab folder binding for a path",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "pipeline_status",
        "description": "Latest GitLab pipelines for the bound code project",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "list_issues_for_file",
        "description": "Open GitLab issues that mention this file path",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_mrs",
        "description": "Open merge requests on the bound code project",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "board_snapshot",
        "description": "Issues on the bound GitLab board",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "assignee_load",
        "description": "10-day assignee busyness from open issues",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
]


def _aird_url() -> str:
    return (os.environ.get("AIRD_URL") or "http://127.0.0.1:8000").rstrip("/")


def _aird_token() -> str:
    return (os.environ.get("AIRD_TOKEN") or "").strip()


def _aird(method: str, path: str, body: dict | None = None) -> Any:
    url = _aird_url() + path
    data = None
    headers = {"Accept": "application/json"}
    token = _aird_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"Aird {exc.code}: {detail}") from exc


def _q(path: str) -> str:
    return urllib.parse.quote(path or "", safe="/")


def _call_tool(name: str, args: dict) -> str:
    path = str(args.get("path") or "")
    if name == "list_file_comments":
        return json.dumps(_aird("GET", f"/api/gitlab/comments?path={_q(path)}"))
    if name == "add_file_comment":
        return json.dumps(
            _aird(
                "POST",
                f"/api/gitlab/comments?path={_q(path)}",
                {"body": args.get("body")},
            )
        )
    if name == "list_binding":
        return json.dumps(_aird("GET", f"/api/gitlab/bindings?path={_q(path)}"))
    status = _aird("GET", f"/api/gitlab/status?path={_q(path)}")
    binding = (status or {}).get("binding") or {}
    host = binding.get("gitlab_host") or "https://gitlab.com"
    username = status.get("owner_username") or os.environ.get("USER") or ""
    token = load_owner_token(username, host)
    if not token:
        return json.dumps({"error": "GitLab token not configured on this machine"})
    if name == "pipeline_status":
        return json.dumps(
            list_pipelines(host, token, binding.get("code_project") or "")
        )
    if name == "list_mrs":
        return json.dumps(list_mrs(host, token, binding.get("code_project") or ""))
    if name in {"board_snapshot", "assignee_load", "list_issues_for_file"}:
        issues = list_open_issues(
            host, token, binding.get("issues_project") or binding.get("code_project") or ""
        )
        if name == "assignee_load":
            return json.dumps(busyness_for_issues(issues))
        if name == "list_issues_for_file":
            filename = args.get("name") or path.rsplit("/", 1)[-1]
            hits = []
            for issue in issues:
                blob = f"{issue.get('title') or ''}\n{issue.get('description') or ''}"
                if filename and filename in extract_file_paths(blob) or filename in blob:
                    hits.append(issue)
            return json.dumps(hits)
        return json.dumps(issues)
    raise RuntimeError(f"Unknown tool {name}")


def _handle_rpc(msg: dict) -> dict | None:
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "aird-gitlab", "version": "0.1.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            text = _call_tool(name, args)
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {"content": [{"type": "text", "text": text}]},
            }
        except (RuntimeError, GitlabError, OSError) as exc:
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": str(exc)}],
                },
            }
    if mid is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": mid,
        "error": {"code": -32601, "message": f"Unknown method {method}"},
    }


def run_stdio() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        reply = _handle_rpc(msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


class _BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("bridge: " + (fmt % args) + "\n")

    def _cors(self) -> None:
        origin = self.headers.get("Origin") or "*"
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Headers", "PRIVATE-TOKEN, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Vary", "Origin")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._proxy()

    def do_POST(self) -> None:  # noqa: N802
        self._proxy()

    def _proxy(self) -> None:
        token = self.headers.get("PRIVATE-TOKEN") or load_owner_token(
            os.environ.get("USER") or "", "https://gitlab.com"
        )
        host = (self.headers.get("X-Gitlab-Host") or "https://gitlab.com").rstrip("/")
        target = host + self.path
        length = int(self.headers.get("Content-Length") or 0)
        payload = self.rfile.read(length) if length else None
        req = urllib.request.Request(
            target,
            data=payload,
            method=self.command,
            headers={"PRIVATE-TOKEN": token or "", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                self.send_response(resp.status)
                self._cors()
                self.send_header("Content-Type", resp.headers.get("Content-Type") or "application/json")
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as exc:
            self.send_response(exc.code)
            self._cors()
            self.end_headers()
            self.wfile.write(exc.read())
        except OSError as exc:
            self.send_response(502)
            self._cors()
            self.end_headers()
            self.wfile.write(str(exc).encode("utf-8"))


def run_bridge(port: int) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), _BridgeHandler)
    sys.stderr.write(f"aird gitlab bridge on http://127.0.0.1:{port}\n")
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aird-gitlab-mcp")
    parser.add_argument("--bridge", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if args.bridge:
        run_bridge(args.port)
        return 0
    run_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
