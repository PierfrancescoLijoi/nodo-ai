"""The local engine marks import responses "slow_on_this_machine" (spec section 16, Task 16)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from nodo_api.api.v1.routes import SLOW_ON_THIS_MACHINE, _import_status
from nodo_api.domain.sources import ImportJob

NOW = datetime(2026, 11, 19, tzinfo=UTC)
JOB = ImportJob("ws", "u", "src_1", "queued", None, 0, 0, 0, None, False, NOW, NOW)


def _warnings(backend: str, notice: bool) -> list[str]:
    settings = SimpleNamespace(backend=backend, local_import_slow_notice=notice)
    body = _import_status(JOB, SimpleNamespace(settings=settings)).model_dump(by_alias=True)
    return list(body["import"]["warnings"])


@pytest.mark.parametrize(
    ("backend", "notice", "expected"),
    [
        ("local", True, [SLOW_ON_THIS_MACHINE]),
        ("local", False, []),
        ("memory", True, []),
        ("azure", True, []),
    ],
)
def test_only_the_local_engine_carries_the_slow_notice(backend, notice, expected) -> None:
    assert _warnings(backend, notice) == expected


def test_the_default_setting_shows_the_notice() -> None:
    from nodo_api.config import Settings

    settings = Settings(
        environment="test", auth_mode="dev", attestation_hmac_key="test-only-key-0123456789abcdef"
    )
    assert settings.local_import_slow_notice is True
