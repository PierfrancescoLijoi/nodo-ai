"""Read-only MCP server for the local engine (FR-27, ADR-014).

One endpoint, `POST /mcp`, speaks MCP's streamable-HTTP JSON-RPC subset: initialize, ping,
tools/list and tools/call.  It sits behind the same per-launch bearer secret and the same loopback
Host check as the rest of the engine.  Every tool is a fixed GET of an existing /v1 route (plus
`ask`, which is a question and changes no knowledge), replayed in-process with the caller's own
Authorization header, so authorization is decided by exactly one code path.  No write route is
reachable: the table below is the whole surface."""

from __future__ import annotations

import re
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from nodo_api.auth import AuthError

router = APIRouter()

PROTOCOL = "2025-06-18"
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_ID_PROP = {"type": "string", "pattern": _ID.pattern}


def _tool(name: str, description: str, method: str, path: str, *ids: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "method": method,
        "path": path,
        "ids": ids,
        "schema": {
            "type": "object",
            "properties": {i: _ID_PROP for i in ids},
            "required": list(ids),
            "additionalProperties": False,
        },
    }


TOOLS: tuple[dict[str, Any], ...] = (
    _tool("list_sources", "List imported sources.", "GET", "/v1/sources"),
    _tool("get_source", "One source's record.", "GET", "/v1/sources/{source_id}", "source_id"),
    _tool(
        "source_outline",
        "A source's section tree.",
        "GET",
        "/v1/sources/{source_id}/outline",
        "source_id",
    ),
    _tool(
        "source_fragments",
        "A source's citable fragments.",
        "GET",
        "/v1/sources/{source_id}/fragments",
        "source_id",
    ),
    _tool("list_pages", "List wiki pages.", "GET", "/v1/pages"),
    _tool("get_page", "One wiki page with its claims.", "GET", "/v1/pages/{page_id}", "page_id"),
    _tool("list_meetings", "List meetings.", "GET", "/v1/meetings"),
    _tool(
        "meeting_transcript",
        "A meeting's transcript.",
        "GET",
        "/v1/meetings/{meeting_id}/transcript",
        "meeting_id",
    ),
    _tool("list_collections", "List collections.", "GET", "/v1/collections"),
)
_BY_NAME = {tool["name"]: tool for tool in TOOLS}

ASK = {
    "name": "ask",
    "description": "Ask a question; the answer cites its evidence or says it is unanswerable.",
    "schema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "minLength": 1, "maxLength": 2000},
            "ui_locale": {"type": "string", "enum": ["en", "pl", "it", "da"]},
        },
        "required": ["question"],
        "additionalProperties": False,
    },
}


def listing() -> list[dict[str, Any]]:
    return [
        {"name": t["name"], "description": t["description"], "inputSchema": t["schema"]}
        for t in (*TOOLS, ASK)
    ]


def _error(request_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    )


def _result(request_id: Any, result: Any) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


async def _call(request: Request, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "ask":
        question = arguments.get("question")
        if not isinstance(question, str) or not 0 < len(question) <= 2000:
            raise ValueError("question must be 1-2000 characters")
        locale = arguments.get("ui_locale", "en")
        method, path, body = "POST", "/v1/query", {"question": question, "ui_locale": locale}
    else:
        tool = _BY_NAME[name]
        values = {}
        for key in tool["ids"]:
            value = arguments.get(key)
            if not isinstance(value, str) or not _ID.match(value):
                raise ValueError(f"{key} is not a valid identifier")
            values[key] = value
        method, path, body = tool["method"], tool["path"].format(**values), None
    base = str(request.base_url).rstrip("/")
    transport = httpx.ASGITransport(app=request.app)
    async with httpx.AsyncClient(transport=transport, base_url=base, timeout=60) as inner:
        reply = await inner.request(
            method,
            path,
            json=body,
            headers={"Authorization": request.headers["Authorization"]},
        )
    return {
        "content": [{"type": "text", "text": reply.text}],
        "isError": reply.status_code >= 400,
    }


@router.post("/mcp")
async def mcp(request: Request):
    header = request.headers.get("Authorization", "")
    try:
        if not header.startswith("Bearer "):
            raise AuthError("missing bearer")
        request.app.state.container.auth.authenticate(header[7:])
    except AuthError:
        return JSONResponse({"error": {"code": "unauthenticated"}}, status_code=401)
    try:
        message = await request.json()
    except ValueError:
        return _error(None, -32700, "Parse error")
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid request")
    request_id, method = message.get("id"), message.get("method")
    if "id" not in message:  # a notification gets no reply
        return Response(status_code=202)
    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "nodo", "version": "1"},
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": listing()})
    if method == "tools/call":
        params = message.get("params") or {}
        name, arguments = params.get("name"), params.get("arguments") or {}
        if name != "ask" and name not in _BY_NAME:
            return _error(request_id, -32602, "Unknown tool")
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "arguments must be an object")
        try:
            return _result(request_id, await _call(request, name, arguments))
        except ValueError as exc:
            return _error(request_id, -32602, str(exc))
    return _error(request_id, -32601, "Method not found")
