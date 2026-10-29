"""The desktop app: starts the local engine and opens a window on it (ADR-014).

    python -m nodo_api.desktop [--no-window] [--port N] [--web DIR] [--data DIR]

The engine listens on 127.0.0.1 only and accepts one secret that is new at every start. The window
is a Chromium "app window" (Microsoft Edge, which Windows 11 ships, or Chrome) with its own profile
folder, so it looks like a program, has no tabs or address bar, and keeps its microphone permission
between runs. The secret reaches the web app in the address fragment, which is never sent to a
server. Closing the window stops the engine. A second launch opens a window on the engine that is
already running instead of starting a second one (two writers on one GPU and one database file).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import uvicorn

from nodo_api.adapters.local.models import data_dir
from nodo_api.config import Settings
from nodo_api.main import create_app

PREFERRED_PORT = 8765
KEY_FILE = "attestation.key"
LOCK_FILE = "engine.json"
START_TIMEOUT_SECONDS = 60
BROWSER_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


def load_or_create_key(folder: Path) -> str:
    """The key that signs recording attestations here; made once and kept in the data folder."""
    path = folder / KEY_FILE
    if path.exists():
        return path.read_text(encoding="ascii").strip()
    folder.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(48)
    path.write_text(key, encoding="ascii")
    return key


def choose_port(preferred: int = PREFERRED_PORT) -> int:
    for candidate in (preferred, 0):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return int(sock.getsockname()[1])
    raise OSError("No free port on 127.0.0.1")


def find_web_dir(explicit: str | None) -> Path | None:
    for candidate in (explicit, os.environ.get("NODO_WEB_DIR"), Path(__file__).parent / "web"):
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return None


def find_browser() -> str | None:
    for name in ("msedge", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return next((p for p in BROWSER_PATHS if Path(p).exists()), None)


def window_command(browser: str, url: str, profile: Path) -> list[str]:
    return [
        browser,
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1280,860",
    ]


@dataclass
class Engine:
    """The API server on a background thread."""

    folder: Path
    web_dir: Path | None
    port: int
    warm_up: bool = True
    token: str = ""
    _server: uvicorn.Server | None = None
    _thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self.token = secrets.token_urlsafe(32)
        settings = Settings(
            environment="prod",
            backend="local",
            auth_mode="local",
            local_token=self.token,
            local_data_dir=self.folder,
            local_public_url=self.url,
            local_web_dir=self.web_dir,
            local_warm_up=self.warm_up,
            attestation_hmac_key=load_or_create_key(self.folder),
        )
        config = uvicorn.Config(
            create_app(settings), host="127.0.0.1", port=self.port, log_level="warning"
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="nodo-engine", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("The engine did not start")
            time.sleep(0.05)
        (self.folder / LOCK_FILE).write_text(
            json.dumps({"pid": os.getpid(), "port": self.port, "token": self.token}),
            encoding="utf-8",
        )

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=30)
        (self.folder / LOCK_FILE).unlink(missing_ok=True)


def running_engine(folder: Path) -> dict | None:
    """The engine that is already running for this data folder, if one answers."""
    try:
        info = json.loads((folder / LOCK_FILE).read_text(encoding="utf-8"))
        reply = httpx.get(f"http://127.0.0.1:{info['port']}/healthz", timeout=2)
        return info if reply.status_code == 200 else None
    except (OSError, ValueError, KeyError, httpx.HTTPError):
        return None


def open_window(url: str, token: str, folder: Path) -> subprocess.Popen | None:
    browser = find_browser()
    if browser is None:
        return None
    return subprocess.Popen(window_command(browser, f"{url}/#token={token}", folder / "window"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nodo-desktop")
    parser.add_argument("--no-window", action="store_true", help="run the engine only")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--web", default=None, help="folder with the exported web app")
    parser.add_argument("--data", default=None, help="data folder (default: the user profile)")
    args = parser.parse_args(argv)
    os.environ.setdefault("HF_HUB_OFFLINE", "1")  # models are used from disk, never fetched

    folder = Path(args.data) if args.data else data_dir()
    folder.mkdir(parents=True, exist_ok=True)

    existing = running_engine(folder)
    if existing is not None:
        if not args.no_window:
            open_window(f"http://127.0.0.1:{existing['port']}", existing["token"], folder)
        return 0

    engine = Engine(folder, find_web_dir(args.web), args.port or choose_port())
    engine.start()
    print(json.dumps({"url": engine.url, "token": engine.token}), flush=True)
    try:
        window = None if args.no_window else open_window(engine.url, engine.token, folder)
        if window is not None:
            window.wait()
        else:
            with contextlib.suppress(KeyboardInterrupt):
                threading.Event().wait()
    finally:
        engine.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
