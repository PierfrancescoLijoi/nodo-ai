"""Build a self-contained NODO folder that runs without Python, pip or a compiler installed.

    services/api/.venv/Scripts/python tools/package/build_bundle.py dist/NODO

The folder holds the interpreter and standard library of the Python that runs this script, every
installed package the engine needs (the closure of `nodo-api[local-models,document]` read from
package metadata, so no network access and no PyTorch), the engine's own source with the built web
interface, and `NODO.cmd`.  Models are NOT copied: first run offers them through the Models screen
with a checksum and an explicit confirmation.

A `python*._pth` file isolates the bundled interpreter from any Python on the PATH.
"""

from __future__ import annotations

import re
import shutil
import sys
import sysconfig
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]
EXTRAS = ("local-models", "document")
SKIP_PARTS = {"__pycache__", "tests", "test"}
LAUNCHER = """@echo off
rem Starts NODO AI from this folder. Nothing else needs to be installed.
setlocal
set HF_HUB_OFFLINE=1
"%~dp0runtime\\python.exe" -m nodo_api.desktop %*
"""


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def closure(root: str, extras: tuple[str, ...]) -> list[str]:
    """Names of `root` and everything it needs on this platform, with the requested extras."""
    wanted: dict[str, set[str]] = {norm(root): set(extras)}
    queue = [norm(root)]
    done: set[tuple[str, frozenset[str]]] = set()
    while queue:
        name = queue.pop()
        key = (name, frozenset(wanted[name]))
        if key in done:
            continue
        done.add(key)
        try:
            dist = distribution(name)
        except PackageNotFoundError:
            continue
        for text in dist.requires or []:
            req = Requirement(text)
            if req.marker is not None and not any(
                req.marker.evaluate({"extra": extra}) for extra in (wanted[name] or {""})
            ):
                continue
            child = norm(req.name)
            fresh = child not in wanted
            wanted.setdefault(child, set()).update(norm(e) for e in req.extras)
            if fresh or (name, frozenset(wanted[child])) not in done:
                queue.append(child)
    return sorted(wanted)


def copy_distribution(name: str, target: Path) -> int:
    try:
        dist = distribution(name)
    except PackageNotFoundError:
        return 0
    copied = 0
    for entry in dist.files or []:
        parts = set(Path(str(entry)).parts)
        source = Path(dist.locate_file(entry))
        if parts & SKIP_PARTS or ".." in parts or not source.is_file():
            continue
        destination = target / Path(str(entry))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied += source.stat().st_size
    return copied


def copy_runtime(destination: Path) -> None:
    base = Path(sys.base_prefix)
    destination.mkdir(parents=True)
    stem = f"python{sys.version_info.major}{sys.version_info.minor}"
    wanted = {"python.exe", "pythonw.exe", "python3.dll", f"{stem}.dll"}
    for name in base.iterdir():
        lower = name.name.lower()
        if name.is_file() and (lower in wanted or lower.startswith(("vcruntime", "msvcp", "ucrtbase"))):
            shutil.copy2(name, destination / name.name)
    shutil.copytree(base / "DLLs", destination / "DLLs")
    shutil.copytree(
        base / "Lib",
        destination / "Lib",
        ignore=shutil.ignore_patterns(
            "site-packages", "__pycache__", "test", "tests", "idlelib", "tkinter", "turtledemo", "ensurepip"
        ),
    )
    (destination / f"{stem}._pth").write_text(
        f"Lib\nDLLs\n..\\site-packages\n..\n{stem}.zip\nimport site\n", encoding="utf-8"
    )


def main() -> None:
    out = Path(sys.argv[1]).resolve()
    if out.exists():
        raise SystemExit(f"{out} exists; remove it first")
    packages = out / "site-packages"
    packages.mkdir(parents=True)
    copy_runtime(out / "runtime")
    total = 0
    for name in closure("nodo-api", EXTRAS):
        if name == "nodo-api":
            continue
        total += copy_distribution(name, packages)
    shutil.copytree(
        ROOT / "services" / "api" / "src" / "nodo_api",
        packages / "nodo_api",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (out / "NODO.cmd").write_text(LAUNCHER, encoding="ascii", newline="\r\n")
    print(f"bundle: {out}  packages: {total / 1e6:.0f} MB  platform: {sysconfig.get_platform()}")


if __name__ == "__main__":
    main()
