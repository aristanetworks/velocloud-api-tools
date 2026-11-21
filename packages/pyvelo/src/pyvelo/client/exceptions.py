import re
from collections.abc import Sequence
from typing import Any

from pyvelo.core.rebase import Path


class VcoError(Exception):
    """Base exception for all pyvelo VCO client errors."""


class VcoClientNotInitializedError(VcoError):
    """Raised when the client is used before connecting."""

    def __init__(self) -> None:
        super().__init__("VcoClient is not initialized")


class VcoApiError(VcoError):
    """Raised when the VCO API returns a JSON-RPC error response."""

    def __init__(
        self,
        code: int,
        message: str,
        data: Any = None,
    ) -> None:
        self.code = code
        self.message = message
        self.data = data
        detail = f"VCO API error {code}: {message}"
        if data:
            if isinstance(data, dict):
                detail += _format_validation_data(data)
            else:
                detail += f" ({data})"
        super().__init__(detail)


class VcoEmptyResultError(VcoError):
    """Raised when the VCO API returns a null result."""

    def __init__(self, method: str | None = None) -> None:
        self.method = method
        msg = "VCO API returned an empty result"
        if method:
            msg += f" for {method}"
        super().__init__(msg)


class VcoOperationError(VcoError):
    """Raised when a VCO operation returns an error in its result payload."""

    def __init__(
        self,
        operation: str,
        error: str,
        *,
        code: int | None = None,
        data: Any = None,
    ) -> None:
        self.operation = operation
        self.error = error
        self.code = code
        self.data = data
        detail = f"{operation} failed: {error}"
        if data:
            if isinstance(data, dict):
                detail += _format_validation_data(data)
            else:
                detail += f" ({data})"
        super().__init__(detail)


def _format_validation_data(data: dict[str, Any]) -> str:
    """Format VCO validation error data for readable output."""
    errors = data.get("error", [])
    warnings = data.get("warning", [])
    if not errors and not warnings:
        return f" ({data})"

    parts: list[str] = []
    for err in errors:
        msg = err.get("message", "")
        path = err.get("path", "")
        err_code = err.get("code", "")
        parts.append(f"  - [{err_code}] {path}: {msg}")
    for warn in warnings:
        msg = warn.get("message", "")
        path = warn.get("path", "")
        parts.append(f"  - [warning] {path}: {msg}")
    if parts:
        return "\n" + "\n".join(parts)
    return f" ({data})"


class VcoHttpError(VcoError):
    """Raised when the VCO returns a non-2xx HTTP response."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body[:500]
        super().__init__(f"HTTP {status_code}: {self.body}")


class VcoRateLimitError(VcoError):
    """Raised when 429 retries are exhausted."""

    def __init__(self, attempts: int) -> None:
        self.attempts = attempts
        super().__init__(f"Rate limited: retries exhausted after {attempts} attempts")


class VcoWebSocketError(VcoError):
    """Raised when a WebSocket operation fails (connection, send, or receive)."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class VcoWebSocketConnectionError(VcoWebSocketError):
    """Raised when the WebSocket connection cannot be established.

    Covers handshake failures, upgrade errors, and malformed noop messages.
    """


class VcoWebSocketTimeoutError(VcoWebSocketError):
    """Raised when a WebSocket request or diagnostic times out."""

    def __init__(self, message: str, *, timeout: float) -> None:
        self.timeout = timeout
        super().__init__(message)


class VcoWebSocketDisconnectedError(VcoWebSocketError):
    """Raised when the WebSocket disconnects and reconnection fails."""


class VcoWebSocketValidationError(VcoWebSocketError):
    """Raised when the VCO rejects a WebSocket request due to a validation error.

    This is a permanent failure — retrying the same request will not succeed.
    """


class VcoUserTypeUnknownError(VcoError):
    """Raised when none of the user-type probe endpoints succeed."""

    def __init__(self) -> None:
        super().__init__("Unable to determine user type: all probes failed")


class VcoCredentialsError(VcoError):
    """Raised when VCO credentials cannot be loaded from the environment."""


class LossyFieldError(VcoError):
    """Raised when assignments to a module cannot be expressed on save.

    The model parses a document lossily (a key absent from the document reads as
    its default), so assigning that default changes nothing in what is written.
    ``paths`` locates each such assignment; ``fields`` is the same, as text.
    ``override_flags`` are those paths that are override flags: an edit to a
    sibling sets the flag, but the document as read has no key for it and it
    already reads as on, so there is nothing to write.
    """

    def __init__(
        self,
        module: str,
        paths: Sequence[Path],
        override_flags: Sequence[Path] = (),
    ) -> None:
        self.module = module
        self.paths = list(paths)
        self.fields = [str(path) for path in self.paths]
        self.override_flags = list(override_flags)
        message = (
            f"{module}: assignment cannot be expressed on save for: "
            f"{', '.join(self.fields)}"
        )
        if self.override_flags:
            flags = ", ".join(str(path) for path in self.override_flags)
            message += (
                f" (override flags: {flags}; set by an edit to a sibling, but the "
                "document as read has no key for them and they already read as on)"
            )
        super().__init__(message)


class ConfigConflictError(VcoError):
    """Raised when a module write is rejected because its version is stale.

    The module changed on the VCO after it was read. Nothing was written; read
    the module again, re-apply the change, and save.
    """

    def __init__(self, module_id: int, version: str, detail: str) -> None:
        self.module_id = module_id
        self.version = version
        self.detail = detail
        super().__init__(
            f"Configuration module {module_id} changed since it was read "
            f"(version {version}): {detail}"
        )


class UnboundError(VcoError):
    """Raised when a segment a layer lacks is asked for, with no profile to copy it from.

    The layer data has not been bound to a profile (see ``bind_profile``), which
    happens when it is built outside ``ConfigState``.
    """


_VERSION_CONFLICT = re.compile(r"\bversion\b|conflict", re.IGNORECASE)


def is_version_conflict(*, error: str | None, rows: int | None) -> bool:
    """Whether an update response means the module's version was stale.

    Provisional: the server's response to a stale version has not been observed.
    This is the one place to correct.
    """
    if error:
        return _VERSION_CONFLICT.search(error) is not None
    return rows == 0
