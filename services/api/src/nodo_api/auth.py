from __future__ import annotations

import hmac
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jwt

from nodo_api.config import Settings
from nodo_api.jwks import JwksUnavailable

_USER_ID = re.compile(r"[A-Za-z0-9_.:@|-]{1,128}")
_WORKSPACE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class AuthError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    workspace_id: str


LOCAL_USER = "local-user"
LOCAL_WORKSPACE = "local"


class DevAuthenticator:
    def __init__(self, settings: Settings) -> None:
        if settings.auth_mode == "local":  # one person, one secret per launch (ADR-014)
            assert settings.local_token is not None
            self._tokens = {
                settings.local_token.get_secret_value(): Principal(LOCAL_USER, LOCAL_WORKSPACE)
            }
            return
        self._tokens = {
            settings.dev_token: Principal(settings.dev_user_id, settings.dev_workspace_id)
        }

    def add_dev_token(self, token: str, user_id: str, workspace_id: str) -> None:
        self._tokens[token] = Principal(user_id, workspace_id)

    def authenticate(self, token: str) -> Principal:
        # Compare against every token in constant time instead of a dict lookup.
        match: Principal | None = None
        for known, principal in self._tokens.items():
            if hmac.compare_digest(known.encode(), token.encode()):
                match = principal
        if match is None:
            raise AuthError("Invalid bearer token")
        return match


class OidcAuthenticator:
    def __init__(self, settings: Settings, key_provider: Callable[[], dict[str, Any]]) -> None:
        self._settings = settings
        self._key_provider = key_provider

    def authenticate(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
            key = self._find_key(str(header.get("kid", "")))
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self._settings.oidc_audience,
                issuer=self._settings.oidc_issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "workspace_id"]},
            )
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            raise AuthError("Invalid OIDC token") from exc
        self._check_client(claims)
        return self._principal(claims)

    def _check_client(self, claims: dict[str, Any]) -> None:
        """Only tokens issued to the expected app registrations (v2 claim azp, v1 claim appid)."""
        allowed = self._settings.oidc_allowed_client_ids
        if allowed and (claims.get("azp") or claims.get("appid")) not in allowed:
            raise AuthError("Token was issued to another application")

    def _principal(self, claims: dict[str, Any]) -> Principal:
        user_id, workspace_id = claims["sub"], claims["workspace_id"]
        # The tenant claim must be a plain string minted by the identity provider: coercing a list
        # or accepting an empty value would collapse users into a shared tenant.
        if not isinstance(user_id, str) or not _USER_ID.fullmatch(user_id):
            raise AuthError("Invalid subject claim")
        if not isinstance(workspace_id, str) or not _WORKSPACE_ID.fullmatch(workspace_id):
            raise AuthError("Invalid workspace claim")
        if claims["exp"] - claims["iat"] > self._settings.oidc_max_token_age_seconds:
            raise AuthError("Token lifetime is too long")
        return Principal(user_id, workspace_id)

    def _find_key(self, kid: str) -> Any:
        provider = self._key_provider
        try:
            key_set = provider.keys(kid) if hasattr(provider, "keys") else provider()
        except JwksUnavailable as exc:
            raise AuthError("Signing keys unavailable") from exc
        for jwk in key_set.get("keys", []):
            usable = jwk.get("kty") == "RSA" and jwk.get("alg", "RS256") == "RS256"
            if jwk.get("kid") == kid and jwk.get("use", "sig") == "sig" and usable:
                return jwt.PyJWK.from_dict(jwk).key
        raise AuthError("Signing key not found")


class AuthManager:
    def __init__(
        self,
        settings: Settings,
        key_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._settings_mode_local = settings.auth_mode == "local"
        if settings.auth_mode in ("dev", "local"):
            self._authenticator = DevAuthenticator(settings)
        else:
            if key_provider is None:
                raise ValueError("An injectable OIDC key provider is required")
            self._authenticator = OidcAuthenticator(settings, key_provider)

    def authenticate(self, token: str) -> Principal:
        return self._authenticator.authenticate(token)

    def add_dev_token(self, token: str, user_id: str, workspace_id: str) -> None:
        if not isinstance(self._authenticator, DevAuthenticator) or self._settings_mode_local:
            raise AuthError("Dev tokens are disabled in this mode")
        self._authenticator.add_dev_token(token, user_id, workspace_id)
