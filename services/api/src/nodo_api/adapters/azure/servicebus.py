"""Service Bus publisher. A job is recorded in Postgres first (outbox), then sent; the message id is
the job identity so Service Bus duplicate detection also absorbs a retried send."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

from azure.servicebus import ServiceBusMessage

from nodo_api.adapters.azure.postgres import PgJobOutbox
from nodo_api.telemetry import inject_context


class ServiceBusJobQueue:
    def __init__(
        self,
        outbox: PgJobOutbox,
        sender_factory: Callable[[], AbstractContextManager[Any]],
    ) -> None:
        self._outbox = outbox
        self._sender_factory = sender_factory

    def publish_once(
        self, workspace_id: str, job_key: str, job_type: str, payload: dict[str, Any]
    ) -> str:
        job_id, published = self._outbox.record(workspace_id, job_key, job_type, payload)
        if not published:
            body = {
                "job_id": job_id,
                "workspace_id": workspace_id,
                "job_key": job_key,
                "job_type": job_type,
                "trace": inject_context(),  # W3C trace context: the worker's spans join this trace
                **payload,
            }
            message = ServiceBusMessage(
                json.dumps(body),
                content_type="application/json",
                message_id=f"{workspace_id}:{job_key}:{job_type}",
                subject=job_type,
                application_properties={"workspace_id": workspace_id},
            )
            with self._sender_factory() as sender:
                sender.send_messages(message)
            self._outbox.mark_published(workspace_id, job_key, job_type)
        return job_id

    def publish_later(
        self,
        workspace_id: str,
        job_key: str,
        job_type: str,
        payload: dict[str, Any],
        delay_seconds: int,
    ) -> None:
        """A message that becomes visible after a delay. No outbox row: the caller keeps the state
        that says what the message is for (the speech job store), and the message id is the key, so
        a repeated schedule of the same poll is absorbed by duplicate detection."""
        body = {
            "workspace_id": workspace_id,
            "job_key": job_key,
            "job_type": job_type,
            "trace": inject_context(),
            **payload,
        }
        message = ServiceBusMessage(
            json.dumps(body),
            content_type="application/json",
            message_id=f"{workspace_id}:{job_key}:{job_type}",
            subject=job_type,
            application_properties={"workspace_id": workspace_id},
        )
        when = datetime.now(UTC) + timedelta(seconds=delay_seconds)
        with self._sender_factory() as sender:
            sender.schedule_messages(message, when)
