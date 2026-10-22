"""Blob Storage adapter. Upload and read links are user-delegation SAS tokens scoped to one
blob, so neither the app nor the speech service ever sees an account key (none is enabled)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobSasPermissions, generate_blob_sas

AUDIO_CONTAINER = "audio"
READ_LINK_SECONDS = 3600
DELEGATION_KEY_SECONDS = 3600


class AzureBlobStore:
    def __init__(
        self,
        account_name: str,
        service_client: Any,
        upload_seconds: int = 900,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._account = account_name
        self._service = service_client
        self._upload_seconds = upload_seconds
        self._clock = clock
        self._key: Any = None
        self._key_expiry = datetime.min.replace(tzinfo=UTC)

    @staticmethod
    def blob_name(workspace_id: str, meeting_id: str) -> str:
        return f"{workspace_id}/{meeting_id}/audio"

    def _delegation_key(self, needed_until: datetime) -> Any:
        now = self._clock()
        if self._key is None or self._key_expiry < needed_until:
            self._key_expiry = now + timedelta(seconds=DELEGATION_KEY_SECONDS)
            self._key = self._service.get_user_delegation_key(
                now - timedelta(minutes=1), self._key_expiry
            )
        return self._key

    def _sas_url(self, blob_name: str, permission: BlobSasPermissions, seconds: int) -> str:
        now = self._clock()
        expiry = now + timedelta(seconds=seconds)
        token = generate_blob_sas(
            account_name=self._account,
            container_name=AUDIO_CONTAINER,
            blob_name=blob_name,
            user_delegation_key=self._delegation_key(expiry),
            permission=permission,
            start=now - timedelta(minutes=1),
            expiry=expiry,
        )
        return f"https://{self._account}.blob.core.windows.net/{AUDIO_CONTAINER}/{quote(blob_name)}?{token}"

    def create_upload_session(self, workspace_id: str, meeting_id: str) -> dict[str, Any]:
        """One blob, write-only. The app stages blocks (Put Block) and commits the list, which is
        how an interrupted 60 minute upload resumes from the first missing block."""
        url = self._sas_url(
            self.blob_name(workspace_id, meeting_id),
            BlobSasPermissions(create=True, write=True),
            self._upload_seconds,
        )
        return {"id": str(uuid4()), "upload_url": url, "expires_in_seconds": self._upload_seconds}

    def create_read_url(self, workspace_id: str, meeting_id: str) -> str:
        return self._sas_url(
            self.blob_name(workspace_id, meeting_id),
            BlobSasPermissions(read=True),
            READ_LINK_SECONDS,
        )

    def erase(self, workspace_id: str, meeting_id: str) -> None:
        container = self._service.get_container_client(AUDIO_CONTAINER)
        try:
            for blob in container.list_blobs(name_starts_with=f"{workspace_id}/{meeting_id}/"):
                container.delete_blob(blob.name)
        except ResourceNotFoundError:
            return  # nothing was ever uploaded: erasure is already complete
