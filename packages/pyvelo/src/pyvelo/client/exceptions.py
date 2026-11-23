# ModuleWriteError names ModulePlan, ConfigurationModuleBase and ModuleWrite in
# annotations only; their modules import this one.
# pyright: reportImportCycles=false

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final, Literal, cast, override

from pydantic import MISSING

from pyvelo.core.base import VcoModel, field_models, is_list, wire_name
from pyvelo.core.changes import patch_of
from pyvelo.core.patch import selector_of
from pyvelo.core.rebase import (
    ItemMove,
    ItemPart,
    ItemPlace,
    Key,
    KeyedList,
    Path,
    RawChange,
    item_parts,
    item_text,
    node_child,
)
from pyvelo.core.response_check import PlannedChange, ServerChange
from pyvelo.core.secrets import HIDDEN
from pyvelo.core.show import moves_text, patch_text, shown_together

if TYPE_CHECKING:
    from pyvelo.client.module_check import ModuleWrite
    from pyvelo.client.module_write import ModulePlan
    from pyvelo.client.schemas.configuration import ConfigurationModuleBase


def _rebuild_error(cls: type[VcoError], args: tuple[Any, ...]) -> VcoError:
    """Build an error of *cls* without calling its constructor; its attributes arrive as state."""
    error = cls.__new__(cls)
    error.args = args
    return error


class VcoError(Exception):
    """Base exception for all pyvelo VCO client errors.

    An error is pickled and copied from its class, its ``args`` and its
    attributes (notes included), not by calling the constructor again: most
    constructors take more than the message, and ``args`` holds only the
    message. The attributes are the state of the rebuilt error, so one that
    refers back to the error is copied too. As with any exception,
    ``__cause__``, ``__context__`` and the traceback are not carried.
    """

    @override
    def __reduce__(self) -> tuple[Any, ...]:
        attributes = dict(self.__dict__)
        if "__notes__" in attributes:
            # A shallow copy must not add its notes to this error's list.
            attributes["__notes__"] = list(cast(list[str], attributes["__notes__"]))
        return (_rebuild_error, (type(self), self.args), attributes)


class VcoClientNotInitializedError(VcoError):
    """Raised when the client is used without a connection."""

    def __init__(self) -> None:
        super().__init__(
            "VcoClient is not connected. Use it inside "
            "`async with VcoClient(host, token) as client:`; the connection closes "
            "when that block ends, and a client built without a transport has none "
            "before it starts."
        )


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
    """Raised when VCO credentials cannot be loaded from the environment.

    Also raised for a token that is empty or that cannot be sent.
    """


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


class RefsNotLoadedError(VcoError):
    """Raised when a plan needs the refs of a module that was read without them.

    A module from ``configuration/getConfigurationModules`` (or an insert's
    answer) carries no refs. An edit to an owner of a ref row then cannot be
    written, because the row that goes with it is not known, and a write of
    a device settings or QoS module with segments would let the VCO replace its
    refs. ``module_name`` is the module; ``paths`` are the owners the edit
    changed (empty when the write alone is refused).
    """

    module_name: str
    paths: tuple[Path, ...]

    def __init__(self, module_name: str, paths: Sequence[Path] = ()) -> None:
        self.module_name = module_name
        self.paths = tuple(paths)
        read = (
            f"{module_name} was read without its refs (it came from "
            "configuration/getConfigurationModules)"
        )
        how = (
            "Read the module with its refs and edit that one: send "
            '`configuration/getConfiguration` with {"id": <configuration ID>, '
            '"with": ["modules"]} (get_configuration), or '
            "`edge/getEdgeConfigurationStack` for an edge (get_edge_configuration_stack)."
        )
        if self.paths:
            changed = ", ".join(self.paths)
            message = (
                f"{read}, so an edit to {changed} cannot be written: the ref rows "
                f"that go with it are not known. {how}"
            )
        else:
            message = (
                f"{read}, so a write of it would let the VCO replace its refs, "
                f"which this module does not have. {how}"
            )
        super().__init__(message)


type WriteAnswer = Literal["row", "counted", "refused", "stale"]
"""What the VCO said of a module write.

``"row"``: it answered with a module row. ``"counted"``: it answered with a
row count of at least one, or an insert's ID, so it says it stored the write.
``"refused"``: it refused the write's version (its precondition error, or a
row count of zero). ``"stale"``: nothing was sent, because the module's
version on the VCO is not the one read (the read before a write that sends
refs), so the module read now is checked as after a refusal.
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


def _first[T](entries: Sequence[T], limit: int | None) -> Sequence[T]:
    """The first *limit* of *entries*; all of them for None."""
    return entries if limit is None else entries[:limit]


def _and_more(total: int, limit: int | None, attribute: str) -> list[str]:
    """The line that counts the entries of a list a message cut short, or no line when it did not."""
    if limit is None or total <= limit:
        return []
    return [f"  and {total - limit} more: error.{attribute} has them all"]


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
    ``ConfigConflictError`` and ``WriteNotAppliedError``, except for a
    ``ConfigConflictError`` whose module is gone from its configuration;
    ``WriteOutcomeUnknownError`` always has None."""
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

    def _notes(self, limit: int | None) -> list[str]:
        """Lines between the headline and the lists; a list in them shows at most *limit* entries."""
        return []

    def _document(self) -> type[VcoModel] | None:
        """The class of the module's data, which says what a value of the plan is."""
        return None if self.plan is None else self.plan.model

    def _values(
        self, change: PlannedChange | ServerChange, *values: object
    ) -> tuple[str, ...]:
        """*values* of *change* as text, with a secret as ``"***"``.

        A ref row is shown as ``"***"`` whatever it holds: rows are compared by
        identity only, and a row can hold a service's credentials.
        """
        if change.part == "refs":
            return shown_together(
                *(value if value is MISSING else HIDDEN for value in values)
            )
        return shown_together(*values, model=self._document(), path=change.path)

    def render(self, limit: int | None = 20) -> str:
        """The message: the headline, any notes, then each list by path; a list with no entries is left out.

        Each list shows its first *limit* entries (all for None), and a line
        after it counts the rest. A secret is shown as ``"***"``.
        """
        lines = [self._headline(), *self._notes(limit)]
        if self.lost_edits:
            lines.append(
                "lost edits (not stored; `error.redo_patch()` redoes those it can, "
                "`error.not_redone()` lists the rest):"
            )
            for change in _first(self.lost_edits, limit):
                as_read, intended, stored = self._values(
                    change, change.as_read, change.intended, change.stored
                )
                line = (
                    f"  {_what(change)}: read {as_read}, "
                    f"meant {intended}, stored {stored}"
                )
                if change.clash:
                    line += "  [clash: also changed on the VCO]"
                lines.append(line)
            lines.extend(_and_more(len(self.lost_edits), limit, "lost_edits"))
        if self.applied:
            lines.append("already stored:")
            lines.extend(f"  {_what(change)}" for change in _first(self.applied, limit))
            lines.extend(_and_more(len(self.applied), limit, "applied"))
        if self.server_changes:
            lines.append("stored otherwise than sent:")
            for server_change in _first(self.server_changes, limit):
                as_read, stored = self._values(
                    server_change, server_change.as_read, server_change.stored
                )
                lines.append(
                    f"  {_where(server_change)}: read {as_read}, stored {stored}"
                )
            lines.extend(_and_more(len(self.server_changes), limit, "server_changes"))
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
        *include_clashes*. A ref row of a ``RefOnly`` field (a DNS or
        authentication provider) is redone by setting the field again, in the
        segment as the stored list names it: to the service's logical ID for a
        row added, to ``null`` for a row removed, and to the row added when one
        is removed and another added at the field. Any other ref row is left
        out (a patch changes data, not refs), and so is an edit no patch can
        make or whose item is no longer stored or cannot be named alone.
        ``not_redone`` lists what is left out. A key the VCO does not know,
        which a write drops, is neither redone nor listed. A change made in a
        segment the plan reconciled (the edge had none of its own) is redone in
        the segment's place, and ``apply`` adds the segment again.

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
        stored_data: object = MISSING if module is None else module.raw_data
        if plan is None or not isinstance(data, VcoModel):
            return {}, self.lost_edits
        stored_parts = dict(self.stored_parts)
        changes: list[RawChange] = []
        owner: dict[int, PlannedChange] = {}
        left_out: set[int] = set()
        provider_edits: dict[Path, list[PlannedChange]] = {}
        for edit in self.lost_edits:
            if edit.part == "refs":
                if edit.owner is None:
                    left_out.add(id(edit))
                else:
                    provider_edits.setdefault(edit.owner, []).append(edit)
            elif edit.clash and not include_clashes:
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
        for path, edits in provider_edits.items():
            stored_adds = [
                edit
                for edit in self.applied
                if edit.part == "refs"
                and edit.owner == path
                and edit.as_read is MISSING
                and edit.intended is not MISSING
            ]
            value = _provider_value([*edits, *stored_adds])
            if value is MISSING or not _set_provider(
                patch, data, stored_data, stored_parts, path, value
            ):
                left_out.update(id(edit) for edit in edits)
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


def _provider_value(edits: Sequence[PlannedChange]) -> object:
    """The value that sets a ``RefOnly`` field again, from the refs entries *edits* at it.

    The added row's logical ID as text, or ``None`` for a row removed with none
    added; ``MISSING`` when an entry is neither. A row added carries only the
    object it goes with, so its logical ID is the first part of its path's key.
    A row removed and a row added at one owner are one set, to the added row's;
    so is a row removed and lost beside a row added and stored (*edits* then
    holds the stored one too), so that the field is not cleared.
    """
    added: object = None
    for edit in edits:
        if edit.intended is MISSING:
            continue
        key = edit.path.parts[-1]
        identity = None if not isinstance(key, Key) else key.value
        if isinstance(identity, tuple):
            identity = identity[0]
        if edit.as_read is not MISSING or not isinstance(identity, str):
            return MISSING
        added = identity
    return added


def _stored_segment(
    data: VcoModel,
    stored_data: object,
    stored_parts: Mapping[Path, ItemPart],
    item: ItemPart,
) -> ItemPart | None:
    """The part that names the segment *item* names in the ``segments`` of *stored_data*, the module's JSON as stored, or None.

    The check records it for an item it walked. Else a segment named by its key
    is the one of the stored list with that key, when the key is its alone.
    """
    found = stored_parts.get(Path(("segments", item)))
    if found is not None or not isinstance(item, Key):
        return found
    node = node_child(type(data), "segments")
    items: object = (
        cast("dict[str, object]", stored_data).get("segments")
        if isinstance(stored_data, dict)
        else None
    )
    if not isinstance(node, KeyedList) or not is_list(items):
        return None
    return next(
        (
            part
            for part in item_parts(items, node)
            if item_text(part) == item_text(item)
        ),
        None,
    )


def _field_spellings(
    models: Sequence[type[VcoModel]], names: Sequence[str]
) -> list[tuple[str, str]] | None:
    """For each of the fields *names* below one of *models*, its python name and its wire name; None where no model has the field."""
    name, *rest = names
    for model in models:
        field_info = model.model_fields.get(name)
        if field_info is None:
            continue
        below = _field_spellings(field_models(field_info), rest) if rest else []
        if below is not None:
            return [(name, wire_name(model, name)), *below]
    return None


def _set_provider(
    patch: dict[str, object],
    data: VcoModel,
    stored_data: object,
    stored_parts: Mapping[Path, ItemPart],
    owner: Path,
    value: object,
) -> bool:
    """Set the ``RefOnly`` field at *owner* to *value* in *patch*, in the segment as the stored list names it; False when the patch has no place for it.

    *owner* is ``segments[<segment>].<fields>`` in python names, and a
    ``RefOnly`` field is not in a raw dump, so the path is built from its parts.
    Each field is written by its wire name, unless the patch already has the
    object under its python name: ``merge_patch`` refuses both for one field.
    The segment's object is the one a data entry of the patch has, if any.
    """
    segment, *names = owner.parts[1:]
    if isinstance(segment, str) or not all(isinstance(name, str) for name in names):
        return False
    models = field_models(type(data).model_fields["segments"])
    fields = _field_spellings(models, cast("list[str]", names))
    part = _stored_segment(data, stored_data, stored_parts, segment)
    if fields is None or part is None:
        return False
    node = _child(patch, ("segments",))
    for spellings in ((selector_of(part),), *fields[:-1]):
        if node is None or node.get("_op") == "delete":
            return False
        node = _child(node, spellings)
    if node is None:
        return False
    node[_spelled(node, fields[-1])] = value
    return True


def _spelled(node: dict[str, object], spellings: tuple[str, ...]) -> str:
    """The key *node* already has for a field, by one of its *spellings*; else the last."""
    return next((key for key in spellings if key in node), spellings[-1])


def _child(
    node: dict[str, object], spellings: tuple[str, ...]
) -> dict[str, object] | None:
    """The object of *node* under a field's key (``_spelled``), made if there is none; None where the key holds something else."""
    found = node.setdefault(_spelled(node, spellings), {})
    return cast("dict[str, object]", found) if isinstance(found, dict) else None


class ConfigConflictError(ModuleWriteError):
    """Raised when a module write is rejected because its version is stale.

    The module changed on the VCO after it was read, and the write's changes
    are not all stored. ``stated`` says how the refusal is known. True: the
    VCO said it refused the version (``detail`` says how). False: the VCO
    answered with the module, which holds none of the changes and is as read
    at each, and the refusal is inferred from that; or ``sent`` is False.
    ``sent`` is False when nothing was sent: the module's version on the VCO
    was not the one read, and the module there did not hold the write's
    changes (a write that sends refs reads the version first). ``lost_edits``
    are the edits to redo, and may be empty after a stated refusal when the
    check cannot tell what is stored; ``module``, as the VCO holds it, is
    what to redo them on. ``write_module`` always sets it, except when the
    module is gone from its configuration: ``module`` is then None and
    ``detail`` says to read the configuration again.
    """

    detail: str
    stated: bool
    sent: bool

    def __init__(
        self,
        module_id: int,
        version: str,
        detail: str,
        *,
        stated: bool = True,
        sent: bool = True,
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
        self.sent = sent
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
        if not self.sent:
            # ``detail`` says that nothing was sent, and what to do.
            return (
                f"Configuration module {self._label()} changed on the VCO since "
                f"it was read (version {self.version}): {self.detail}"
            )
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
    def _notes(self, limit: int | None) -> list[str]:
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


class RefsDroppedError(WriteNotAppliedError):
    """Raised when the VCO stored a module write but not all of its ref rows.

    Every lost edit is a ref row. After an update answered with the module, a
    change is stored beside them (with none, the write is a conflict); an
    insert, or a write the VCO only counted, raises this with none stored too.
    The VCO deletes the module's refs and inserts the rows sent one by one, and it
    skips on its own a row whose object it cannot resolve while it stores the
    others. A row the plan added is not stored, or one it removed still is
    (``lost_edits``; each has ``part == "refs"``, and ``owner`` is the path of
    the ``RefOnly`` field the row goes with). Nothing was written to put the
    rows back.

    When the VCO took none of the rows sent, ``every_row_lost`` is true and
    ``refs_as_read`` holds the refs as the module was read, to put back;
    ``refs_as_read`` is the module's ``raw_refs`` (``MISSING`` when it had
    none).
    """

    refs_as_read: object

    def __init__(
        self,
        module_id: int,
        version: str,
        *,
        module_name: str,
        plan: ModulePlan,
        module: ConfigurationModuleBase,
        lost_edits: Sequence[PlannedChange],
        refs_as_read: object,
        applied: Sequence[PlannedChange] = (),
        left_out: Sequence[PlannedChange] = (),
        server_changes: Sequence[ServerChange] = (),
        stored_parts: Mapping[Path, ItemPart] | None = None,
    ) -> None:
        self.refs_as_read = refs_as_read
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

    @property
    def every_row_lost(self) -> bool:
        """Whether the VCO took none of the rows sent, and some were sent as read: each of those is a lost edit, and ``refs_as_read`` holds them to put back."""
        return any(
            edit.as_read is not MISSING and edit.intended is not MISSING
            for edit in self.lost_edits
        )

    @override
    def _headline(self) -> str:
        lost = len(self.lost_edits)
        return (
            f"Configuration module {self._label()}: {lost} of "
            f"{lost + len(self.applied)} planned changes are not stored as sent, "
            "and all of them are ref rows"
        )

    @override
    def _notes(self, limit: int | None) -> list[str]:
        if self.every_row_lost:
            lines = [
                (
                    "The VCO took none of the ref rows this write sent, and the "
                    "data is stored. error.refs_as_read holds the refs as they "
                    "were read: to put them back, send "
                    "configuration/updateConfigurationModule with "
                    f'{{"id": {self.module_id}, "_update": {{"refs": '
                    "<error.refs_as_read>}} (update_configuration_module)."
                )
            ]
        else:
            lines = []
            if any(edit.intended is not MISSING for edit in self.lost_edits):
                lines.append(
                    "The VCO skips a ref row whose object it cannot resolve and "
                    "stores the others. Check that the object of each row "
                    "added exists on the VCO, then make the change again on "
                    "error.module."
                )
            if any(edit.intended is MISSING for edit in self.lost_edits):
                lines.append(
                    "The VCO kept a ref row the plan removed. Clear the field it "
                    "goes with again on error.module."
                )
        owners = [edit for edit in self.lost_edits if edit.owner is not None]
        if owners:
            lines.append(
                "  error.redo_patch() builds these patches with the service "
                "filled in, for each field whose segment error.module has:"
            )
        for edit in _first(owners, limit):
            owner = cast(Path, edit.owner)
            if edit.intended is MISSING:
                lines.append(
                    f"  to clear {owner} again, send the patch "
                    f"{patch_text(owner, clear=True)} (error.module reads it as set)"
                )
            else:
                lines.append(
                    f"  to set {owner} again, send the patch {patch_text(owner)} "
                    "(error.module reads it as unset)"
                )
        lines.extend(_and_more(len(owners), limit, "lost_edits"))
        return lines


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
    def _notes(self, limit: int | None) -> list[str]:
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
            for change in _first(self.plan.changes, limit):
                before, after = shown_together(
                    change.before,
                    change.after,
                    model=self.plan.model,
                    path=change.path,
                )
                where = f"{change.path}{_place_or_order(change.place, change.moves)}"
                lines.append(f"  {where}: read {before}, meant {after}")
            lines.extend(_and_more(len(self.plan.changes), limit, "plan.changes"))
            lines.extend(self.plan.ref_lines())
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
    """Raised by an edit through an object that a write replaced.

    ``update_module`` raises it too, for a module an earlier write replaced.

    After a save the state holds the module as the VCO stored it; after
    ``update_module`` the module it returned (or ``error.module``, after a
    write error) is that module. The object raising belongs to the module
    that was there before. Take the object again from the module as stored.
    ``paths`` are where in the module such edits were found, when they were
    found by comparison; they may come from several modules, which the message
    names, a line each. An assignment or ``merge_patch`` that raises names the
    object and leaves them empty, and so does the refusal of ``update_module``
    to write a module an earlier ``update_module`` replaced. Only
    ``update_module`` marks the module it is given; ``write_module`` does not.
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
