# ModuleWriteError names ModulePlan, ConfigurationModuleBase and ModuleWrite in
# annotations only; their modules import this one.
# pyright: reportImportCycles=false

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final, cast, override

from pydantic import MISSING

from pyvelo.core.rebase import Path
from pyvelo.core.response_check import PlannedChange, ServerChange

if TYPE_CHECKING:
    from pyvelo.client.module_check import ModuleWrite
    from pyvelo.client.module_write import ModulePlan
    from pyvelo.client.schemas.configuration import ConfigurationModuleBase


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


_VALUE_WIDTH: Final = 80
"""How much of a value's JSON a message shows."""


def _shown(value: object) -> str:
    """*value* as a message shows it: its JSON, cut to ``_VALUE_WIDTH``; ``(nothing)`` for ``MISSING``."""
    if value is MISSING:
        return "(nothing)"
    text = json.dumps(value, sort_keys=True)
    return text if len(text) <= _VALUE_WIDTH else f"{text[:_VALUE_WIDTH]}…"


def _where(change: PlannedChange | ServerChange) -> str:
    """The path of *change*, with ``refs`` before it for a refs entry."""
    return f"refs {change.path}" if change.part == "refs" else str(change.path)


class ModuleWriteError(VcoError):
    """A module write the VCO answered, whose changes are not all stored.

    ``lost_edits`` are the planned changes that are not stored, to redo;
    ``applied`` those that are; ``left_out`` those the VCO stores its own way,
    neither applied nor lost; ``server_changes`` what else differs between
    the payload and what is stored. ``str(error)`` lists them by path
    (``render``), all but ``left_out``.
    """

    module_id: int
    module_name: str
    version: str
    """The version the write carried; ``""`` for none."""
    plan: ModulePlan | None
    module: ConfigurationModuleBase | None
    """The module as the VCO holds it now; None if it was not read."""
    lost_edits: tuple[PlannedChange, ...]
    applied: tuple[PlannedChange, ...]
    left_out: tuple[PlannedChange, ...]
    """Planned changes the VCO stores otherwise than as sent (``ResponseCheck.left_out``)."""
    server_changes: tuple[ServerChange, ...]
    completed: tuple[ModuleWrite[Any], ...]
    """The earlier writes of the same save, which are stored. Set by the save."""

    def __init__(
        self,
        module_id: int,
        version: str,
        *,
        module_name: str = "",
        plan: ModulePlan | None = None,
        module: ConfigurationModuleBase | None = None,
        lost_edits: Sequence[PlannedChange] = (),
        applied: Sequence[PlannedChange] = (),
        left_out: Sequence[PlannedChange] = (),
        server_changes: Sequence[ServerChange] = (),
    ) -> None:
        self.module_id = module_id
        self.module_name = module_name
        self.version = version
        self.plan = plan
        self.module = module
        self.lost_edits = tuple(lost_edits)
        self.applied = tuple(applied)
        self.left_out = tuple(left_out)
        self.server_changes = tuple(server_changes)
        self.completed = ()
        super().__init__(self._headline())

    def _label(self) -> str:
        """The module as a message names it: its ID, and its name when it has one."""
        if self.module_name:
            return f"{self.module_id} ({self.module_name})"
        return str(self.module_id)

    def _headline(self) -> str:
        return (
            f"Configuration module {self._label()}: the write is not stored as planned"
        )

    def render(self) -> str:
        """The message: the headline, then each list by path; a list with no entries is left out."""
        lines = [self._headline()]
        if self.lost_edits:
            lines.append("lost edits (not stored; redo these):")
            for change in self.lost_edits:
                line = (
                    f"  {_where(change)}: read {_shown(change.as_read)}, "
                    f"meant {_shown(change.intended)}, stored {_shown(change.stored)}"
                )
                if change.clash:
                    line += "  [clash: also changed on the VCO]"
                lines.append(line)
        if self.applied:
            lines.append("already stored:")
            lines.extend(f"  {_where(change)}" for change in self.applied)
        if self.server_changes:
            lines.append("stored otherwise than sent:")
            lines.extend(
                f"  {_where(change)}: read {_shown(change.as_read)}, "
                f"stored {_shown(change.stored)}"
                for change in self.server_changes
            )
        if self.completed:
            lines.append(
                "written before this one: "
                + ", ".join(
                    f"{write.name} ({write.operation})" for write in self.completed
                )
            )
        return "\n".join(lines)

    @override
    def __str__(self) -> str:
        # Computed when asked: ``completed`` is set after the error is built.
        return self.render()


class ConfigConflictError(ModuleWriteError):
    """Raised when a module write is rejected because its version is stale.

    The module changed on the VCO after it was read, and the write's changes
    are not all stored. The VCO refused the version (``detail`` says how), or
    the module as stored holds none of the changes and is as read at each:
    a stale version refused by a VCO that does not say so. ``lost_edits`` are
    the edits to redo; ``module``, when it was read, is what to redo them on.
    """

    detail: str

    def __init__(
        self,
        module_id: int,
        version: str,
        detail: str,
        *,
        module_name: str = "",
        plan: ModulePlan | None = None,
        module: ConfigurationModuleBase | None = None,
        lost_edits: Sequence[PlannedChange] = (),
        applied: Sequence[PlannedChange] = (),
        left_out: Sequence[PlannedChange] = (),
        server_changes: Sequence[ServerChange] = (),
    ) -> None:
        self.detail = detail
        super().__init__(
            module_id,
            version,
            module_name=module_name,
            plan=plan,
            module=module,
            lost_edits=lost_edits,
            applied=applied,
            left_out=left_out,
            server_changes=server_changes,
        )

    @override
    def _headline(self) -> str:
        return (
            f"Configuration module {self._label()} changed since it was read "
            f"(version {self.version}): {self.detail}"
        )


class WriteNotAppliedError(ModuleWriteError):
    """Raised when the VCO stored a module write otherwise than as planned.

    Some planned changes are stored and some are not, or none is and a value
    at a planned path is not the one read: the VCO stored it in another form,
    or another writer changed it. ``lost_edits`` are the changes not stored,
    with the value sent and the value stored.
    """

    def __init__(
        self,
        module_id: int,
        version: str,
        *,
        module_name: str,
        plan: ModulePlan,
        module: ConfigurationModuleBase,
        lost_edits: Sequence[PlannedChange],
        applied: Sequence[PlannedChange] = (),
        left_out: Sequence[PlannedChange] = (),
        server_changes: Sequence[ServerChange] = (),
    ) -> None:
        super().__init__(
            module_id,
            version,
            module_name=module_name,
            plan=plan,
            module=module,
            lost_edits=lost_edits,
            applied=applied,
            left_out=left_out,
            server_changes=server_changes,
        )

    @override
    def _headline(self) -> str:
        lost = len(self.lost_edits)
        headline = (
            f"Configuration module {self._label()}: {lost} of "
            f"{lost + len(self.applied)} planned changes are not stored as sent"
        )
        # The reason holds when nothing is stored and a value moved. An insert
        # that stored nothing has nothing there that moved.
        if self.applied or not any(change.clash for change in self.lost_edits):
            return headline
        return (
            f"{headline}. None is stored, and a value there is not the one read: "
            "the VCO stored it in another form, or another writer changed it."
        )


class UnboundError(VcoError):
    """Raised when a segment a layer lacks is asked for, with no profile to copy it from.

    The layer data has not been bound to a profile (see ``bind_profile``), which
    happens when it is built outside ``ConfigState``.
    """


class DetachedObjectError(VcoError):
    """Raised by an edit through an object that a save replaced.

    The state holds the module as the VCO stored it; this object belongs to
    the module that was there before. Take the object from the state again.
    ``paths`` are where in the module such edits were found, when they were
    found by comparison; they may come from several modules, which the message
    names, a line each. An assignment or ``merge_patch`` that raises names the
    object and leaves them empty.
    """

    paths: list[Path]

    def __init__(self, message: str, paths: Sequence[Path] = ()) -> None:
        self.paths = list(paths)
        super().__init__(message)


PRECONDITION_FAILED: Final = "UPDATE_CONFIGURATION_MODULE_PRECONDITION_FAILED"
"""The code of the VCO's refusal of a stale module version, in ``error.data.error[]``."""


def is_precondition_failure(error: VcoOperationError) -> bool:
    """Whether *error* is the VCO refusing a stale module version before it writes.

    A VCO with its conditional check on answers an update whose ``version`` is
    not the module's current one with a ``validationError`` whose data lists an
    error with the code ``PRECONDITION_FAILED``, and writes nothing.
    """
    data = cast(object, error.data)
    if not isinstance(data, Mapping):
        return False
    entries = cast(Mapping[str, object], data).get("error")
    if not isinstance(entries, list):
        return False
    return any(
        isinstance(entry, Mapping)
        and cast(Mapping[str, object], entry).get("code") == PRECONDITION_FAILED
        for entry in cast(list[object], entries)
    )


def is_version_conflict(*, error: str | None, rows: int | None) -> bool:
    """Whether an update result means the module's version was stale.

    Observed against a live Orchestrator: an update that carries a version other
    than the module's current one (an older one, one never issued, one that is
    not a number) is answered with an ordinary result, ``{"rows": 0}``, with no
    error text, and nothing is written. The current version answers
    ``{"rows": 1}``, also when the data is unchanged, so zero rows never means
    "nothing to change". A request without a version is never checked and writes
    unconditionally. That is a VCO with its conditional check off. With it on,
    a stale version gets no result: it is refused with the JSON-RPC
    ``validationError`` ``UPDATE_CONFIGURATION_MODULE_PRECONDITION_FAILED``
    (:func:`is_precondition_failure`). In a result, a conflict is zero rows with
    no error text, and nothing else.
    """
    return not error and rows == 0
