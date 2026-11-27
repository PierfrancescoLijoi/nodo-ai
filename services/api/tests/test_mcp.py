"""The read-only MCP endpoint of the local engine (FR-27)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from nodo_api.api.mcp import TOOLS
from nodo_api.main import create_app
from tests.test_local_app import HEADERS, TOKEN, _settings


def rpc(client, method, params=None, *, headers=HEADERS, id=1):
    body = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers)


def test_requires_the_launch_secret(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        assert rpc(client, "tools/list", headers={}).status_code == 401
        assert (
            rpc(client, "tools/list", headers={"Authorization": "Bearer nope"}).status_code == 401
        )


def test_a_new_launch_rejects_the_old_secret(tmp_path):
    with TestClient(create_app(_settings(tmp_path, local_token=TOKEN + "-next"))) as client:
        assert rpc(client, "tools/list").status_code == 401


def test_non_loopback_host_is_refused(tmp_path):
    with TestClient(create_app(_settings(tmp_path)), base_url="http://evil.example") as client:
        assert rpc(client, "tools/list").status_code == 421


def test_exposes_only_read_tools(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        tools = rpc(client, "tools/list").json()["result"]["tools"]
    assert {t["name"] for t in tools} == {t["name"] for t in TOOLS} | {"ask"}
    assert all(t["method"] == "GET" for t in TOOLS)


def test_initialize_ping_and_notifications(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        assert rpc(client, "initialize", {}).json()["result"]["serverInfo"]["name"] == "nodo"
        assert rpc(client, "ping").json()["result"] == {}
        note = client.post(
            "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=HEADERS
        )
        assert note.status_code == 202
        assert rpc(client, "nope").json()["error"]["code"] == -32601


def test_tool_call_returns_authorized_data_and_rejects_bad_input(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        listed = rpc(client, "tools/call", {"name": "list_sources", "arguments": {}}).json()[
            "result"
        ]
        assert listed["isError"] is False and "items" in listed["content"][0]["text"]
        assert rpc(client, "tools/call", {"name": "delete_source", "arguments": {}}).json()["error"]
        bad = rpc(client, "tools/call", {"name": "get_source", "arguments": {"source_id": "../x"}})
        assert bad.json()["error"]["code"] == -32602
        missing = rpc(
            client, "tools/call", {"name": "get_source", "arguments": {"source_id": "nope"}}
        )
        assert missing.json()["result"]["isError"] is True
