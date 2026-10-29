from __future__ import annotations

import json
import socket

import httpx
import pytest

from nodo_api import desktop


def test_the_attestation_key_is_made_once_and_reused(tmp_path) -> None:
    first = desktop.load_or_create_key(tmp_path)
    assert len(first) >= 32 and desktop.load_or_create_key(tmp_path) == first


def test_a_taken_preferred_port_falls_back_to_a_free_one() -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        port = taken.getsockname()[1]
        chosen = desktop.choose_port(port)
    assert chosen != port and 1024 < chosen < 65536


def test_the_window_has_its_own_profile_and_no_browser_chrome(tmp_path) -> None:
    command = desktop.window_command("edge.exe", "http://127.0.0.1:1/#token=t", tmp_path / "w")
    assert command[0] == "edge.exe" and command[1].startswith("--app=http://127.0.0.1:1/")
    assert any(part.startswith("--user-data-dir=") for part in command)


def test_the_web_folder_is_found_in_order(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("NODO_WEB_DIR", raising=False)
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    assert desktop.find_web_dir(str(explicit)) == explicit
    assert desktop.find_web_dir(str(tmp_path / "missing")) in (
        None,
        desktop.Path(desktop.__file__).parent / "web",
    )
    other = tmp_path / "env"
    other.mkdir()
    monkeypatch.setenv("NODO_WEB_DIR", str(other))
    assert desktop.find_web_dir(None) == other


def test_the_engine_starts_serves_with_its_secret_and_stops(tmp_path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<title>NODO</title>", encoding="utf-8")
    engine = desktop.Engine(tmp_path / "data", web, desktop.choose_port(0), warm_up=False)
    (tmp_path / "data").mkdir()
    engine.start()
    try:
        assert httpx.get(f"{engine.url}/healthz").json() == {"status": "ok"}
        assert "NODO" in httpx.get(f"{engine.url}/").text
        assert httpx.get(f"{engine.url}/v1/meetings").status_code == 401
        headers = {"Authorization": f"Bearer {engine.token}"}
        assert httpx.get(f"{engine.url}/v1/meetings", headers=headers).json()["items"] == []
        info = desktop.running_engine(tmp_path / "data")
        assert info is not None and info["port"] == engine.port
        lock = json.loads((tmp_path / "data" / desktop.LOCK_FILE).read_text())
        assert lock["token"] == engine.token
    finally:
        engine.stop()
    assert desktop.running_engine(tmp_path / "data") is None


@pytest.mark.parametrize("content", ["not json", "{}", '{"port": 1}'])
def test_a_stale_or_broken_lock_file_means_no_engine(tmp_path, content) -> None:
    (tmp_path / desktop.LOCK_FILE).write_text(content, encoding="utf-8")
    assert desktop.running_engine(tmp_path) is None
