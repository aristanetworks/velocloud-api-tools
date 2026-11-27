# ModuleWriteError names ModulePlan, ConfigurationModuleBase and ModuleWrite in
# annotations only; their modules import this one.
# pyright: reportImportCycles=false
"""The errors pyvelo raises, each a ``VcoError``.

First the client's: an HTTP status, a JSON-RPC error, an empty result, the rate
limit, the WebSocket and the credentials. Then the refusals of a plan before it
writes (``LossyFieldError``, ``HubListError``, ``DanglingReferenceError`` and
others), the module write errors (``ModuleWriteError`` and its subclasses) and
the layer errors.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Final, Literal, cast, override

from pydantic import MISSING

from pyvelo.client.enums.configuration import python_module_name
from pyvelo.core.base import VcoModel, field_models, is_list, wire_name
from pyvelo.core.changes import patch_of
from pyvelo.core.names import named_path, named_value
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
from pyvelo.core.references import Reference, reference_text
from pyvelo.core.secrets import HIDDEN
from pyvelo.core.show import moves_text, patch_text, shown_together

if TYPE_CHECKING:
    from pyvelo.client.module_check import ModuleWrite
    from pyvelo.client.module_write import ModulePlan
    from pyvelo.client.schemas.configuration import ConfigurationModuleBase
    from pyvelo.core.mending import MendingKind, MendingPatch
    from pyvelo.core.response_check import PlannedChange, ServerChange


def _rebuild_error(cls: type[VcoError], args: tuple[Any, ...]) -> VcoError:
    """Build an error of *cls* without calling its constructor.

    Its attributes arrive as state.
    """
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
            attributes["__notes__"] = list(cast("list[str]", attributes["__notes__"]))
        return (_rebuild_error, (type(self), self.args), attributes)


class VcoClientNotInitializedError(VcoError):
    """Raised when the client is used without a connection."""

    def __init__(self) -> None:
        """The error, with a message that says to use the client in ``async with``."""
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
        """The JSON-RPC error's *code*, *message* and *data*.

        The message lists the VCO's validation errors and warnings in *data* a line
        each, and shows any other *data* as it is.
        """
        self.code: int = code
        self.message: str = message
        self.data: Any = data
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
        """The error for an empty result of *method*, which the message names."""
        self.method: str | None = method
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
        """The failure of *operation* with the VCO's *error* text.

        *code* and *data* are the JSON-RPC error's, when the failure came as one; the
        message shows *data* as ``VcoApiError`` does.
        """
        self.operation: str = operation
        self.error: str = error
        self.code: int | None = code
        self.data: Any = data
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
        """The error for HTTP *status_code*, with *body* the response's text."""
        self.status_code: int = status_code
        self.text: str = body
        self.body: str = body[:500]
        super().__init__(f"HTTP {status_code}: {self.body}")


class VcoRateLimitError(VcoError):
    """Raised when 429 retries are exhausted."""

    def __init__(self, attempts: int) -> None:
        """The error after *attempts* requests that were each answered 429."""
        self.attempts: int = attempts
        super().__init__(f"Rate limited: retries exhausted after {attempts} attempts")


class VcoWebSocketError(VcoError):
    """Raised when a WebSocket operation fails (connection, send, or receive)."""

    def __init__(self, message: str) -> None:
        """The error, with *message* kept as ``message`` too."""
        self.message: str = message
        super().__init__(message)


class VcoWebSocketConnectionError(VcoWebSocketError):
    """Raised when the WebSocket connection cannot be established.

    Covers handshake failures, upgrade errors, and malformed noop messages.
    """


class VcoWebSocketTimeoutError(VcoWebSocketError):
    """Raised when a WebSocket request or diagnostic times out."""

    def __init__(self, message: str, *, timeout: float) -> None:
        """The error, with the *timeout* that ran out, in seconds."""
        self.timeout: float = timeout
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
        """The error, with a fixed message: every probe failed."""
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
    ``module`` is the module's python name. ``paths`` locates each such edit,
    as its raiser gives it (a plan gives each as pyvelo shows a path: fields
    by attribute name, items by what names them in the data as edited);
    ``fields`` is the same, as text. ``override_flags`` are those paths that
    are override flags: an edit to a sibling sets the flag, but the document
    as read has no key for it and it already reads as on, so there is nothing
    to write.
    """

    def __init__(
        self,
        module: str,
        paths: Sequence[Path],
        override_flags: Sequence[Path] = (),
    ) -> None:
        """The error for the edits to *module* at *paths*, *override_flags* among them.

        *module* is the module's name, the VCO's or the python one. The
        message says why a save writes none of them and what to do instead.
        """
        self.module: str = python_module_name(module)
        self.paths: list[Path] = list(paths)
        self.fields: list[str] = [str(path) for path in self.paths]
        self.override_flags: list[Path] = list(override_flags)
        message = (
            f"{self.module}: a save cannot write these edits: "
            f"{', '.join(self.fields)}. "
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


class FixedListError(VcoError):
    """Raised when a payload changes a fixed part of a ``FixedList``.

    The QoS ``defaults`` are such a part.

    The ``defaults`` rules of a segment have fixed entries, keys and order, and
    only their ``action`` can change. A plan compares the payload with the
    module as read, so the edit is refused however it was made (an attribute
    assignment, a plain-dict data, a patch). ``module_name`` is the module's
    python name; ``paths`` are the places that changed, as the raiser gives
    them (a plan gives each as pyvelo shows a path), each at the rule or at
    the list (the reason of each is in the message).
    """

    module_name: str
    paths: tuple[Path, ...]

    def __init__(
        self, module_name: str, violations: Sequence[tuple[Path, str]]
    ) -> None:
        """The error for *module_name*: *violations* are each changed place and why.

        *module_name* is the module's name, the VCO's or the python one.
        """
        self.module_name = python_module_name(module_name)
        self.paths = tuple(path for path, _ in violations)
        found = "; ".join(f"{path}: {reason}" for path, reason in violations)
        super().__init__(
            f"{self.module_name}: the edit changes a fixed part of a list whose "
            f"entries are fixed: {found}. Only the actions of the `defaults` "
            "rules can change: send "
            '{"segments": {"<segment>": {"defaults": {"<rule>": {"action": {...}}}}}} '
            "and leave the rules' other fields, their order and their set as "
            "they are. A missing entry is repaired when the module is written, "
            "not built by an edit."
        )


class SegmentSetError(VcoError):
    """Raised when one module's plan would change the set of segments of a profile.

    A profile's segment is in all three modules (device settings, QoS and
    firewall), so one module cannot add or remove one alone. The pipeline
    changes the three together. ``module_name`` is the module planned, or
    None when the error is about several; ``segments`` are the names of the
    segments added or removed; ``modules`` are the modules the rule found
    wanting. A module is named by its python name, here and in the message,
    which says what happened and how to do it instead.
    """

    module_name: str | None
    segments: tuple[str, ...]
    modules: tuple[str, ...]

    def __init__(
        self,
        message: str,
        *,
        module_name: str | None = None,
        segments: Sequence[str] = (),
        modules: Sequence[str] = (),
    ) -> None:
        """The error with *message* and the module, segments and modules it is about.

        A module is given by its name, the VCO's or the python one.
        """
        self.module_name = (
            None if module_name is None else python_module_name(module_name)
        )
        self.segments = tuple(segments)
        self.modules = tuple(python_module_name(module) for module in modules)
        super().__init__(message)


class HubListError(VcoError):
    """Raised when one module's plan would change a segment's hub lists.

    The plan would add a hub to them, or remove one.

    A hub in the ``vpn`` lists of a profile's segment (the hub list, the
    bridge list and the backhaul list) needs its role set on the hub's
    service, and a hub taken out needs it cleared. The pipeline does that as a
    step of the save; one module write cannot, so the VCO would list a hub no
    edge can use, or lose one's roles. ``segment`` is the segment's name (None
    when the error is about several), ``lists`` the paths of the lists whose
    members differ from the module as read, from the segment, in attribute
    names (``vpn.backhaul_edges``), as a patch names them, ``hubs`` the
    names of the hubs added or removed, and ``edges`` the
    Edges (their IDs and names) that the change would leave with a hub they
    cannot use. The message says what happened and how to do it instead.
    """

    segment: str | None
    lists: tuple[str, ...]
    hubs: tuple[str, ...]
    edges: tuple[tuple[int, str], ...]

    def __init__(
        self,
        message: str,
        *,
        segment: str | None = None,
        lists: Sequence[str] = (),
        hubs: Sequence[str] = (),
        edges: Sequence[tuple[int, str]] = (),
    ) -> None:
        """The error with *message* and the segment, lists, hubs and Edges it names."""
        self.segment = segment
        self.lists = tuple(lists)
        self.hubs = tuple(hubs)
        self.edges = tuple(edges)
        super().__init__(message)


def _sends(mending: Sequence[MendingPatch], kind: MendingKind) -> str:
    """The patches of *mending* of *kind*, each as ``send <json> to <module>``."""
    return "; ".join(
        f"send {json.dumps(m.patch)} to {m.module}" for m in mending if m.kind == kind
    )


def _mending_text(mending: Sequence[MendingPatch], *, on_edge: bool) -> str:
    """The sentences of a ``DanglingReferenceError`` that give its *mending* patches.

    A kind with no patch has no sentence. On an Edge, the hub lists are its
    profile's, so the ``add_member`` patches go to the profile.
    """
    text = (
        " The patches below mend the hub references, and `mending` holds them as "
        "data: send those of one kind."
    )
    removals = _sends(mending, "remove_reference")
    if removals:
        text += f" To take each hub out of the rule that names it: {removals}."
    members = _sends(mending, "add_member")
    if members:
        text += f" To make each hub a backhaul hub of its segment: {members}."
        if on_edge:
            text += (
                " This is an Edge, and the hub lists are its profile's: send that "
                "patch to the profile's device settings (load the profile, apply "
                "it there and save the profile), not to the Edge's; then load the "
                "Edge again and redo its edit, since this Edge state holds the "
                "profile as it was loaded."
            )
    return text + " Or take back every edit to a module with `revert`."


def _only_hubs_text(
    only_hubs: Sequence[Reference], mending: Sequence[MendingPatch]
) -> str:
    """The sentence of a ``DanglingReferenceError`` for its *only_hubs*."""
    again = (
        "with the patch above"
        if any(m.kind == "add_member" for m in mending)
        else "(one of the segment's `vpn.backhaul_edges`)"
    )
    return (
        " No patch takes a hub out of the rule that names it: for "
        f"{len(only_hubs)} of these references (`only_hubs` holds them) the hub "
        "is the only hub of a rule that backhauls through hubs, or one of all "
        "the hubs it names, and the rule would backhaul nowhere. Make the hub a "
        f"backhaul hub again {again}; or first give the rule another hub or "
        "change its `route_policy`, and the next `plan()` offers the patch that "
        "takes the hub out of the rule; or delete the rule."
    )


class DanglingReferenceError(VcoError):
    """Raised when a save would leave a value that names nothing the configuration has.

    A field that refers to a segment (an interface, a LAN network, a loopback)
    or to a hub (a QoS rule's backhaul entry) holds a value that no item of
    the set it refers to has. The VCO would store it without complaint, and
    what it stands for would not work: an interface in a segment that does
    not exist, a rule that backhauls nowhere. ``references`` are the
    references found, each with its module (by its python name), its path (as
    pyvelo shows it: fields by attribute name, items by what names them), its
    value and the name of its segment. A reference the configuration already
    held as it was read is not one of them.
    ``on_edge`` is whether the target is an Edge: the hub lists are its
    profile's, and the message says so.

    ``mending`` holds the patches that mend the references to a backhaul hub,
    as data: for each, a ``remove_reference`` patch (the rule stops using the
    hub) and an ``add_member`` patch (the hub becomes a backhaul hub of the
    segment, in the profile's device settings). Each names its module by its
    python name and each field of its patch by its attribute, and applies as
    it is given. All ``remove_reference`` patches come first, then the
    ``add_member`` ones. The patches of one kind together mend every hub
    reference of the error; a kind that cannot be built for every one is left
    out whole. A reference to a segment gets no patch.

    ``only_hubs`` are the hub references whose rule, one that backhauls through
    hubs (``route_policy`` ``"backhaul"``, ``route_cfg.type_`` ``"edge"``), would
    be left with no hub if each were taken out of it: each is the only hub its
    rule names, or one of all the hubs it names. They are among ``references``,
    the same objects. No ``remove_reference`` patch
    is offered then (for any reference), and the message says to make the hub
    a backhaul hub again; or first to give the rule another hub or change its
    ``route_policy``, after which the next ``plan()`` offers the removal patch;
    or to delete the rule.
    """

    references: tuple[Reference, ...]
    on_edge: bool
    mending: tuple[MendingPatch, ...]
    only_hubs: tuple[Reference, ...]

    def __init__(
        self,
        references: Sequence[Reference],
        *,
        on_edge: bool = False,
        mending: Sequence[MendingPatch] = (),
        only_hubs: Sequence[Reference] = (),
    ) -> None:
        """The error for *references*, with a message that lists each and what to do.

        The message says where a segment or a backhaul hub comes from and gives
        each patch of *mending*, with the module it goes to, and what to do for
        the references of *only_hubs*.
        """
        self.references = tuple(references)
        self.on_edge = on_edge
        self.mending = tuple(mending)
        self.only_hubs = tuple(only_hubs)
        lines = "\n".join(f"  {reference_text(r)}" for r in self.references)
        names = {r.set_name for r in self.references}
        sources: list[str] = []
        if "segment" in names:
            sources.append(
                "a segment is one of the device settings `segments` of the profile"
            )
        if "backhaul_hub" in names:
            sources.append(
                "a backhaul hub is one of the segment's `vpn.backhaul_edges` in the "
                "profile's device settings"
            )
        nowhere = (
            " (for a hub, a rule that backhauls nowhere)"
            if "backhaul_hub" in names
            else ""
        )
        message = (
            f"{len(self.references)} reference(s) name nothing:\n{lines}\n"
            "The save is refused because the VCO would store a value that names "
            f"nothing{nowhere}. Give a value that exists, or remove the reference."
        )
        if sources:
            message += f" Where each comes from: {'; '.join(sources)}."
        if self.mending:
            message += _mending_text(self.mending, on_edge=on_edge)
        if self.only_hubs:
            message += _only_hubs_text(self.only_hubs, self.mending)
        super().__init__(message)


@dataclass(frozen=True)
class HublessBackhaul:
    """A route action of a QoS rule that backhauls to hubs and names none.

    One place a ``BackhaulWithoutHubError`` names.
    """

    module: str
    """The module the rule is in.

    A place an error carries has the module's python name
    (``business_policy``).
    """
    path: Path
    """The route action's path, each field by its attribute name.

    For example, ``segments[Guest].rules[Web].action.edge_to_cloud_route_action``.
    A place an error carries names the segment and the rule as pyvelo shows
    them (``named_path``).
    """
    segment_name: str | None
    """The name of the segment the rule is in, when the segment has one."""
    rule_name: str
    keys: tuple[str, ...] | None = field(default=None, compare=False)
    """The keys a patch to the module gives to reach the route action.

    Each field by its attribute name and each item by the selector that names
    it alone. ``None`` when an item on the way has no such selector.
    """
    deletable: bool = field(default=True, compare=False)
    """Whether a patch can delete the rule.

    False for one of a segment's default rules, a fixed list.
    """


def _nested(keys: Sequence[str], leaf: object) -> dict[str, object]:
    """*leaf* under *keys*, the first key outermost."""
    value = leaf
    for key in reversed(keys):
        value = {key: value}
    return cast("dict[str, object]", value)


def _backhaul_ways(places: Sequence[HublessBackhaul]) -> str:
    """The sentences of a ``BackhaulWithoutHubError`` that say how to mend a rule.

    The patches are those of the first place that has keys, each field by its
    attribute name; the others are mended the same way.
    """
    first = next((p for p in places if p.keys is not None), None)
    if first is None or first.keys is None:
        text = (
            " To mend a rule, give it a hub (a backhaul hub of its segment), set "
            'the route action\'s `route_policy` to "auto", "direct" or "gateway", '
            "or delete the rule (a rule that is not one of the segment's default "
            "rules)."
        )
    else:
        keys = first.keys
        added = {"_op": "add", "edge_name": "<hub name>"}
        hub = _nested(keys, {"route_cfg": {"backhaul_edges": {"+": added}}})
        policy = _nested(keys, {"route_policy": "<auto, direct or gateway>"})
        text = (
            f" To mend the rule, send one of these patches to {first.module}: to "
            "give the rule a hub (a backhaul hub of its segment: one of the "
            "segment's `vpn.backhaul_edges` in the profile's device settings), "
            f"{json.dumps(hub)}; to route it another way, {json.dumps(policy)}"
        )
        if first.deletable:
            rule = _nested(keys[:-2], {"_op": "delete"})
            text += f"; to delete the rule, {json.dumps(rule)}."
        else:
            text += (
                ". The rule is one of the segment's default rules, which cannot "
                "be deleted."
            )
    if len(places) > 1:
        text += _others_text(places, first)
    return text + " Or take back every edit to a module with `revert`."


def _others_text(
    places: Sequence[HublessBackhaul], first: HublessBackhaul | None
) -> str:
    """The sentence that says how the places after *first* are mended.

    A default rule cannot be deleted, so the delete patch shown for *first*
    is not offered for one; and when *first* is a default rule, a rule that
    is not can be deleted as well.
    """
    others = [p for p in places if p is not first]
    if first is None:
        return " Each rule is mended in one of these ways."
    if first.deletable:
        fixed = [p for p in others if not p.deletable]
        if not fixed:
            return " The other rules are mended the same way."
        # A rule with two route actions left with no hub is named once.
        names = ", ".join(dict.fromkeys(_rule_text(p) for p in fixed))
        return (
            " The other rules are mended the same way, but these are default "
            f"rules of their segment, which cannot be deleted: {names}; give "
            "each a hub or route it another way."
        )
    text = " The other rules are mended the same way"
    if any(p.deletable for p in others):
        text += (
            ", and one that is not a default rule can be deleted too, with "
            '`{"_op": "delete"}` at the rule'
        )
    return text + "."


def _rule_text(place: HublessBackhaul) -> str:
    """``rule 'Web' of segment 'Guest'``, or without the segment when it has no name."""
    text = f"rule {place.rule_name!r}"
    if place.segment_name is not None:
        text += f" of segment {place.segment_name!r}"
    return text


class BackhaulWithoutHubError(VcoError):
    """Raised when a save would leave a QoS rule that backhauls to hubs and names none.

    A route action of a rule (its Edge to Edge, Edge to data center or Edge to
    cloud action) whose ``route_policy`` is ``"backhaul"`` and whose
    ``route_cfg.type_`` is ``"edge"`` sends its traffic to the hubs of its
    ``route_cfg.backhaul_edges``. With none, the VCO would store a rule that
    backhauls nowhere. ``places`` are the route actions found, each with its
    module (by its python name), its path (as pyvelo shows it), its segment
    and its rule. A route action that was so as the module
    was read is not one of them (``ConfigState.plan()`` warns of it instead).
    """

    places: tuple[HublessBackhaul, ...]

    def __init__(self, places: Sequence[HublessBackhaul]) -> None:
        """The error for *places*, with a message that lists each and what to do.

        The message gives the patches that mend the first, in attribute
        names, with the module they go to.
        """
        self.places = tuple(places)
        lines = "\n".join(
            f"  {p.module} {p.path}: {_rule_text(p)}" for p in self.places
        )
        message = (
            f"{len(self.places)} backhaul rule(s) name no hub:\n{lines}\n"
            "Each sends its traffic by backhaul and names no hub to send it to: "
            'its `route_policy` is "backhaul", its `route_cfg.type_` is "edge" and '
            "its `route_cfg.backhaul_edges` is empty. The save is refused because "
            "the VCO would store a rule that backhauls nowhere."
        )
        super().__init__(message + _backhaul_ways(self.places))


class RefsNotLoadedError(VcoError):
    """Raised when a plan needs the refs of a module that was read without them.

    A module from ``configuration/getConfigurationModules`` (or an insert's
    answer) carries no refs. An edit to an owner of a ref row then cannot be
    written, because the row that goes with it is not known, and a write of
    a device settings or QoS module with segments would let the VCO replace its
    refs. ``module_name`` is the module's python name; ``paths`` are the
    owners the edit changed (empty when the write alone is refused), as the
    raiser gives them (a plan gives each as pyvelo shows a path).
    """

    module_name: str
    paths: tuple[Path, ...]

    def __init__(self, module_name: str, paths: Sequence[Path] = ()) -> None:
        """The error for *module_name*, with the owners at *paths* the edit changed.

        *module_name* is the module's name, the VCO's or the python one. The
        message says how to read the module with its refs.
        """
        self.module_name = python_module_name(module_name)
        self.paths = tuple(paths)
        read = (
            f"{self.module_name} was read without its refs (it came from "
            "configuration/getConfigurationModules)"
        )
        how = (
            "Read the module with its refs and edit that one: send "
            '`configuration/getConfiguration` with {"id": <configuration ID>, '
            '"with": ["modules"]} (get_configuration), or '
            '`edge/getEdgeConfigurationStack` for an edge with {"edgeId": <edge ID>} '
            '(and "enterpriseId": <enterprise ID> beside it when you are an operator) '
            "(get_edge_configuration_stack)."
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


class VersionRequiredError(VcoError):
    """Raised for an update of a module that has no version.

    It is raised when nothing asked for an unchecked write (``unconditional``).
    ``module_name`` is the module's python name.
    """

    module_name: str

    def __init__(self, module_name: str) -> None:
        """The error for *module_name*.

        *module_name* is the module's name, the VCO's or the python one. The
        message says how to read the module with a version, and how to ask
        for the unchecked write.
        """
        self.module_name = python_module_name(module_name)
        super().__init__(
            f"{self.module_name}: this module has no version, so the VCO cannot "
            "check "
            "that it still holds what was read: an update would overwrite whatever "
            "another writer stored since, and that change would be lost without a "
            "word. Nothing was sent. Every module the VCO stores has a version, so "
            "read the module again (load the state again) and redo the edit. To "
            "send the update with no check, pass unconditional=True to the save (a "
            'JSON caller sends "unconditional": true with it); with write_module, '
            "send dataclasses.replace(plan, unconditional=True). update_module has "
            "no such option: plan with plan_module and write with write_module"
        )


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
    """The line that counts the entries of a list a message cut short.

    There is no line when the message did not cut it short.
    """
    if limit is None or total <= limit:
        return []
    return [f"  and {total - limit} more: error.{attribute} has them all"]


def _where(change: PlannedChange | ServerChange) -> str:
    """The path of *change*, with ``refs`` before it for a refs entry.

    The keys of a refs entry are data: its path is written as the entry has
    it. *change* is as a write error shows it (``ModuleWriteError._shown``), so
    the path of a change of the data is in the names of the plan's text.
    """
    return f"refs {change.path}" if change.part == "refs" else str(change.path)


def _what(change: PlannedChange) -> str:
    """The path of *change*, then its place or its order."""
    return _where(change) + _place_or_order(change.place, change.moves)


def _written_whole(applied: Sequence[PlannedChange]) -> frozenset[Path]:
    """The paths of *applied* at which the write stored a value whole.

    Below such a path the check compares what is stored with what was sent
    key by key, and item by item at one position in both, so a server change
    there names an item by its position in the data as planned.
    """
    return frozenset(
        change.path
        for change in applied
        if change.part == "data"
        and change.moves is None
        and change.intended is not MISSING
    )


def _dropped(server_changes: Sequence[ServerChange]) -> frozenset[tuple[Path, str]]:
    """The items that were sent and the stored module lacks, of *server_changes*.

    Each as the path of its list and the item's name there, as text
    (``item_text``): the name an order entry of that list has for the item.
    """
    return frozenset(
        (Path(change.path.parts[:-1]), item_text(change.path.parts[-1]))
        for change in server_changes
        if change.part == "data"
        and change.stored is MISSING
        and change.path.parts
        and not isinstance(change.path.parts[-1], str)
    )


class ModuleWriteError(VcoError):
    """A module write that is not known to be stored as planned.

    It was sent, or was about to be sent again.

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

    The lists hold each change as the check made it: its path names a field
    by the key the VCO uses and an item by its key, and its values are as
    stored. The text shows each as the plan's text does (``ModulePlan.render``):
    the module by its python name, a field by its attribute, an item by what
    names it, and an object with its keys by attribute name.
    """

    module_id: int
    module_name: str
    """The module's python name (``business_policy`` for the module the VCO calls
    ``QOS``), whichever of the two names the error was made with; ``""`` for
    none. ``apply`` and ``revert`` take it as ``module``. The VCO's name is
    ``plan.name``, on an error that has its plan."""
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
    """Planned changes the VCO stores otherwise than as sent
    (``ResponseCheck.left_out``)."""
    server_changes: tuple[ServerChange, ...]
    completed: tuple[ModuleWrite[Any], ...]
    """The earlier writes of the same save, which are stored. Set by the save."""
    stored_parts: dict[Path, ItemPart]
    """For each item of every keyed list the check walks that the module as stored holds
    too, by its path, the part that names it there (``ResponseCheck.stored_parts``)."""

    def __init__(  # noqa: PLR0913 (an error's constructor: each argument is a field the error reports)
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
        """The write of module *module_id* at *version*, with what its check found.

        ``completed`` starts empty; the save sets it.
        """
        self.module_id = module_id
        self.module_name = python_module_name(module_name)
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
        """Lines between the headline and the lists.

        A list in them shows at most *limit* entries.
        """
        del limit
        return []

    def _document(self) -> type[VcoModel] | None:
        """The class of the module's data, which says what a value of the plan is."""
        return None if self.plan is None else self.plan.model

    def _values(
        self, change: PlannedChange | ServerChange, *values: object
    ) -> tuple[str, ...]:
        """*values* of *change* as text, with a secret as ``"***"``.

        *change* is as the check made it, with its path in the VCO's keys:
        the secrets are found by it. *values* are as a line shows them
        (``_shown``, ``_shown_server``): an object has its keys by attribute
        name, and a secret is found under either name.

        A ref row is shown as ``"***"`` whatever it holds: rows are compared by
        identity only, and a row can hold a service's credentials.
        """
        if change.part == "refs":
            return shown_together(
                *(value if value is MISSING else HIDDEN for value in values)
            )
        return shown_together(*values, model=self._document(), path=change.path)

    def _shown(self, change: PlannedChange) -> PlannedChange:
        """*change*, one of this error's planned changes, as its line shows it.

        The path, the item a place is after, the items of the moves and the
        item names an order entry holds are shown as the plan's text shows
        them (``ModulePlan.shown_change``): a field by its attribute, an item
        by what names it in the data as planned, and an item the plan removes
        in the data as read. So a lost edit has the path of the plan's line
        for that change, and the path is one ``apply`` takes. An object among
        the values has its keys by attribute name.

        A refs entry is returned as it is: its keys are data. So is every
        change of an error that has no plan, which has nothing to name an
        item by.
        """
        plan = self.plan
        if plan is None or change.part == "refs":
            return change
        seen = plan.shown_change(
            RawChange(
                change.path,
                change.as_read,
                change.intended,
                place=change.place,
                moves=change.moves,
            )
        )
        stored = (
            plan.shown_names(change.path, change.stored)
            if change.moves is not None
            else named_value(change.stored, plan.model, change.path)
        )
        return replace(
            change,
            path=seen.path,
            as_read=seen.before,
            intended=seen.after,
            stored=stored,
            place=seen.place,
            moves=seen.moves,
        )

    def _shown_server(
        self,
        change: ServerChange,
        whole: frozenset[Path],
        dropped: frozenset[tuple[Path, str]],
    ) -> ServerChange:
        """*change*, one of ``server_changes``, as its line shows it.

        *whole* is ``_written_whole`` of the changes this error has as applied,
        and *dropped* is ``_dropped`` of its server changes.

        Its path names an item in the document that holds it:

        - An item the plan's data has is shown as the plan's text shows it
          (``ModulePlan.named``): the check names it as the plan's changes
          do.
        - Below a value the write stored whole (an added item, a list that
          was replaced), an item is at one position in the data as planned
          and as stored, and the check names it by that position. It is kept,
          also where the item has a name: the position counts in the payload,
          which is not always the list the plan's data holds (a save adds a
          segment's missing defaults to it), so the item is not looked up
          there. The fields are named by their classes.
        - An item only the stored module has is named by its key there, or
          by its position there, and is kept as written: the plan's data
          has no such item, and its position is another item's in it.

        An order (a keyed list the VCO stores in another order than sent)
        holds the items' names as sent and as stored. Those sent are shown as
        the plan's text shows them, and a stored name is shown as the sent
        item of that name is. A stored name no sent item has is an item only
        the stored module has, and is kept as written, as its own line keeps
        it; so is one whose sent item the stored module lacks (*dropped*): a
        position there may be written as that item's was. A text that is no
        item's name is kept too. An object among the values has its keys by
        attribute name.

        A refs entry is returned as it is, and so is every change of an
        error that has no plan.
        """
        plan = self.plan
        if plan is None or change.part == "refs":
            return change
        path = self._server_path(plan, change, whole)
        values = [change.as_read, change.stored]
        # An order's two values are the items' names, as text; no other
        # change of a keyed list has two lists of texts.
        if all(
            is_list(value) and all(isinstance(name, str) for name in value)
            for value in values
        ):
            sent = cast("list[str]", change.as_read)
            shown_sent = plan.shown_names(change.path, sent)
            if is_list(shown_sent) and len(shown_sent) == len(sent):
                names = dict(zip(sent, shown_sent, strict=True))
                values = [
                    shown_sent,
                    [
                        name
                        if (change.path, name) in dropped
                        else names.get(name, name)
                        for name in cast("list[str]", change.stored)
                    ],
                ]
        as_read, stored = (
            named_value(value, plan.model, change.path) for value in values
        )
        return replace(change, path=path, as_read=as_read, stored=stored)

    @staticmethod
    def _server_path(
        plan: ModulePlan, change: ServerChange, whole: frozenset[Path]
    ) -> Path:
        """The path of *change*, a server change of the data, as it is shown.

        The three cases of ``_shown_server``: below a path of *whole*, then
        an item only the stored module has (the check gives it nothing as
        read, and its path ends at the item), then any other path.
        """
        parts = change.path.parts
        for length in range(1, len(parts)):
            if Path(parts[:length]) in whole:
                by_class = named_path(change.path, plan.model)
                head = plan.named(Path(parts[:length]))
                return Path((*head.parts, *by_class.parts[length:]))
        if parts and change.as_read is MISSING and not isinstance(parts[-1], str):
            return plan.named(Path(parts[:-1])).child(parts[-1])
        return plan.named(change.path)

    def render(self, limit: int | None = 20) -> str:
        """The message: the headline, any notes, then each list by path.

        A list with no entries is left out.

        Each list shows its first *limit* entries (all for None), and a line
        after it counts the rest. A secret is shown as ``"***"``.

        The module is named by its python name. A path is in the names of
        the plan's text: a lost edit and a stored one have the path of the
        plan's line for that change, which ``apply`` takes as ``path``, and
        an object is written with its keys by attribute name. In ``stored
        otherwise than sent`` an item only the stored module has keeps the
        key, or the position, it has there, in its own line and in the order
        of its list; an item below a value the write added or replaced whole
        is shown by its position in that value; and a key the model does not
        declare is kept as stored. A refs entry is ``refs <path>``, its keys
        as stored. An error that has no plan keeps every path as the check
        wrote it.
        """
        lines = [self._headline(), *self._notes(limit)]
        if self.lost_edits:
            lines.append(
                "lost edits (not stored; `error.redo_patch()` redoes those it can, "
                "`error.not_redone()` lists the rest):"
            )
            for change in _first(self.lost_edits, limit):
                seen = self._shown(change)
                as_read, intended, stored = self._values(
                    change, seen.as_read, seen.intended, seen.stored
                )
                line = (
                    f"  {_what(seen)}: read {as_read}, "
                    f"meant {intended}, stored {stored}"
                )
                if change.clash:
                    line += "  [clash: also changed on the VCO]"
                lines.append(line)
            lines.extend(_and_more(len(self.lost_edits), limit, "lost_edits"))
        if self.applied:
            lines.append("already stored:")
            lines.extend(
                f"  {_what(self._shown(change))}"
                for change in _first(self.applied, limit)
            )
            lines.extend(_and_more(len(self.applied), limit, "applied"))
        if self.server_changes:
            lines.append("stored otherwise than sent:")
            whole = _written_whole(self.applied)
            dropped = _dropped(self.server_changes)
            for server_change in _first(self.server_changes, limit):
                seen_change = self._shown_server(server_change, whole, dropped)
                as_read, stored = self._values(
                    server_change, seen_change.as_read, seen_change.stored
                )
                lines.append(
                    f"  {_where(seen_change)}: read {as_read}, stored {stored}"
                )
            lines.extend(_and_more(len(self.server_changes), limit, "server_changes"))
        if self.completed:
            lines.append(
                "written before this one: "
                + ", ".join(
                    f"{python_module_name(write.name)} ({write.operation})"
                    for write in self.completed
                )
            )
        return "\n".join(lines)

    @override
    def __str__(self) -> str:
        # Computed when asked: ``completed`` is set after the error is built.
        return self.render()

    def redo_patch(self, *, include_clashes: bool = False) -> dict[str, object]:
        """The patch that redoes the lost edits on ``module``, the module as stored.

        It is plain JSON (``json.loads(json.dumps(patch)) == patch``), rooted
        at the module, so ``await pipeline.apply(state,
        module=error.module_name, patch=error.redo_patch())`` takes it, and so
        does ``error.module.data.merge_patch(error.redo_patch())`` without a
        state. Each lost edit is redone from what is stored to what was meant:
        an added item goes back in its place, a lost order is made again by the
        moves from the stored order, and each item is named as the stored list
        names it, so an edit to "the second rule" still reaches that rule when
        another writer put one in front of it. An item the plan added is added
        again, never set on the item another writer has under its name or at
        its place: where that name is taken, ``apply`` refuses.

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
        the segment's place, and ``apply`` adds the segment again. So is a
        segment the pipeline added to a profile module: it is redone as an
        ``add`` under its name, with the plan's edits inside it
        (``{"segments": {"Guest": {"_op": "add", ...}}}``), which assigns it
        again. A repaired default of a QoS segment that is lost is neither
        redone nor listed: the next write of the module repairs it again.

        The patch is empty, and nothing is left out, for a
        ``WriteOutcomeUnknownError``: the caller's data still holds the edits.
        It is empty, with every lost edit left out, for an error that has no
        plan or no module.

        ``apply`` checks the patch like any other and may refuse it, changing
        nothing, where the stored data no longer takes it (a rename to a name
        another writer gave an item, for example) or where the edit is one
        ``apply`` refuses as a patch: make such an edit again the way it was
        made.
        """
        return self._redo(include_clashes=include_clashes)[0]

    def not_redone(self, *, include_clashes: bool = False) -> tuple[PlannedChange, ...]:
        """The lost edits that ``redo_patch`` leaves out.

        That is ``redo_patch(include_clashes=include_clashes)``, and they come
        in the order of ``lost_edits``.
        """
        return self._redo(include_clashes=include_clashes)[1]

    def _redo(  # noqa: C901, PLR0912 (existing: split it when it next changes)
        self, *, include_clashes: bool
    ) -> tuple[dict[str, object], tuple[PlannedChange, ...]]:
        """The patch that redoes the lost edits, and the lost edits it leaves out."""
        plan, module = self.plan, self.module
        data: object = None if module is None else getattr(module, "data", None)
        if plan is None or not isinstance(data, VcoModel):
            return {}, self.lost_edits
        stored_parts = dict(self.stored_parts)
        changes: list[RawChange] = []
        owner: dict[int, PlannedChange] = {}
        added: list[str] = []  # the selectors of the segments to add again
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
            elif edit.path in plan.legacy_keys or _is_repaired_default(plan, edit):
                continue
            elif (is_added := _is_added_segment(plan, edit)) or _is_reconciled_segment(
                plan, edit
            ):
                if is_added:
                    added.append(selector_of(cast("Key", edit.path.parts[1])))
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
        for selector in added:
            # The segment is assigned again, with the plan's edits inside it.
            segments = cast("dict[str, Any]", patch.setdefault("segments", {}))
            body = cast("dict[str, Any]", segments.setdefault(selector, {}))
            body["_op"] = "add"
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
            if (
                value is MISSING
                or module is None
                or not _set_provider(
                    patch, data, module, plan, stored_parts, edits, value
                )
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


def _is_added_segment(plan: ModulePlan, edit: PlannedChange) -> bool:
    """Whether *edit* is the whole entry of an added segment.

    The segment is one the pipeline added to the profile module.

    The module as read had no entry for it, so the check finds the whole entry
    lost when the VCO stored none of it. The plan's changes are relative to the
    module plus its additions, so what the plan changed inside the entry is
    what to redo beside the add.
    """
    parts = edit.path.parts
    return (
        edit.as_read is MISSING
        and len(parts) == 2  # noqa: PLR2004  # segments[<segment>] has two parts
        and parts[0] == "segments"
        and isinstance(parts[1], Key)
        and parts[1].value in plan.added_segments
    )


def _is_repaired_default(plan: ModulePlan, edit: PlannedChange) -> bool:
    """Whether *edit* is a ``defaults`` entry, or the list, that the plan repaired.

    The next write of the module repairs it again, so it is not an edit to redo.
    """
    parts = edit.path.parts
    return (
        len(parts) in (3, 4)
        and parts[0] == "segments"
        and isinstance(parts[1], Key)
        and parts[2] == "defaults"
        and any(
            missing.repaired
            and missing.segment == parts[1].value
            and (len(parts) == 3 or _is_default_named(parts[3], missing.names))  # noqa: PLR2004  # segments[<segment>].defaults has three parts; an entry of it a fourth
            for missing in plan.missing_defaults
        )
    )


def _is_default_named(part: object, names: Sequence[str]) -> bool:
    """Whether the ``defaults`` item *part* names is one of *names*."""
    return isinstance(part, Key) and part.value in names


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
        and len(parts) == 2  # noqa: PLR2004  # segments[<segment>] has two parts
        and parts[0] == "segments"
        and not isinstance(parts[1], str)
        and all(change.path != edit.path for change in plan.changes)
    )


def _provider_value(edits: Sequence[PlannedChange]) -> object:
    """The value that sets a ``RefOnly`` field again.

    It comes from the refs entries *edits* at it.

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


_SEGMENT_REF: Final = "deviceSettings:segment"
"""The ref name of a segment's row; ``ref_owners.SEGMENT_REF``, which this module cannot
import (a cycle)."""


def _json_objects(value: object) -> list[dict[str, object]]:
    """The objects of *value*, a list or one object; none for anything else."""
    items = value if isinstance(value, list) else [value]
    return [
        cast("dict[str, object]", item)
        for item in cast("list[object]", items)
        if isinstance(item, dict)
    ]


def _json_field(value: object, *names: str) -> object:
    """The value at *names* below *value*.

    None where a step is not an object or is absent.
    """
    for name in names:
        if not isinstance(value, dict):
            return None
        value = cast("dict[str, object]", value).get(name)
    return value


def _same_logical_id(left: object, right: object) -> bool:
    return (
        isinstance(left, str)
        and isinstance(right, str)
        and (left.lower() == right.lower())
    )


def _stored_segment(  # noqa: PLR0913, PLR0917 (existing: split it when it next changes)
    data: VcoModel,
    module: ConfigurationModuleBase,
    plan: ModulePlan,
    stored_parts: Mapping[Path, ItemPart],
    item: ItemPart,
    object_ids: Sequence[int],
) -> ItemPart | None:
    """The part that names the segment the edits were made to.

    It is the part in the ``segments`` of *module* as stored; None when it
    cannot be proved.

    The check records it for an item it walked (a data edit in the segment).
    Otherwise the segment is found by identity, never by name: another segment
    may have taken the name. A stored entry is the segment when its
    ``segmentId`` is that of the stored ``deviceSettings:segment`` row whose
    ``enterpriseObjectId`` is one of *object_ids* (the ``segmentObjectId`` of
    the refs entries). When that row has no ``segmentId``, its ``segmentLogicalId``
    is the row's ``logicalId``; with no such row, it is that of the plan's
    segment named *item*. Exactly one entry must match, and a key must name it
    alone.
    """
    found = stored_parts.get(Path(("segments", item)))
    if found is not None:
        return found
    node = node_child(type(data), "segments")
    stored = _json_field(module.raw_data, "segments")
    if not isinstance(node, KeyedList) or not is_list(stored):
        return None
    rows = [
        row
        for row in _json_objects(_json_field(module.raw_refs, _SEGMENT_REF))
        if row.get("enterpriseObjectId") in object_ids
    ]
    by_id = {_json_field(row, "data", "segmentId") for row in rows} - {None}
    if by_id:
        matches = [
            part
            for part, entry in zip(item_parts(stored, node), stored, strict=True)
            if _json_field(entry, "segment", "segmentId") in by_id
        ]
    else:
        logical_ids = [row.get("logicalId") for row in rows]
        if not rows and isinstance(item, Key):
            logical_ids = [
                _json_field(entry, "segment", "segmentLogicalId")
                for entry in _json_objects(_json_field(plan.data, "segments"))
                if _json_field(entry, "segment", "name") == item.value
            ]
        matches = [
            part
            for part, entry in zip(item_parts(stored, node), stored, strict=True)
            if any(
                _same_logical_id(
                    _json_field(entry, "segment", "segmentLogicalId"), known
                )
                for known in logical_ids
            )
        ]
    return matches[0] if len(matches) == 1 and isinstance(matches[0], Key) else None


def _field_spellings(
    models: Sequence[type[VcoModel]], names: Sequence[str]
) -> list[tuple[str, str]] | None:
    """The python name and wire name of each field *names* below one of *models*.

    None where no model has the field.
    """
    name, *rest = names
    for model in models:
        field_info = model.model_fields.get(name)
        if field_info is None:
            continue
        below = _field_spellings(field_models(field_info), rest) if rest else []
        if below is not None:
            return [(name, wire_name(model, name)), *below]
    return None


def _set_provider(  # noqa: PLR0913, PLR0917 (existing: split it when it next changes)
    patch: dict[str, object],
    data: VcoModel,
    module: ConfigurationModuleBase,
    plan: ModulePlan,
    stored_parts: Mapping[Path, ItemPart],
    edits: Sequence[PlannedChange],
    value: object,
) -> bool:
    """Set the ``RefOnly`` field the refs entries *edits* go with to *value* in *patch*.

    The field is set in the segment as the stored module names it. False when
    the patch has no place for it.

    The owner is ``segments[<segment>].<fields>`` in python names, and a
    ``RefOnly`` field is not in a raw dump, so the path is built from its parts.
    Each field is written by its wire name, unless the patch already has the
    object under its python name: ``merge_patch`` refuses both for one field.
    The segment's object is the one a data entry of the patch has, if any.
    """
    owner = cast("Path", edits[0].owner)
    object_ids = [
        key.value[1]
        for edit in edits
        for key in [edit.path.parts[-1]]
        if isinstance(key, Key)
        and isinstance(key.value, tuple)
        and len(key.value) == 2  # noqa: PLR2004  # a two-part key, its second part the object ID
        and isinstance(key.value[1], int)
    ]
    segment, *names = owner.parts[1:]
    if isinstance(segment, str) or not all(isinstance(name, str) for name in names):
        return False
    models = field_models(type(data).model_fields["segments"])
    fields = _field_spellings(models, cast("list[str]", names))
    part = _stored_segment(data, module, plan, stored_parts, segment, object_ids)
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
    """The key *node* already has for a field, by one of its *spellings*.

    Else the last spelling.
    """
    return next((key for key in spellings if key in node), spellings[-1])


def _child(
    node: dict[str, object], spellings: tuple[str, ...]
) -> dict[str, object] | None:
    """The object of *node* under a field's key (``_spelled``).

    It is made if there is none; None where the key holds something else.
    """
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

    def __init__(  # noqa: PLR0913 (an error's constructor: each argument is a field the error reports)
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
        """The conflict, with *detail* and how the refusal is known (*stated*, *sent*).

        The other arguments are those of ``ModuleWriteError``.
        """
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

    def __init__(  # noqa: PLR0913 (an error's constructor: each argument is a field the error reports)
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
        """The write, with the *module* as stored and the *lost_edits* it does not hold.

        The other arguments are those of ``ModuleWriteError``.
        """
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
    ``refs_as_read`` holds the rows to put back: the module's refs as read
    (``MISSING`` when it had none), without the rows of the
    ``APPEND_ONLY_REF_NAMES`` (``deviceSettings:vnfs:edge``,
    ``objectGroup:addressGroup`` and ``objectGroup:portGroup``). Those rows are
    the VCO's own and it keeps them through the write, so sending them back
    would store them twice; they were not among the rows sent either.
    """

    refs_as_read: object

    def __init__(  # noqa: PLR0913 (an error's constructor: each argument is a field the error reports)
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
        """As ``WriteNotAppliedError``, with *refs_as_read*: the refs to put back."""
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
        """Whether the VCO took none of the rows sent, and some were sent as read.

        Each of those is a lost edit, and ``refs_as_read`` holds them to put back.
        """
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

    def _put_back_note(self) -> str:
        """How to put the rows back when the VCO took none of those sent.

        It is written for a caller with or without Python.
        """
        configuration_id = (
            "<configuration ID>"
            if self.module is None or self.module.configuration_id is None
            else self.module.configuration_id
        )
        from pyvelo.client.derived_refs import APPEND_ONLY_REF_NAMES  # noqa: PLC0415 (derived_refs reaches this module through the models)

        names = ", ".join(sorted(APPEND_ONLY_REF_NAMES))
        return (
            "The VCO took none of the ref rows this write sent, and the data is "
            "stored. To put the rows back, send "
            "configuration/updateConfigurationModule with "
            f'{{"id": {self.module_id}, "_update": {{"data": <the module\'s '
            'data as stored now>, "refs": <error.refs_as_read>}} '
            "(update_configuration_module; error.refs_as_read holds the rows to "
            "send). If you speak only JSON, read the data as stored now with "
            "configuration/getConfiguration and "
            f'{{"id": {configuration_id}, "with": ["modules"]}}, and for "refs" '
            "send the module's refs from the read this write was planned on (the "
            "VCO's own now lack the rows it dropped), without the rows named "
            f"{names}: the VCO keeps those itself and would store them twice."
        )

    @override
    def _notes(self, limit: int | None) -> list[str]:
        if self.every_row_lost:
            lines = [self._put_back_note()]
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
                "  error.redo_patch() builds these patches with the real value "
                "(null for a clear), for each field whose segment error.module has:"
            )
        for edit in _first(owners, limit):
            owner = cast("Path", edit.owner)
            if self.plan is not None:
                # As the plan's ref line names the field; the patch is that path.
                owner = self.plan.named_owner(owner, added=edit.intended is not MISSING)
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
    """Raised when a module write was sent and the module as stored could not be read.

    This includes a write that was about to be sent again.

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

    def __init__(  # noqa: PLR0913 (an error's constructor: each argument is a field the error reports)
        self,
        module_id: int,
        version: str,
        detail: str,
        *,
        answer: WriteAnswer,
        module_name: str,
        plan: ModulePlan,
    ) -> None:
        """The write of *plan*, with *detail* and the VCO's *answer* to the write."""
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
            said = (
                "The VCO counted the write, so the changes below are most likely "
                "stored."
            )
        else:
            said = "Whether the changes below are stored is not known."
        lines = [said, "planned, not checked:"]
        if self.plan is not None:
            for change in _first(self.plan.changes, limit):
                # As the plan's own line for the change shows it.
                seen = self.plan.shown_change(change)
                before, after = shown_together(
                    seen.before,
                    seen.after,
                    model=self.plan.model,
                    path=change.path,
                )
                where = f"{seen.path}{_place_or_order(seen.place, seen.moves)}"
                lines.append(f"  {where}: read {before}, meant {after}")
            lines.extend(_and_more(len(self.plan.changes), limit, "plan.changes"))
            lines.extend(self.plan.ref_lines())
        lines.append(
            "Read the configuration again and compare before you write this "
            "module again."
        )
        return lines


class UnboundError(VcoError):
    """Raised when a layer is asked for something that only its profile can give.

    The layer has no profile.

    A segment a layer lacks is asked for with no profile to copy it from, or an
    edge-layer value is asked to ``localize`` (copy the profile's value in)
    with no profile to copy from. The layer data has not been bound to a
    profile (see ``bind_profile`` and ``bind_layers``), which happens when it
    is built outside ``ConfigState``.
    """


class LayerUnknownError(VcoError):
    """Raised by an update that was given no layer and answered with a firewall row.

    The update asked for the module row back.

    A firewall module is of the edge layer or the profile layer, and the row
    the VCO answers with does not say which; reading it as the wrong one would
    not raise. The update was sent and the VCO answered with the module's row,
    so the write is most likely stored. ``module_id`` is the row's ``id`` and
    ``answer`` the row as the VCO returned it, as JSON, so that nothing it said
    is lost. Read the module again, or call with ``level=ConfigContext.EDGE`` or
    ``level=ConfigContext.PROFILE``.
    """

    module_id: int | None
    answer: dict[str, object]

    def __init__(
        self, message: str, *, module_id: int | None, answer: dict[str, object]
    ) -> None:
        """The error with *message*, the row's *module_id* and the row as *answer*."""
        self.module_id = module_id
        self.answer = answer
        super().__init__(message)


class GroupOwnershipError(VcoError):
    """Raised by an edit that would leave a coupled group half inherited.

    The edit is of one member of the group.

    The members of a group (the stateful firewall's settings, its IPv6
    settings and its enabled flag, say) change ownership together: the UI does
    not recognize a group with some members local and the others inherited.
    ``group`` is the group's name and ``field`` the python name of the member
    the edit was made through. The message says to edit the group as a whole,
    and gives the patch that does the same.
    """

    group: str
    field: str

    def __init__(self, message: str, *, group: str, field: str) -> None:
        """The error with *message*, the *group*'s name and the *field* edited."""
        self.group = group
        self.field = field
        super().__init__(message)


class ComposedModuleError(VcoError):
    """Raised by ``compare_effective`` for a module with no composed data.

    The VCO's composed read gave no data for it.

    ``edge/getEdgeConfigurationModules`` hides a composition error by
    returning ``null`` for the module, and a module it was asked for may be
    left out of the answer. Either way there is nothing to check the effective
    view against, so the comparison of that module stops with this error
    instead of reporting every key as missing. ``module`` is the module's
    python name. Open the edge in the VCO to see why its modules do not
    compose, or compare the other modules.
    """

    module: str

    def __init__(self, message: str, *, module: str) -> None:
        """The error with *message* for *module*, given by either of its names."""
        self.module = python_module_name(module)
        super().__init__(message)


class DetachedObjectError(VcoError):
    """Raised by an edit through an object that a write replaced.

    ``update_module`` raises it too, for a module an earlier write replaced.

    After a save the state holds the module as the VCO stored it, and after
    ``ConfigPipeline.revert`` the module as the VCO last gave it; after
    ``update_module`` the module it returned (or ``error.module``, after a
    write error) is that module. The object raising belongs to the module
    that was there before, which a write or a revert replaced. Take the object
    again from the module the state holds now. ``paths`` are where in the
    module such edits were found, when they were found by comparison; they may
    come from several modules, which the message names by their python names,
    a line each. A path names a field by its attribute; an item is named by
    what names it where the raiser holds the data, else by the key or the
    position the comparison found it at. An
    assignment or ``merge_patch`` through a replaced object names the object
    and leaves them empty, and so does the refusal of ``update_module`` to
    write a module an earlier ``update_module`` replaced. A ``merge_patch``,
    ``apply`` or ``assign_segment`` refused because the model holds a replaced
    object inside, put back as it is, names that object and sets ``paths`` to
    where it sits. Only ``update_module`` marks the module it is given;
    ``write_module`` does not.
    """

    paths: list[Path]

    def __init__(self, message: str, paths: Sequence[Path] = ()) -> None:
        """The error with *message* and the *paths* of the edits found by comparison."""
        self.paths = list(paths)
        super().__init__(message)


PRECONDITION_FAILED: Final = "UPDATE_CONFIGURATION_MODULE_PRECONDITION_FAILED"
"""The code of the VCO's refusal of a stale module version, in
``error.data.error[]``."""


def _lists_precondition(data: object) -> bool:
    """Whether a JSON-RPC error's ``data`` lists a ``PRECONDITION_FAILED`` entry.

    The entry is one of ``error[]`` with the code ``PRECONDITION_FAILED``.
    """
    if not isinstance(data, Mapping):
        return False
    entries = cast("Mapping[str, object]", data).get("error")
    if not isinstance(entries, list):
        return False
    return any(
        isinstance(entry, Mapping)
        and cast("Mapping[str, object]", entry).get("code") == PRECONDITION_FAILED
        for entry in cast("list[object]", entries)
    )


def is_precondition_failure(error: VcoOperationError) -> bool:
    """Whether *error* is the VCO refusing a stale module version before it writes.

    A VCO with its conditional check on answers an update whose ``version`` is
    not the module's current one with a ``validationError`` whose data lists an
    error with the code ``PRECONDITION_FAILED``, and writes nothing.
    """
    return _lists_precondition(cast("object", error.data))


def is_precondition_answer(text: str) -> bool:
    """Whether *text* is a JSON-RPC answer carrying the precondition refusal.

    The same refusal as :func:`is_precondition_failure`, found in the body of
    an answer that came with a non-2xx HTTP status, and so was never parsed.
    False for text that is not JSON, is not an object, or has no ``error``
    object whose data lists the code.
    """
    try:
        answer = cast("object", json.loads(text))
    except ValueError:
        return False
    if not isinstance(answer, Mapping):
        return False
    error = cast("Mapping[str, object]", answer).get("error")
    if not isinstance(error, Mapping):
        return False
    return _lists_precondition(cast("Mapping[str, object]", error).get("data"))


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
