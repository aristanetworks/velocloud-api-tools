# ModuleWriteError names ModulePlan, ConfigurationModuleBase and ModuleWrite in
# annotations only; their modules import this one.
# pyright: reportImportCycles=false

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final, Literal, cast, override

from pydantic import MISSING

from pyvelo.core.base import VcoModel
from pyvelo.core.changes import patch_of
from pyvelo.core.rebase import ItemMove, ItemPart, ItemPlace, Path, RawChange
from pyvelo.core.response_check import PlannedChange, ServerChange
from pyvelo.core.show import moves_text, shown_together

if TYPE_CHECKING:
    from pyvelo.client.module_check import ModuleWrite
    from pyvelo.client.module_write import ModulePlan
    from pyvelo.client.schemas.configuration import ConfigurationModuleBase


def _rebuild_error(
    cls: type[VcoError], args: tuple[Any, ...], attributes: dict[str, Any]
) -> VcoError:
    """Build an error of *cls* without calling its constructor, from its ``args`` and attributes."""
    error = cls.__new__(cls)
    error.args = args
    error.__dict__.update(attributes)
    return error


class VcoError(Exception):
    """Base exception for all pyvelo VCO client errors.

    An error is pickled and copied from its class, its ``args`` and its
    attributes (notes included), not by calling the constructor again: most
    constructors take more than the message, and ``args`` holds only the
    message. As with any exception, ``__cause__``, ``__context__`` and the
    traceback are not carried.
    """

    @override
    def __reduce__(self) -> tuple[Any, ...]:
        attributes = dict(self.__dict__)
        if "__notes__" in attributes:
            # A shallow copy must not add its notes to this error's list.
            attributes["__notes__"] = list(cast(list[str], attributes["__notes__"]))
        return (_rebuild_error, (type(self), self.args, attributes))


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
    """Raised when the VCO returns a non-2xx HTTP response.

    ``body`` is the first 500 characters of the answer, which the message
    shows; ``text`` is all of it.
    """

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.text = body
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
    """Raised when edits to a module are ones a save could not write.

    The model parses a document lossily (a key absent from the document reads as
    its default), so assigning that default changes nothing in what is written;
    and this library writes some sections at one configuration level only, so
    an edit to one at the other level is not written either.
    ``paths`` locates each such edit; ``fields`` is the same, as text.
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
            f"{module}: a save cannot write these edits: {', '.join(self.fields)}. "
            "A save sends what differs from the module as read, and at these "
            "fields nothing would. Either this library does not write the field "
            "at this configuration level (some sections are written for a "
            "profile only, or for an edge only: make the edit at the level that "
            "writes it), or the field was absent as read and now holds its "
            "default, the value it already read as (leave it out, or set "
            "another value)."
        )
        if self.override_flags:
            flags = ", ".join(str(path) for path in self.override_flags)
            message += (
                f" Of these, the override flags are: {flags}. An edit to a "
                "sibling set them, the module as read has no key for them and "
                "they already read as on."
            )
        super().__init__(message)


type WriteAnswer = Literal["row", "counted", "refused"]
"""What the VCO said of a module write.

``"row"``: it answered with a module row. ``"counted"``: it answered with a
row count of at least one, or an insert's ID, so it says it stored the write.
``"refused"``: it refused the write's version (its precondition error, or a
row count of zero).
"""


def _place_or_order(place: ItemPlace | None, moves: tuple[ItemMove, ...] | None) -> str:
    """What follows a path on a line of a write error.

    ``: order: <moves>`` for an order entry with moves, ``: order`` for one
    without, `` (<place>)`` for a change with a place, else nothing.
    """
    if moves is not None:
        return f": order: {moves_text(moves)}" if moves else ": order"
    if place is not None:
        return f" ({place})"
    return ""


def _where(change: PlannedChange | ServerChange) -> str:
    """The path of *change*, with ``refs`` before it for a refs entry."""
    return f"refs {change.path}" if change.part == "refs" else str(change.path)


def _what(change: PlannedChange) -> str:
    """The path of *change*, then its place or its order."""
    return _where(change) + _place_or_order(change.place, change.moves)


class ModuleWriteError(VcoError):
    """A module write that was sent, or was about to be sent again, and is not known to be stored as planned.

    ``ConfigConflictError`` and ``WriteNotAppliedError`` are a write whose
    module as stored was read and holds the changes only in part. They carry
    ``module``, and ``lost_edits``, the planned changes that are not stored, to
    redo (``redo_patch()`` is the patch that redoes them, ``not_redone()`` the
    ones it leaves out); ``applied``, those that are; ``left_out``, those the
    VCO stores its own way, neither applied nor lost; and ``server_changes``,
    what else differs between the payload and what is stored.
    ``WriteOutcomeUnknownError`` is a write whose module as stored could not be
    read: it has no ``module`` and no lists. ``str(error)`` lists them by path
    (``render``), all but ``left_out``.
    """

    module_id: int
    module_name: str
    version: str
    """The version the write carried; ``""`` for none."""
    plan: ModulePlan | None
    module: ConfigurationModuleBase | None
    """The module as the VCO holds it now. ``write_module`` sets it on every
    ``ConfigConflictError`` and ``WriteNotAppliedError``; only
    ``WriteOutcomeUnknownError`` has None."""
    lost_edits: tuple[PlannedChange, ...]
    applied: tuple[PlannedChange, ...]
    left_out: tuple[PlannedChange, ...]
    """Planned changes the VCO stores otherwise than as sent (``ResponseCheck.left_out``)."""
    server_changes: tuple[ServerChange, ...]
    completed: tuple[ModuleWrite[Any], ...]
    """The earlier writes of the same save, which are stored. Set by the save."""
    stored_parts: dict[Path, ItemPart]
    """For each item of every keyed list the check walks that the module as stored
    holds too, by its path, the part that names it there (``ResponseCheck.stored_parts``)."""

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
        stored_parts: Mapping[Path, ItemPart] | None = None,
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
        self.stored_parts = dict(stored_parts or {})
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

    def _notes(self) -> list[str]:
        """Lines between the headline and the lists."""
        return []

    def render(self) -> str:
        """The message: the headline, any notes, then each list by path; a list with no entries is left out."""
        lines = [self._headline(), *self._notes()]
        if self.lost_edits:
            lines.append("lost edits (not stored; `error.redo_patch()` redoes them):")
            for change in self.lost_edits:
                as_read, intended, stored = shown_together(
                    change.as_read, change.intended, change.stored
                )
                line = (
                    f"  {_what(change)}: read {as_read}, "
                    f"meant {intended}, stored {stored}"
                )
                if change.clash:
                    line += "  [clash: also changed on the VCO]"
                lines.append(line)
        if self.applied:
            lines.append("already stored:")
            lines.extend(f"  {_what(change)}" for change in self.applied)
        if self.server_changes:
            lines.append("stored otherwise than sent:")
            for server_change in self.server_changes:
                as_read, stored = shown_together(
                    server_change.as_read, server_change.stored
                )
                lines.append(
                    f"  {_where(server_change)}: read {as_read}, stored {stored}"
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

    def redo_patch(self, include_clashes: bool = False) -> dict[str, object]:
        """The patch that redoes the lost edits on ``module``, the module as stored.

        It is plain JSON (``json.loads(json.dumps(patch)) == patch``), rooted
        at the module, so
        ``await pipeline.apply(state, module=error.module_name, patch=error.redo_patch())``
        takes it, and so does ``error.module.data.merge_patch(error.redo_patch())``
        without a state. Each lost edit is redone from what is stored to what
        was meant: an added item goes back in its place, a lost order is made
        again by the moves from the stored order, and each item is named as the
        stored list names it, so an edit to "the second rule" still reaches
        that rule when another writer put one in front of it. An item the plan
        added is added again, never set on the item another writer has under
        its name or at its place: where that name is taken, ``apply`` refuses.

        A clash is an edit whose value was also changed on the VCO, and
        redoing it overwrites that change: it is left out unless
        *include_clashes*. A ref row is left out (a patch changes data, not
        refs), and so is an edit no patch can make or whose item is no longer
        stored or cannot be named alone. ``not_redone`` lists what is left
        out. A key the VCO does not know, which a write drops, is neither
        redone nor listed. A change made in a segment the plan reconciled
        (the edge had none of its own) is redone in the segment's place, and
        ``apply`` adds the segment again.

        The patch is empty, and nothing is left out, for a
        ``WriteOutcomeUnknownError``: the caller's data still holds the edits.
        It is empty, with every lost edit left out, for an error that has no
        plan or no module.

        ``apply`` checks the patch like any other and may refuse it, changing
        nothing, where the stored data no longer takes it (a rename to a name
        another writer gave an item, for example) or where the edit is one
        ``apply`` refuses as a patch (a segment added to a profile by
        assignment): make such an edit again the way it was made.
        """
        return self._redo(include_clashes)[0]

    def not_redone(self, include_clashes: bool = False) -> tuple[PlannedChange, ...]:
        """The lost edits that ``redo_patch(include_clashes)`` leaves out, in the order of ``lost_edits``."""
        return self._redo(include_clashes)[1]

    def _redo(
        self, include_clashes: bool
    ) -> tuple[dict[str, object], tuple[PlannedChange, ...]]:
        """The patch that redoes the lost edits, and the lost edits it leaves out."""
        plan, module = self.plan, self.module
        data: object = None if module is None else getattr(module, "data", None)
        if plan is None or not isinstance(data, VcoModel):
            return {}, self.lost_edits
        stored_parts = dict(self.stored_parts)
        changes: list[RawChange] = []
        owner: dict[int, PlannedChange] = {}
        left_out: set[int] = set()
        for edit in self.lost_edits:
            if edit.part == "refs" or (edit.clash and not include_clashes):
                left_out.add(id(edit))
            elif edit.path in plan.legacy_keys:
                continue
            elif _is_reconciled_segment(plan, edit):
                for change in _changes_below(plan, edit.path):
                    owner[id(change)] = edit
                    changes.append(change)
                    # Nothing of the segment is stored: its items are named as
                    # the plan names them.
                    for n, part in enumerate(change.path.parts, 1):
                        if not isinstance(part, str):
                            path = Path(change.path.parts[:n])
                            _ = stored_parts.setdefault(path, part)
            else:
                # An item the plan added is added again, never set: the item
                # stored under its name, or at its place, is another writer's.
                change = RawChange(
                    edit.path,
                    MISSING if edit.as_read is MISSING else edit.stored,
                    edit.intended,
                    place=edit.place,
                    moves=edit.moves,
                )
                owner[id(change)] = edit
                changes.append(change)
        patch, unwritten = patch_of(changes, type(data), stored_parts=stored_parts)
        left_out.update(id(owner[id(change)]) for change in unwritten)
        return patch, tuple(edit for edit in self.lost_edits if id(edit) in left_out)


def _changes_below(plan: ModulePlan, path: Path) -> list[RawChange]:
    """The changes of *plan* at a path below *path*."""
    depth = len(path.parts)
    return [
        change
        for change in plan.changes
        if len(change.path.parts) > depth and change.path.parts[:depth] == path.parts
    ]


def _is_reconciled_segment(plan: ModulePlan, edit: PlannedChange) -> bool:
    """Whether *edit* is the entry of a segment the plan reconciled.

    The edge had no entry for the segment, so nothing of it is stored and the
    check finds the whole entry added; what the plan changed inside it is what
    to redo.
    """
    parts = edit.path.parts
    return (
        bool(plan.reconciled_segments)
        and edit.as_read is MISSING
        and len(parts) == 2
        and parts[0] == "segments"
        and not isinstance(parts[1], str)
        and all(change.path != edit.path for change in plan.changes)
    )


class ConfigConflictError(ModuleWriteError):
    """Raised when a module write is rejected because its version is stale.

    The module changed on the VCO after it was read, and the write's changes
    are not all stored. ``stated`` says how the refusal is known. True: the
    VCO said it refused the version (``detail`` says how). False: the VCO
    answered with the module, which holds none of the changes and is as read
    at each, and the refusal is inferred from that. ``lost_edits`` are the
    edits to redo, and may be empty after a stated refusal when the check
    cannot tell what is stored; ``module``, as the VCO holds it, is what to
    redo them on. ``write_module`` always sets it.
    """

    detail: str
    stated: bool

    def __init__(
        self,
        module_id: int,
        version: str,
        detail: str,
        *,
        stated: bool = True,
        module_name: str = "",
        plan: ModulePlan | None = None,
        module: ConfigurationModuleBase | None = None,
        lost_edits: Sequence[PlannedChange] = (),
        applied: Sequence[PlannedChange] = (),
        left_out: Sequence[PlannedChange] = (),
        server_changes: Sequence[ServerChange] = (),
        stored_parts: Mapping[Path, ItemPart] | None = None,
    ) -> None:
        self.detail = detail
        self.stated = stated
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
            stored_parts=stored_parts,
        )

    @override
    def _headline(self) -> str:
        if self.stated:
            return (
                f"Configuration module {self._label()} changed since it was read "
                f"(version {self.version}): {self.detail}"
            )
        return (
            f"Configuration module {self._label()}: {self.detail}. The VCO seems "
            f"to have refused the write (version {self.version}) without saying "
            "so, as it does when the module changed since it was read"
        )

    @override
    def _notes(self) -> list[str]:
        if self.stated and not self.lost_edits and self.plan is not None:
            note = (
                "Nothing is listed as lost: the check cannot tell whether every "
                "change of this write is stored. Compare the module as stored "
                "with what you meant, and make the change again where it is missing."
            )
            return [note]
        return []


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
        stored_parts: Mapping[Path, ItemPart] | None = None,
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
            stored_parts=stored_parts,
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


class WriteOutcomeUnknownError(ModuleWriteError):
    """Raised when a module write was sent, or was about to be sent again, and the module as stored could not be read.

    The write's answer did not parse, or it was not this module's row, or the
    read-back found no module to check (``detail`` says which). Nothing is
    known of what is stored, so no change is listed as lost or stored:
    ``plan`` holds what was sent, or what was about to be sent, and ``module``
    is None. ``answer`` is what the
    VCO said of the write itself: ``"refused"`` (it refused the version, so it
    stored nothing of this write), ``"counted"`` (it counted the write, so the
    changes are most likely stored) or ``"row"`` (it answered with a row that
    could not be used). ``module_id`` is the module's ID; for an insert the VCO
    counted or answered with a row, the ID it gave. When the read request
    itself fails, its own error is raised instead, and the write may be stored.

    ``write_module(if_absent=True)`` raises it too, before it sends anything,
    when the read it makes before an insert whose earlier answer was lost
    cannot be used: the configuration does not parse, or the module of the
    plan's name found there does not. An earlier insert of the module may be
    stored.
    """

    detail: str
    answer: WriteAnswer

    def __init__(
        self,
        module_id: int,
        version: str,
        detail: str,
        *,
        answer: WriteAnswer,
        module_name: str,
        plan: ModulePlan,
    ) -> None:
        self.detail = detail
        self.answer = answer
        super().__init__(module_id, version, module_name=module_name, plan=plan)

    @override
    def _headline(self) -> str:
        return (
            f"Configuration module {self._label()}: the module as stored could "
            f"not be read: {self.detail}"
        )

    @override
    def _notes(self) -> list[str]:
        if self.answer == "refused":
            said = (
                "The VCO refused this write, so it stored nothing of it. Whether "
                "the changes below are stored already is not known."
            )
        elif self.answer == "counted":
            said = "The VCO counted the write, so the changes below are most likely stored."
        else:
            said = "Whether the changes below are stored is not known."
        lines = [said, "planned, not checked:"]
        if self.plan is not None:
            for change in self.plan.changes:
                before, after = shown_together(change.before, change.after)
                where = f"{change.path}{_place_or_order(change.place, change.moves)}"
                lines.append(f"  {where}: read {before}, meant {after}")
            if self.plan.refs is not None:
                lines.append("  refs: replaced")
        lines.append(
            "Read the configuration again and compare before you write this "
            "module again."
        )
        return lines


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


def _lists_precondition(data: object) -> bool:
    """Whether the ``data`` of a JSON-RPC error lists an ``error[]`` entry with the code ``PRECONDITION_FAILED``."""
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


def is_precondition_failure(error: VcoOperationError) -> bool:
    """Whether *error* is the VCO refusing a stale module version before it writes.

    A VCO with its conditional check on answers an update whose ``version`` is
    not the module's current one with a ``validationError`` whose data lists an
    error with the code ``PRECONDITION_FAILED``, and writes nothing.
    """
    return _lists_precondition(cast(object, error.data))


def is_precondition_answer(text: str) -> bool:
    """Whether *text* is a JSON-RPC answer carrying the precondition refusal.

    The same refusal as :func:`is_precondition_failure`, found in the body of
    an answer that came with a non-2xx HTTP status, and so was never parsed.
    False for text that is not JSON, is not an object, or has no ``error``
    object whose data lists the code.
    """
    try:
        answer = cast(object, json.loads(text))
    except ValueError:
        return False
    if not isinstance(answer, Mapping):
        return False
    error = cast(Mapping[str, object], answer).get("error")
    if not isinstance(error, Mapping):
        return False
    return _lists_precondition(cast(Mapping[str, object], error).get("data"))


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
