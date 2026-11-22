from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nodo_api.domain.hierarchy import LinkRefusal


class NodoError(Exception):
    code = "internal_error"
    status_code = 500
    public_message = "Internal server error"


class NotFoundError(NodoError):
    code = "not_found"
    status_code = 404
    public_message = "Resource not found"


class AuthenticationError(NodoError):
    code = "unauthorized"
    status_code = 401
    public_message = "Authentication required"


class ConflictError(NodoError):
    code = "conflict"
    status_code = 409
    public_message = "Request conflicts with current state"


class OriginalMissing(ConflictError):
    """The stored original is gone (retention or a cancel erased it): upload the file again."""

    code = "original_missing"
    public_message = "The original file is no longer stored"


class CycleRefused(ConflictError):
    """A parent link would close a cycle (or point at itself)."""

    code = "would_create_cycle"
    public_message = "The parent link would create a cycle"


class DepthRefused(ConflictError):
    """A parent link would make a path longer than the depth cap."""

    code = "too_deep"
    public_message = "The parent link would make the hierarchy too deep"


class DuplicateName(ConflictError):
    """A sibling collection already has this name (case and accent insensitive)."""

    code = "duplicate_name"
    public_message = "A sibling already has this name"


class TooManyCollections(ConflictError):
    code = "too_many_collections"
    public_message = "The collection limit is reached"


def refusal_error(code: LinkRefusal) -> ConflictError:
    """The 409 for a refused parent link: `self` and `cycle` are both a cycle."""
    return DepthRefused() if code == "too_deep" else CycleRefused()


class ModelUnavailable(NodoError):
    """A local model the request needs is not installed or could not start."""

    code = "model_unavailable"
    status_code = 503
    public_message = "A local model needed for this request is not available"


class ValidationFailed(NodoError):
    code = "validation_error"
    status_code = 422
    public_message = "Request validation failed"


class UseMeetingsApi(ValidationFailed):
    public_message = "use the meetings API"


class NotAvailableOnEngine(NodoError):
    code = "not_available_on_engine"
    status_code = 501
    public_message = "This feature is not available on the configured engine"
