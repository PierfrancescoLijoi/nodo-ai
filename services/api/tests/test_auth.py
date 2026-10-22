from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from nodo_api.auth import AuthError, AuthManager, OidcAuthenticator
from nodo_api.config import Settings


@pytest.fixture(scope="module")
def rsa_keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    public_jwk.update({"kid": "test-key", "use": "sig", "alg": "RS256"})
    return private, public_jwk


def _token(private, **overrides) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": "user-a",
        "workspace_id": "workspace-a",
        "iss": "https://issuer.example/",
        "aud": "nodo-api",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": "test-key"})


def _auth(jwk) -> OidcAuthenticator:
    settings = Settings(
        auth_mode="oidc",
        oidc_issuer="https://issuer.example/",
        oidc_audience="nodo-api",
        attestation_hmac_key="test-key",
    )
    return OidcAuthenticator(settings, lambda: {"keys": [jwk]})


def test_valid_oidc_jwt(rsa_keys) -> None:
    private, jwk = rsa_keys
    principal = _auth(jwk).authenticate(_token(private))
    assert principal.user_id == "user-a"
    assert principal.workspace_id == "workspace-a"


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": datetime.now(UTC) - timedelta(minutes=1)},
        {"aud": "wrong-audience"},
    ],
)
def test_invalid_oidc_claims_are_rejected(rsa_keys, claims) -> None:
    private, jwk = rsa_keys
    with pytest.raises(AuthError):
        _auth(jwk).authenticate(_token(private, **claims))


def test_wrong_oidc_signature_is_rejected(rsa_keys) -> None:
    _, jwk = rsa_keys
    wrong_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AuthError):
        _auth(jwk).authenticate(_token(wrong_private))


def test_oidc_refuses_dev_token(rsa_keys) -> None:
    _, jwk = rsa_keys
    with pytest.raises(AuthError):
        _auth(jwk).authenticate("dev-only-token")


def test_dev_auth_rejects_unknown_token() -> None:
    manager = AuthManager(Settings(attestation_hmac_key="test-key"))
    with pytest.raises(AuthError):
        manager.authenticate("unknown")


def test_oidc_settings_require_issuer_and_audience() -> None:
    with pytest.raises(ValueError, match="issuer and audience"):
        Settings(auth_mode="oidc", attestation_hmac_key="test-key")
