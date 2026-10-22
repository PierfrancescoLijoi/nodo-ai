from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from nodo_api.config import Settings
from nodo_api.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        auth_mode="dev",
        dev_token="test-dev-token",
        dev_user_id="user-a",
        dev_workspace_id="workspace-a",
        attestation_hmac_key="test-only-attestation-key",
    )


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-dev-token"}


@pytest.fixture
def attestation() -> dict[str, object]:
    return {
        "notice_version": "2026-10-v1",
        "locale": "en",
        "retention_policy_id": "retention-30d",
        "acknowledged": True,
    }
