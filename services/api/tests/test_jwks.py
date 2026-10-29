from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from nodo_api.auth import AuthError, OidcAuthenticator
from nodo_api.config import Settings
from nodo_api.jwks import JwksCache, JwksUnavailable

ISSUER = "https://issuer.example/tenant/v2.0"
JWKS_URL = "https://issuer.example/tenant/keys"


def _key(kid: str):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return private, jwk


class Provider:
    """A fake identity provider that counts requests and can be switched off."""

    def __init__(self, *keys: dict) -> None:
        self.keys = list(keys)
        self.calls: list[str] = []
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        if self.down:
            return httpx.Response(503)
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": JWKS_URL})
        return httpx.Response(200, json={"keys": self.keys})


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _cache(provider: Provider, clock: Clock, **kwargs) -> JwksCache:
    http = httpx.Client(transport=httpx.MockTransport(provider.handler))
    return JwksCache(ISSUER, kwargs.pop("url", None), http=http, clock=clock, **kwargs)


def test_the_key_set_url_comes_from_the_discovery_document_and_keys_are_cached() -> None:
    _, jwk = _key("a")
    provider, clock = Provider(jwk), Clock()
    cache = _cache(provider, clock)
    assert cache.keys()["keys"][0]["kid"] == "a"
    cache.keys()
    cache.keys("a")
    assert len(provider.calls) == 2  # discovery once, key set once
    assert provider.calls[0].endswith("/.well-known/openid-configuration")


def test_keys_are_refreshed_when_they_expire() -> None:
    provider, clock = Provider(_key("a")[1]), Clock()
    cache = _cache(provider, clock, url=JWKS_URL, ttl=100)
    cache.keys()
    clock.now += 101
    provider.keys = [_key("b")[1]]
    assert cache.keys()["keys"][0]["kid"] == "b"


def test_an_unknown_key_id_refreshes_once_per_cooldown_however_many_forged_tokens_arrive() -> None:
    provider, clock = Provider(_key("a")[1]), Clock()
    cache = _cache(provider, clock, url=JWKS_URL, cooldown=60)
    cache.keys()
    for _ in range(50):
        cache.keys("forged")
    assert len(provider.calls) == 1  # inside the cooldown: no extra request to the provider
    clock.now += 61
    provider.keys = [_key("a")[1], _key("forged")[1]]  # the provider rotated in the meantime
    assert {k["kid"] for k in cache.keys("forged")["keys"]} == {"a", "forged"}
    assert len(provider.calls) == 2


def test_an_outage_keeps_serving_the_last_keys_for_a_while_then_stops() -> None:
    provider, clock = Provider(_key("a")[1]), Clock()
    cache = _cache(provider, clock, url=JWKS_URL, ttl=100, stale_for=1000, cooldown=1)
    cache.keys()
    provider.down = True
    clock.now += 500
    assert cache.keys()["keys"][0]["kid"] == "a"  # stale but usable
    clock.now += 600
    with pytest.raises(JwksUnavailable):
        cache.keys()


def test_nothing_cached_and_the_provider_down_is_unavailable() -> None:
    provider = Provider()
    provider.down = True
    with pytest.raises(JwksUnavailable):
        _cache(provider, Clock(), url=JWKS_URL).keys()


@pytest.mark.parametrize(
    "document",
    [
        {"issuer": "https://other.example", "jwks_uri": JWKS_URL},
        {"issuer": ISSUER, "jwks_uri": "http://insecure.example/keys"},
    ],
)
def test_a_discovery_document_naming_another_issuer_or_plain_http_is_refused(document) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=document)

    cache = JwksCache(ISSUER, http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(JwksUnavailable):
        cache.keys()


def _settings(**overrides) -> Settings:
    values = {
        "environment": "prod",
        "auth_mode": "oidc",
        "oidc_issuer": ISSUER,
        "oidc_audience": "nodo-api",
        "attestation_hmac_key": "k" * 40,
    }
    return Settings(**{**values, **overrides})


def _token(private, kid: str = "a", **claims) -> str:
    now = datetime.now(UTC)
    body = {
        "sub": "user-a",
        "workspace_id": "workspace-a",
        "iss": ISSUER,
        "aud": "nodo-api",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        **claims,
    }
    return jwt.encode(body, private, algorithm="RS256", headers={"kid": kid})


def test_a_token_signed_with_a_freshly_rotated_key_is_accepted_after_one_refresh() -> None:
    old_private, old_jwk = _key("old")
    new_private, new_jwk = _key("new")
    provider, clock = Provider(old_jwk), Clock()
    cache = _cache(provider, clock, url=JWKS_URL, cooldown=1)
    auth = OidcAuthenticator(_settings(), cache)
    assert auth.authenticate(_token(old_private, "old")).user_id == "user-a"
    provider.keys = [old_jwk, new_jwk]
    clock.now += 5
    assert auth.authenticate(_token(new_private, "new")).user_id == "user-a"


def test_an_identity_provider_outage_is_an_authentication_error_not_a_crash() -> None:
    provider = Provider()
    provider.down = True
    private, _ = _key("a")
    auth = OidcAuthenticator(_settings(), _cache(provider, Clock(), url=JWKS_URL))
    with pytest.raises(AuthError):
        auth.authenticate(_token(private))


def test_only_tokens_issued_to_the_expected_applications_are_accepted() -> None:
    private, jwk = _key("a")
    auth = OidcAuthenticator(
        _settings(oidc_allowed_client_ids="app-one, app-two"), lambda: {"keys": [jwk]}
    )
    assert auth.authenticate(_token(private, azp="app-two")).user_id == "user-a"
    assert auth.authenticate(_token(private, appid="app-one")).user_id == "user-a"
    for claims in ({"azp": "someone-else"}, {}):
        with pytest.raises(AuthError):
            auth.authenticate(_token(private, **claims))


def test_a_key_that_is_not_an_rs256_signing_key_is_ignored() -> None:
    private, jwk = _key("a")
    for change in ({"alg": "HS256"}, {"use": "enc"}):
        auth = OidcAuthenticator(_settings(), lambda change=change: {"keys": [{**jwk, **change}]})
        with pytest.raises(AuthError):
            auth.authenticate(_token(private))
