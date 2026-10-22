"""Audio on the local disk. Same contract as the Azure blob store: the client gets a short-lived,
single-purpose upload link; the speech step reads the finished file; erasure removes everything.

The link points at the engine itself (PUT /v1/local/uploads/<token>). The token is the capability:
random, tied to one meeting, valid once, expiring."""

from __future__ import annotations

import os
import secrets
import shutil
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from nodo_api.adapters.local.db import LocalDatabase, now_text, to_text
from nodo_api.errors import NotFoundError

AUDIO_NAME = "audio"
MAX_UPLOAD_BYTES = 2 * 1024**3  # a four hour recording in a compressed format fits easily


class UploadRejected(Exception):
    """The token is unknown, expired, used, or the body is too large."""


class Upload:
    """The open file of one upload, with the size limit enforced as bytes arrive."""

    def __init__(self, workspace_id: str, meeting_id: str, partial: Path) -> None:
        self.workspace_id = workspace_id
        self.meeting_id = meeting_id
        self._handle = partial.open("wb")
        self._written = 0

    def write(self, chunk: bytes) -> None:
        self._written += len(chunk)
        if self._written > MAX_UPLOAD_BYTES:
            raise UploadRejected
        self._handle.write(chunk)

    def close(self) -> None:
        self._handle.close()


class LocalBlobStore:
    def __init__(
        self,
        root: Path | str,
        db: LocalDatabase,
        base_url: Callable[[], str],
        upload_seconds: int = 900,
    ) -> None:
        self._root = Path(root)
        self._db = db
        self._base_url = base_url
        self._upload_seconds = upload_seconds

    def _folder(self, workspace_id: str, meeting_id: str) -> Path:
        # ids come from the database, but a path built from text is never trusted
        for part in (workspace_id, meeting_id):
            if not part or part in {".", ".."} or any(c in part for c in "/\\:\0"):
                raise ValueError("Unsafe identifier for a file path")
        return self._root / workspace_id / meeting_id

    def create_upload_session(self, workspace_id: str, meeting_id: str) -> dict[str, Any]:
        token = secrets.token_urlsafe(32)
        expires = datetime.now(UTC) + timedelta(seconds=self._upload_seconds)
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO upload_sessions (token, workspace_id, meeting_id, expires_at)"
                " VALUES (?, ?, ?, ?)",
                (token, workspace_id, meeting_id, to_text(expires)),
            )
        return {
            "id": str(uuid4()),
            "upload_url": f"{self._base_url().rstrip('/')}/v1/local/uploads/{token}",
            "expires_in_seconds": self._upload_seconds,
        }

    @contextmanager
    def open_upload(self, token: str) -> Iterator[Upload]:
        """Claim the link and give a writer for the body; the file appears only on success, and a
        failed attempt frees the link for another try."""
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT workspace_id, meeting_id, expires_at, used_at FROM upload_sessions"
                " WHERE token = ?",
                (token,),
            ).fetchone()
            if row is None or row["used_at"] or row["expires_at"] <= now_text():
                raise UploadRejected
            conn.execute(
                "UPDATE upload_sessions SET used_at = ? WHERE token = ?", (now_text(), token)
            )
        folder = self._folder(row["workspace_id"], row["meeting_id"])
        folder.mkdir(parents=True, exist_ok=True)
        partial = folder / f"{AUDIO_NAME}.part"
        upload = Upload(row["workspace_id"], row["meeting_id"], partial)
        try:
            yield upload
            upload.close()
            os.replace(partial, folder / AUDIO_NAME)
        except BaseException:
            upload.close()
            partial.unlink(missing_ok=True)
            with self._db.transaction() as conn:
                conn.execute("UPDATE upload_sessions SET used_at = NULL WHERE token = ?", (token,))
            raise

    def accept_upload(self, token: str, chunks: Iterable[bytes]) -> tuple[str, str]:
        """Store a body that is already available as chunks. Returns (workspace, meeting)."""
        with self.open_upload(token) as upload:
            for chunk in chunks:
                upload.write(chunk)
        return upload.workspace_id, upload.meeting_id

    def audio_path(self, workspace_id: str, meeting_id: str) -> Path:
        path = self._folder(workspace_id, meeting_id) / AUDIO_NAME
        if not path.is_file():
            raise NotFoundError
        return path

    def create_read_url(self, workspace_id: str, meeting_id: str) -> str:
        """For the local speech adapter this is a file path, not a URL."""
        return str(self.audio_path(workspace_id, meeting_id))

    def erase(self, workspace_id: str, meeting_id: str) -> None:
        shutil.rmtree(self._folder(workspace_id, meeting_id), ignore_errors=True)
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM upload_sessions WHERE workspace_id = ? AND meeting_id = ?",
                (workspace_id, meeting_id),
            )

    def was_erased(self, workspace_id: str, meeting_id: str) -> bool:
        return not self._folder(workspace_id, meeting_id).exists()
