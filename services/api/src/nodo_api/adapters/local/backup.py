"""Backup, restore and complete export of the local data folder (FR-28, ADR-017).

A backup is one zip holding a consistent copy of `nodo.db` (SQLite's online backup API, so the
engine may keep running) and the `audio/` blobs, plus a manifest with a SHA-256 for each file.
Models are not included: the catalogue reinstalls them with a verified checksum.  Restoring checks
every hash and every path before it touches the target, and puts the old data aside instead of
deleting it.  Pages, their revisions, review state and settings all live in `nodo.db`, so they come
back together with Sources and Concepts.

The complete export dumps every table of that database as JSON, so nothing that exists in the
workspace can be missing from it by omission."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

FORMAT = "nodo-backup-1"
DB_NAME = "nodo.db"
AUDIO_DIR = "audio"
MANIFEST = "manifest.json"


class BackupError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def backup(root: Path, destination: Path) -> Path:
    """Write a backup of `root` to `destination` (a .zip path that must not exist yet)."""
    if destination.exists():
        raise BackupError("The backup file already exists")
    source_db = root / DB_NAME
    if not source_db.is_file():
        raise BackupError("There is no database to back up")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / DB_NAME
        with (
            contextlib.closing(sqlite3.connect(source_db)) as live,
            contextlib.closing(sqlite3.connect(copy)) as target,
        ):
            live.backup(target)
        entries: dict[str, Path] = {DB_NAME: copy}
        audio = root / AUDIO_DIR
        if audio.is_dir():
            for path in sorted(audio.rglob("*")):
                if path.is_file():
                    entries[f"{AUDIO_DIR}/{path.relative_to(audio).as_posix()}"] = path
        manifest = {
            "format": FORMAT,
            "created_at": datetime.now(UTC).isoformat(),
            "files": {name: _sha256(path) for name, path in entries.items()},
        }
        partial = destination.with_suffix(".partial")
        try:
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(MANIFEST, json.dumps(manifest, indent=2))
                for name, path in entries.items():
                    archive.write(path, name)
            partial.replace(destination)
        finally:
            partial.unlink(missing_ok=True)
    return destination


def _verified_members(archive: zipfile.ZipFile) -> dict[str, str]:
    try:
        manifest = json.loads(archive.read(MANIFEST))
    except (KeyError, ValueError) as error:
        raise BackupError("This is not a NODO backup") from error
    if manifest.get("format") != FORMAT or not isinstance(manifest.get("files"), dict):
        raise BackupError("Unsupported backup format")
    files: dict[str, str] = manifest["files"]
    if DB_NAME not in files:
        raise BackupError("The backup has no database")
    for name in files:
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not (name == DB_NAME or path.parts[0] == AUDIO_DIR)
        ):
            raise BackupError("The backup contains a path outside the data folder")
    if set(archive.namelist()) - set(files) - {MANIFEST}:
        raise BackupError("The backup contains files its manifest does not list")
    for name, expected in files.items():
        digest = hashlib.sha256()
        try:
            with archive.open(name) as member:
                for block in iter(lambda: member.read(1 << 20), b""):
                    digest.update(block)
        except KeyError as error:
            raise BackupError(f"The backup is missing {name}") from error
        if digest.hexdigest() != expected:
            raise BackupError(f"Checksum mismatch for {name}")
    return files


def restore(archive_path: Path, root: Path) -> Path | None:
    """Restore into `root` while the engine is stopped.  Returns where the previous data went."""
    with zipfile.ZipFile(archive_path) as archive:
        files = _verified_members(archive)
        staging = Path(tempfile.mkdtemp(prefix="restore-", dir=root.parent))
        try:
            for name in files:
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as member, target.open("wb") as out:
                    shutil.copyfileobj(member, out)
            with contextlib.closing(sqlite3.connect(staging / DB_NAME)) as check:
                if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise BackupError("The restored database failed its integrity check")
            aside: Path | None = None
            root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            existing = [
                p
                for p in (
                    root / DB_NAME,
                    root / f"{DB_NAME}-wal",
                    root / f"{DB_NAME}-shm",
                    root / AUDIO_DIR,
                )
                if p.exists()
            ]
            if existing:
                aside = root.parent / f"{root.name}.before-restore-{stamp}"
                aside.mkdir()
                for path in existing:
                    shutil.move(str(path), aside / path.name)
            shutil.move(str(staging / DB_NAME), root / DB_NAME)
            if (staging / AUDIO_DIR).exists():
                shutil.move(str(staging / AUDIO_DIR), root / AUDIO_DIR)
            return aside
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode()}
    return value


def export_everything(db_path: Path) -> dict[str, Any]:
    """Every row of every table, from a read-only connection."""
    with contextlib.closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    ) as conn:
        conn.row_factory = sqlite3.Row
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        data = {
            table: [
                {key: _jsonable(row[key]) for key in row.keys()}  # noqa: SIM118  (sqlite3.Row iterates values)
                for row in conn.execute(f'SELECT * FROM "{table}"')
            ]
            for table in tables
        }
    return {
        "format": "nodo-export-complete-1",
        "exported_at": datetime.now(UTC).isoformat(),
        "tables": data,
    }


def main(argv: list[str] | None = None) -> None:
    import argparse

    from nodo_api.adapters.local.models import data_dir

    parser = argparse.ArgumentParser(prog="python -m nodo_api.adapters.local.backup")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("backup", "restore"):
        sub.add_parser(name).add_argument("file", type=Path)
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    root = args.data_dir or data_dir()
    if args.command == "backup":
        print(backup(root, args.file))
    else:
        aside = restore(args.file, root)
        print(f"Restored. Previous data kept at: {aside}" if aside else "Restored.")


if __name__ == "__main__":
    main()
