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


class ValidationFailed(NodoError):
    code = "validation_error"
    status_code = 422
    public_message = "Request validation failed"
