from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jwt

from nodo_api.config import Settings


class AuthError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    workspace_id: str


class DevAuthenticator:
    def __init__(self, settings: Settings) -> None:
        self._tokens = {
            settings.dev_token: Principal(settings.dev_user_id, settings.dev_workspace_id)
        }

    def add_dev_token(self, token: str, user_id: str, workspace_id: str) -> None:
        self._tokens[token] = Principal(user_id, workspace_id)

    def authenticate(self, token: str) -> Principal:
        principal = self._tokens.get(token)
        if principal is None:
            raise AuthError("Invalid bearer token")
        return principal


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
                options={"require": ["exp", "sub", "workspace_id"]},
            )
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            raise AuthError("Invalid OIDC token") from exc
        return Principal(str(claims["sub"]), str(claims["workspace_id"]))

    def _find_key(self, kid: str) -> Any:
        for jwk in self._key_provider().get("keys", []):
            if jwk.get("kid") == kid:
                return jwt.PyJWK.from_dict(jwk).key
        raise AuthError("Signing key not found")


class AuthManager:
    def __init__(
        self,
        settings: Settings,
        key_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        if settings.auth_mode == "dev":
            self._authenticator = DevAuthenticator(settings)
        else:
            if key_provider is None:
                raise ValueError("An injectable OIDC key provider is required")
            self._authenticator = OidcAuthenticator(settings, key_provider)

    def authenticate(self, token: str) -> Principal:
        return self._authenticator.authenticate(token)

    def add_dev_token(self, token: str, user_id: str, workspace_id: str) -> None:
        if not isinstance(self._authenticator, DevAuthenticator):
            raise AuthError("Dev tokens are disabled in oidc mode")
        self._authenticator.add_dev_token(token, user_id, workspace_id)
