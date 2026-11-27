"""Verified, transactional installation of direct local model artifacts."""

from __future__ import annotations

import shutil
import tarfile
import tempfile
import zipfile
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import BinaryIO

from nodo_api.adapters.local.model_catalog import (
    ModelCatalogEntry,
    safe_archive_members,
    verify_sha256,
)

CHUNK_SIZE = 1 << 20


def install(entry: ModelCatalogEntry, root: Path, opener: Callable[[str], BinaryIO]) -> Path:
    """Install a pinned artifact without ever replacing an existing model.

    The complete artifact is staged below the model root, checksum-verified, inspected, and only
    then renamed into its catalog-owned destination.  A failed download or extraction leaves no
    partially-installed target behind.
    """
    if not entry.installable or entry.url is None or entry.sha256 is None:
        raise ValueError("model has no verified direct download")
    paths = entry.owned_paths(root)
    if len(paths) != 1:
        raise ValueError("direct download must own exactly one target")
    target = paths[0]
    if target.exists():
        raise FileExistsError(f"model target already exists: {target}")

    root.mkdir(parents=True, exist_ok=True)
    staging_root = root / ".staging"
    staging_root.mkdir(exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f"{entry.id}-", dir=staging_root))
    artifact = staging / "download"
    try:
        _download(entry, artifact, opener)
        if not verify_sha256(artifact, entry.sha256):
            raise ValueError("downloaded model does not match its SHA-256")
        payload = staging / "payload"
        _unpack(artifact, payload, entry.archive)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload.replace(target)
        return target
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _download(entry: ModelCatalogEntry, artifact: Path, opener: Callable[[str], BinaryIO]) -> None:
    assert entry.url is not None
    # A signed checksum detects tampering; a size cap also prevents an endless response consuming
    # the owner's disk before that integrity check can happen.
    maximum = entry.size_mb * 1024 * 1024 + 8 * 1024 * 1024
    written = 0
    with closing(opener(entry.url)) as source, artifact.open("wb") as destination:
        while chunk := source.read(CHUNK_SIZE):
            written += len(chunk)
            if written > maximum:
                raise ValueError("model download exceeds the catalogued size limit")
            destination.write(chunk)


def _unpack(artifact: Path, destination: Path, archive: str | None) -> None:
    if archive is None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        artifact.replace(destination)
        return
    destination.mkdir()
    if archive == "zip":
        with zipfile.ZipFile(artifact) as bundle:
            members = bundle.infolist()
            safe_archive_members(tuple(member.filename for member in members))
            if any(_zip_is_symlink(member) for member in members):
                raise ValueError("model archive contains a symlink")
            bundle.extractall(destination)
        return
    if archive == "tar.bz2":
        with tarfile.open(artifact, "r:bz2") as bundle:
            members = bundle.getmembers()
            safe_archive_members(tuple(member.name for member in members))
            if any(member.issym() or member.islnk() for member in members):
                raise ValueError("model archive contains a link")
            bundle.extractall(destination, filter="data")
        return
    raise ValueError(f"unsupported model archive type: {archive}")


def _zip_is_symlink(member: zipfile.ZipInfo) -> bool:
    return (member.external_attr >> 16) & 0o170000 == 0o120000
