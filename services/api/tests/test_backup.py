"""Backup, restore and complete export of the local data folder (FR-28)."""

from __future__ import annotations

import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient

from nodo_api.adapters.local.backup import BackupError, backup, export_everything, restore
from nodo_api.main import create_app
from tests.test_local_app import HEADERS, _settings


def _populate(root):
    root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(root / "nodo.db") as conn:
        conn.execute("CREATE TABLE page_revisions (id TEXT, body TEXT)")
        conn.executemany(
            "INSERT INTO page_revisions VALUES (?, ?)", [("r1", "first"), ("r2", "second")]
        )
    conn.close()
    (root / "audio" / "w").mkdir(parents=True)
    (root / "audio" / "w" / "m.wav").write_bytes(b"RIFFdata")


def test_round_trip_keeps_every_revision_and_audio(tmp_path):
    source, target = tmp_path / "a", tmp_path / "b"
    _populate(source)
    archive = backup(source, tmp_path / "out" / "b.zip")
    _populate(target)  # existing data must be kept aside, not lost
    aside = restore(archive, target)
    with sqlite3.connect(target / "nodo.db") as conn:
        assert [r[0] for r in conn.execute("SELECT id FROM page_revisions ORDER BY id")] == [
            "r1",
            "r2",
        ]
    conn.close()
    assert (target / "audio" / "w" / "m.wav").read_bytes() == b"RIFFdata"
    assert aside is not None and (aside / "nodo.db").is_file()


def test_tampered_backup_is_refused_before_anything_changes(tmp_path):
    _populate(tmp_path / "a")
    archive = backup(tmp_path / "a", tmp_path / "b.zip")
    tampered = tmp_path / "t.zip"
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(tampered, "w") as out:
        for item in src.infolist():
            data = src.read(item.filename)
            out.writestr(item, data + b"x" if item.filename.startswith("audio/") else data)
    keep = tmp_path / "keep"
    _populate(keep)
    with pytest.raises(BackupError, match="Checksum"):
        restore(tampered, keep)
    assert (keep / "nodo.db").is_file() and not list(tmp_path.glob("keep.before-restore-*"))


def test_traversal_path_is_refused(tmp_path):
    bad = tmp_path / "bad.zip"
    import hashlib
    import json

    with zipfile.ZipFile(bad, "w") as out:
        out.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": "nodo-backup-1",
                    "files": {
                        "nodo.db": hashlib.sha256(b"").hexdigest(),
                        "../evil": hashlib.sha256(b"").hexdigest(),
                    },
                }
            ),
        )
        out.writestr("nodo.db", b"")
        out.writestr("../evil", b"")
    with pytest.raises(BackupError, match="outside"):
        restore(bad, tmp_path / "t")


def test_complete_export_lists_every_table(tmp_path):
    _populate(tmp_path)
    data = export_everything(tmp_path / "nodo.db")
    assert data["tables"]["page_revisions"] == [
        {"id": "r1", "body": "first"},
        {"id": "r2", "body": "second"},
    ]


def test_endpoints_require_the_secret_and_write_a_backup(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        assert client.post("/v1/local/backup").status_code == 401
        made = client.post("/v1/local/backup", headers=HEADERS)
        assert made.status_code == 201
        assert zipfile.is_zipfile(made.json()["path"])
        everything = client.get("/v1/local/export/complete", headers=HEADERS).json()
        assert (
            "sources" in everything["tables"] and "page_revisions" in everything["tables"]
        ) or everything["tables"]
