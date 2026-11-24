"""``Owned``: what an inheritable field holds on the edge layer, the encodings that say how it is stored, and the ways to change who owns it.

An edge layer may override a value its profile layer provides. ``Owned[T]``
keeps what the edge stores at the key, and whether that counts as an override
follows from the field's encoding, declared once on the field. A caller
changes who owns the value by one of three intents, and by no assignment:

- ``set_local(value)``: the edge overrides the profile with *value*.
- ``inherit()``: the edge stores nothing, so the profile's value applies.
- ``localize()``: the profile's value is copied in as the edge's own, and
  returned to be edited in place.

Some keys change ownership together (a coupled group, ``OwnedGroup``): the
group's handle does the intents for every member, and a member refuses the
ones that would leave the group half inherited. ``bind_layers`` links an edge
layer to the profile layer it inherits from, which ``localize`` copies from.

The intents read the field's declaration (``layers.field_kind``) and validate
as a patch does (``layers.inner_adapter``); ``layers`` imports this module, so
those imports are made when an intent runs.
"""

# layers imports this module; the intents import layers when they run.
# The group handle and the binding work on the private state of ``Owned`` and
# ``Layered``, which they share this module (and ``layers``) with.
# pyright: reportImportCycles=false, reportPrivateUsage=false

from __future__ import annotations

import copy
import functools
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self, cast, get_args, overload

from pydantic import MISSING

if TYPE_CHECKING:
    from .base import VcoModel
    from .layers import Edge, Layered, Profile
    from .patch import PatchError


@dataclass(frozen=True)
class ByPresence:
    """Encoding: a value is local when its key is present and not ``null``.

    ``false``, ``0``, ``{}`` and ``[]`` count as present.
    """


@dataclass(frozen=True)
class ByFlag:
    """Encoding: an object is local when it is present and its flag *flag* is true.

    *inherit_as* makes the object a path that persists a reset keeps (for
    instance ``{override: false, affinity: []}``); without it, inheriting
    omits the object. *presence_wins* records that the backend composer
    selects a present edge object without checking the flag.
    """

    flag: str
    inherit_as: Callable[[], object] | None = None
    presence_wins: bool = False


type Encoding = ByPresence | ByFlag


@dataclass(frozen=True)
class MemberOf:
    """Annotated marker: this ``Inheritable`` field is a member of the coupled group *group*.

    Usage: ``Annotated[Inheritable[bool], ByPresence(), MemberOf("stateful"),
    Default(False)]``. The group is declared once on the class with an
    ``OwnedGroup``.
    """

    group: str


class Binding:
    """The profile-layer model an edge-layer model inherits from, or ``None``: it has no counterpart.

    ``bind_layers`` sets one on each model it reaches. A deep copy keeps it
    and shares the profile: the copy inherits from the same profile.
    """

    __slots__ = ("parent",)
    parent: Layered[Profile] | None

    def __init__(self, parent: Layered[Profile] | None) -> None:
        self.parent = parent

    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        return self

    def __copy__(self) -> Self:
        return self


class Owned[T]:
    """What an ``Inheritable[T]`` field holds on the edge layer.

    *stored* is what the edge layer holds at the key, parsed: a value,
    ``None`` for a stored JSON ``null``, or ``MISSING`` when the key is
    absent. *is_local* says whether that counts as the edge overriding the
    profile; *local* is *stored* then, and ``MISSING`` otherwise.

    A model of the edge layer attaches the ``Owned`` it holds to itself when it
    is validated, constructed or deep-copied, which is what gives *is_local* the
    field's encoding and the intents the field to work on. The first model to
    attach it keeps it: a shallow copy holds the same ``Owned`` values as its
    original, which stay the original's. An ``Owned`` that
    belongs to no model (one built by hand, or a deep copy of one taken from a
    model) reads *is_local* by presence, and its intents store what they are
    given without checking it.

    An ``Owned`` given as input to a model is copied, *stored* too, so the
    model does not hold the caller's object.
    """

    stored: T | None | MISSING
    _owner: Layered[Edge] | None
    _field: str
    _encoding: Encoding | None

    def __init__(self, stored: T | None | MISSING = MISSING) -> None:
        self.stored = stored
        self._owner = None
        self._field = ""
        self._encoding = None

    def _attach(
        self, owner: Layered[Edge], field: str, encoding: Encoding | None
    ) -> None:
        if self._owner is not None:
            # The model that held it first keeps it: a shallow copy of a model
            # (the merge validates one) holds the same Owned values, and
            # validating it must not make them the copy's.
            return
        self._owner = owner
        self._field = field
        self._encoding = encoding

    @property
    def is_local(self) -> bool:
        stored = self.stored
        if stored is MISSING or stored is None:
            return False
        encoding = self._encoding
        if isinstance(encoding, ByFlag):
            return bool(getattr(stored, encoding.flag, False))
        return True

    @property
    def local(self) -> T | MISSING:
        return self.stored if self.is_local else MISSING  # pyright: ignore[reportReturnType]

    # -- the intents ----------------------------------------------------------

    def set_local(self, value: T) -> None:
        """Override the profile with *value*.

        *value* is validated as the field's type, as a patch value is (a
        ``PatchError`` says why not, and nothing changes), and a copy of it is
        stored; a ``ByFlag`` field stores it with the flag set. A ``null`` is
        refused: at this key it means inherit. On a member of a coupled group
        this raises ``GroupOwnershipError`` unless the group is local. After
        a save or ``update_module`` replaced the module this belongs to, it
        raises ``DetachedObjectError``.
        """
        self._refuse("set_local")
        self.stored = self._local_value(value)  # pyright: ignore[reportAttributeAccessIssue]

    def inherit(self) -> None:
        """Store nothing, so the profile's value applies.

        A ``ByFlag`` field with ``inherit_as`` stores that object instead (a
        reset object the VCO keeps). On a member of a coupled group this
        raises ``GroupOwnershipError``: inherit the group. After a save or
        ``update_module`` replaced the module this belongs to, it raises
        ``DetachedObjectError``.
        """
        self._refuse("inherit")
        self._inherit()

    def localize(self) -> T:
        """Copy the profile's value in as the edge's own, and return it to be edited in place.

        The value is the profile's, or the field's display default when the
        profile does not store one or the edge has no profile, copied deep;
        the profile is unchanged. What is returned is the stored object, so
        an edit through it is an edit of the edge. A value that is local
        already is returned as it is. A ``null`` cannot be local (``None`` is
        returned, and the field stays inherited). It needs the profile, so it
        raises ``UnboundError`` on an object that did not come from a loaded
        state, and ``GroupOwnershipError`` on a member of a coupled group
        that is not local (``localize`` the group). After a save or
        ``update_module`` replaced the module this belongs to, it raises
        ``DetachedObjectError``.
        """
        self._refuse("localize")
        return self._localize()  # pyright: ignore[reportReturnType]

    # -- the work, with no check of the group (the group handle does those) ----

    def _refuse(self, intent: str) -> None:
        """Raise if the intent *intent* may not be made now: the module was replaced, or the field belongs to a group and the intent would split it."""
        owner = self._owner
        if owner is None:
            return
        owner.check_not_detached(f"{self._field}.{intent}()")
        found = group_of(owner, self._field)
        if found is None:
            return
        if intent == "inherit" or not found.handle(owner).is_local:
            _refuse_member(owner, self._field, found, intent)

    def _inherit(self) -> None:
        encoding = self._encoding
        if isinstance(encoding, ByFlag) and encoding.inherit_as is not None:
            self.stored = encoding.inherit_as()  # pyright: ignore[reportAttributeAccessIssue]
        else:
            self.stored = MISSING

    def _local_value(self, value: object) -> object:
        """A copy of *value* validated as this field's type, ready to store as local."""
        owner = self._owner
        if owner is None:
            return copy.deepcopy(value)
        if value is None:
            raise _null_is_inherit(owner, self._field)
        checked = _validate_inner(owner, self._field, copy.deepcopy(value))
        return self._flagged(checked)

    def _flagged(self, value: object) -> object:
        encoding = self._encoding
        if isinstance(encoding, ByFlag) and value is not None:
            setattr(value, encoding.flag, True)
        return value

    def _parent_value(self) -> object:
        """A deep copy of what the profile gives this field, or the display default; ``UnboundError`` if unbound."""
        from .layers import display_default

        owner = self._owner
        binding = None if owner is None else owner._binding
        if owner is None or binding is None:
            raise _unbound(owner, self._field)
        info = type(owner).model_fields[self._field]
        parent = binding.parent
        if parent is None:
            return clear_server_owned(display_default(info))
        value: object = getattr(parent, self._field)
        return clear_server_owned(copy.deepcopy(value))

    def _localize(self) -> object:
        if self.is_local:
            return self.stored
        value = self._parent_value()
        self._store_copy(value)
        return value

    def _store_copy(self, value: object) -> None:
        """Make *value*, a copy of the parent's, the local value; a ``null`` cannot be local, so nothing changes."""
        if value is not None:
            self.stored = self._flagged(value)  # pyright: ignore[reportAttributeAccessIssue]

    def __deepcopy__(self, memo: dict[int, Any]) -> Owned[T]:
        # The copy belongs to no model until the model that holds it attaches it.
        return Owned(copy.deepcopy(self.stored, memo))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Owned) and self.stored == other.stored  # pyright: ignore[reportUnknownMemberType]

    __hash__ = None  # pyright: ignore[reportAssignmentType]

    def __repr__(self) -> str:
        return f"Owned({self.stored!r})"


def clear_server_owned(value: object) -> object:
    """*value*, a copy the caller owns, with every ``ServerOwned`` field of every model inside it cleared.

    A field the VCO assigns (a rule's ``ruleLogicalId``) holds an ID that
    belongs to the profile's object, not to the copy the edge makes of it, so
    the copy carries none: the field becomes ``MISSING`` where its type
    accepts that, else its default. Models, list items and dict values are
    walked; *value* is changed in place and returned.
    """
    from pydantic import BaseModel

    from .fields import is_server_owned

    if isinstance(value, BaseModel):
        cls = type(value)
        for name, info in cls.model_fields.items():
            if is_server_owned(info):
                if MISSING in get_args(info.annotation):
                    cleared: object = MISSING
                else:
                    cleared = info.get_default(call_default_factory=True)
                object.__setattr__(value, name, cleared)
                value.__pydantic_fields_set__.discard(name)
            else:
                _ = clear_server_owned(getattr(value, name, None))
    elif isinstance(value, list):
        for item in cast("list[object]", value):
            _ = clear_server_owned(item)
    elif isinstance(value, dict):
        for item in cast("dict[object, object]", value).values():
            _ = clear_server_owned(item)
    return value


def unwrap(value: object) -> object:
    """What a field's value holds: an ``Owned``'s ``stored``, anything else as it is.

    A walk that reads field values from a model instance reads through this,
    as one that reads field types goes through ``layers.field_kind``. What an
    ``Owned`` stores is ``MISSING`` when the edge stores nothing.
    """
    if isinstance(value, Owned):
        stored: object = value.stored  # pyright: ignore[reportUnknownMemberType]
        return stored
    return value


# ---------------------------------------------------------------------------
# Validation of a local value
# ---------------------------------------------------------------------------


def _key(owner: Layered[Edge], field: str) -> str:
    from .base import wire_name

    return wire_name(type(owner), field)


def _null_is_inherit(owner: Layered[Edge], field: str) -> PatchError:
    from .patch import PatchError
    from .rebase import Path

    key = _key(owner, field)
    return PatchError(
        f"null is not a local value of {key}: a null at this key means the edge "
        + f"inherits it, so nothing would be overridden; call .{field}.inherit() "
        + f'or send {{"{key}": null}} to inherit, or give a value',
        Path((field,)),
        "null",
    )


def _validate_inner(owner: Layered[Edge], field: str, value: object) -> object:
    """*value* validated as the inner type of *field* of *owner*, as ``merge_patch`` validates a patched value.

    ``validate_patched_value`` does it, so a refusal is the one a patch of the
    field gives: the same ``PatchError``, at the python name of the field.
    """
    from .base import field_models
    from .layers import inner_adapter
    from .merge import validate_patched_value
    from .rebase import Path

    cls = type(owner)
    return validate_patched_value(
        inner_adapter(cls, field),
        value,
        field_models(cls.model_fields[field]),
        Path((field,)),
    )


def _unbound(owner: Layered[Edge] | None, field: str) -> Exception:
    from pyvelo.client.exceptions import UnboundError

    where = f"{type(owner).__name__}.{field}" if owner is not None else "this Owned"
    key = _key(owner, field) if owner is not None else "<key>"
    return UnboundError(
        f"{where}: localize copies the profile's value, and this object does not "
        + "know its profile, because it was not read from a loaded state. Take "
        + "the object from the state (state.edge.<module>), where it is bound "
        + "to its profile; or give the value yourself with .set_local(value), "
        + f'or in a patch {{"{key}": <value>}}'
    )


# ---------------------------------------------------------------------------
# Coupled groups
# ---------------------------------------------------------------------------


def _group_name(owner: Layered[Edge], field: str) -> str | None:
    from .layers import field_kind

    info = type(owner).model_fields[field]
    return next(
        (m.group for m in field_kind(info).metadata if isinstance(m, MemberOf)), None
    )


def group_of(owner: Layered[Edge], field: str) -> OwnedGroup | None:
    """The coupled group that field *field* of *owner* is a member of, or ``None``."""
    return groups_of(type(owner)).get(_group_name(owner, field) or "")


def _refuse_member(
    owner: Layered[Edge], field: str, group: OwnedGroup, intent: str
) -> None:
    from pyvelo.client.exceptions import GroupOwnershipError

    cls = type(owner)
    handle = f"{group.attribute}"
    anchor_key = _key(owner, group.anchor)
    where = f"{cls.__name__}.{field}"
    if intent == "inherit":
        message = (
            f'{where} is a member of the group "{group.name}", whose members '
            + "change ownership together: a group with some members inherited "
            + "and others local is a state the VCO UI does not recognize. "
            + f"Inherit the whole group with .{handle}.inherit(), or in a patch "
            + f'send {{"{anchor_key}": null}}'
        )
    else:
        keys = ", ".join(f'"{_key(owner, name)}": ...' for name in group.members(cls))
        message = (
            f'{where} is a member of the group "{group.name}", which the edge '
            + "inherits, so setting one member would leave the others behind: "
            + "a group with some members inherited and others local is a state "
            + "the VCO UI does not recognize. Make the group local first, with "
            + f".{handle}.set_local(bundle) or .{handle}.localize(), or in a "
            + f"patch send every member: {{{keys}}}"
        )
    raise GroupOwnershipError(message, group=group.name, field=field)


class OwnedGroup:
    """Declares a coupled group on a ``Layered`` class: members that change ownership together.

    A class variable: ``stateful: ClassVar[OwnedGroup] = OwnedGroup("stateful",
    anchor="stateful_firewall_settings", bundle=StatefulLocal)``. Its members
    are the ``Inheritable`` fields marked ``MemberOf("stateful")``, ``anchor``
    among them: the group is local when the anchor is. *bundle* is a model
    with a required field for each member (and no other), what ``set_local``
    of the group takes.

    On an edge-layer instance the attribute is the group's handle
    (``GroupHandle``); on any other layer it does not exist, as the members
    are plain values there. The declaration is checked when the class is
    created.
    """

    def __init__(self, name: str, *, anchor: str, bundle: type[VcoModel]) -> None:
        self.name = name
        self.anchor = anchor
        self.bundle = bundle
        self.attribute = name

    def __set_name__(self, owner: type, name: str) -> None:
        self.attribute = name

    @overload
    def __get__(self, obj: None, owner: type) -> Self: ...
    @overload
    def __get__(self, obj: Layered[Edge], owner: type | None = None) -> GroupHandle: ...
    def __get__(
        self, obj: Layered[Any] | None, owner: type | None = None
    ) -> Self | GroupHandle:
        if obj is None:
            return self
        from .layers import Edge

        layer = type(obj).layer()
        if layer is not Edge:
            raise AttributeError(
                f"{type(obj).__name__} has no group handle {self.attribute!r}: "
                + f"the {layer.__name__} layer has no coupled groups, its members "
                + "are plain values; the handle is on the edge layer"
            )
        return self.handle(obj)

    def handle(self, owner: Layered[Edge]) -> GroupHandle:
        return GroupHandle(self, owner)

    def members(self, cls: type[Layered[Any]]) -> tuple[str, ...]:
        """The python names of the members in *cls*, in declaration order."""
        from .layers import field_kind

        return tuple(
            name
            for name, info in cls.model_fields.items()
            if any(
                isinstance(m, MemberOf) and m.group == self.name
                for m in field_kind(info).metadata
            )
        )


@functools.cache
def groups_of(cls: type) -> dict[str, OwnedGroup]:
    """The groups declared on *cls* and its bases, by name."""
    found: dict[str, OwnedGroup] = {}
    for klass in reversed(cls.__mro__):
        for value in vars(klass).values():
            if isinstance(value, OwnedGroup):
                found[value.name] = value
    return found


def check_groups(cls: type[Layered[Any]]) -> None:
    """Check the groups of *cls*; ``TypeError`` for the first declaration that is wrong."""
    from .layers import Kind, field_kind

    groups = groups_of(cls)
    members: dict[str, list[str]] = {name: [] for name in groups}
    for name, info in cls.model_fields.items():
        for marker in field_kind(info).metadata:
            if not isinstance(marker, MemberOf):
                continue
            if field_kind(info).kind is not Kind.INHERITABLE:
                raise TypeError(
                    f"{cls.__name__}.{name}: MemberOf marks an Inheritable field, "
                    + "the only kind that is owned by the edge or inherited"
                )
            if marker.group not in groups:
                raise TypeError(
                    f"{cls.__name__}.{name}: MemberOf({marker.group!r}) names a "
                    + "group the class does not declare; declare it with "
                    + f'{marker.group}: ClassVar[OwnedGroup] = OwnedGroup("{marker.group}", '
                    + "anchor=..., bundle=...)"
                )
            members[marker.group].append(name)
    for group in groups.values():
        named = members[group.name]
        anchor = cls.model_fields.get(group.anchor)
        if anchor is None or group.anchor not in named:
            raise TypeError(
                f"{cls.__name__}: the anchor {group.anchor!r} of the group "
                + f"{group.name!r} is not an Inheritable field marked "
                + f"MemberOf({group.name!r}); the anchor is one of the members"
            )
        bundle_fields = set(group.bundle.model_fields)
        if bundle_fields != set(named):
            missing = sorted(set(named) - bundle_fields)
            extra = sorted(bundle_fields - set(named))
            raise TypeError(
                f"{cls.__name__}: the bundle {group.bundle.__name__} of the group "
                + f"{group.name!r} has the fields of its members and no others"
                + (f"; it lacks {missing}" if missing else "")
                + (f"; it has {extra} too" if extra else "")
            )


class GroupHandle:
    """The coupled group *group* of one edge-layer model: ``is_local``, ``inherit``, ``set_local`` and ``localize`` for every member at once."""

    def __init__(self, group: OwnedGroup, owner: Layered[Edge]) -> None:
        self._group = group
        self._owner = owner

    def _members(self) -> list[Owned[Any]]:
        names = self._group.members(type(self._owner))
        return [getattr(self._owner, name) for name in names]

    def _check(self, intent: str) -> None:
        self._owner.check_not_detached(f"{self._group.attribute}.{intent}()")

    @property
    def is_local(self) -> bool:
        """Whether the group is local: its anchor member is."""
        anchor: Owned[Any] = getattr(self._owner, self._group.anchor)
        return anchor.is_local

    def inherit(self) -> None:
        """Inherit every member: the edge stores none of them."""
        self._check("inherit")
        for owned in self._members():
            owned._inherit()

    def set_local(self, bundle: VcoModel) -> None:
        """Make every member local, each from the field of *bundle* (an instance of the group's bundle model) that has its name.

        Every member is validated before any is changed.
        """
        self._check("set_local")
        if not isinstance(bundle, self._group.bundle):
            raise TypeError(
                f"{self._group.attribute}.set_local takes a {self._group.bundle.__name__} "
                + f"with every member of the group, not a {type(bundle).__name__}; "
                + "a text-driven caller sends the members in a patch"
            )
        members = self._group.members(type(self._owner))
        values = {
            name: getattr(self._owner, name)._local_value(getattr(bundle, name))
            for name in members
        }
        for name, value in values.items():
            getattr(self._owner, name).stored = value

    def localize(self) -> None:
        """Make every member local, copied from the profile; one that is local already is kept.

        The members are edited through their own ``Owned``
        (``fw.stateful_firewall_settings.local.udp_flow_timeout = 600``). It
        needs the profile (``UnboundError``).
        """
        self._check("localize")
        owned = self._members()
        values = [None if o.is_local else o._parent_value() for o in owned]
        for member, value in zip(owned, values, strict=True):
            if value is not None:
                member._store_copy(value)


# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------


def bind_layers(edge: Layered[Edge], profile: Layered[Profile] | None) -> None:
    """Link an edge-layer model to the profile-layer model it inherits from, and the models below each to theirs.

    ``Owned.localize`` and a group's ``localize`` copy from the profile, which
    they find through this. The roots are bound to each other. Below them, a
    ``Layered`` field is bound to the profile's field of that name, and the
    items of a keyed list of ``Layered`` models to the profile's item with the
    same alt key (the stable ID of a segment, ``segment.segment_id``), or the
    same key where the list has no alt key. A model with no counterpart is
    bound to no profile, and ``localize`` gives it the display default. A list
    without a ``KeyField`` is not bound. An item added after binding is not
    bound until this runs again.

    *profile* is ``None`` for an edge with no profile: every model is bound to
    nothing.
    """
    from .fields import get_key_spec
    from .layers import Edge as EdgeLayer
    from .layers import Kind, Layered, field_kind

    if type(edge).layer() is not EdgeLayer:
        raise TypeError(
            "bind_layers links an edge layer, Data[Edge], to its profile layer, "
            + f"Data[Profile]; got {type(edge).__name__}"
        )
    edge._binding = Binding(profile)
    for name, info in type(edge).model_fields.items():
        if field_kind(info).kind is not Kind.STRUCTURAL:
            continue
        value: object = getattr(edge, name)
        other: object = getattr(profile, name, None) if profile is not None else None
        if isinstance(value, Layered):
            bind_layers(
                cast("Layered[Edge]", value),
                next(iter(_profile_models(other)), None),
            )
        elif isinstance(value, list):
            items = [
                cast("Layered[Edge]", item)
                for item in cast("list[object]", value)
                if isinstance(item, Layered)
            ]
            spec = get_key_spec(info)
            if not items or spec is None:
                continue
            counterparts = {
                item_identity(model, spec.paths, spec.alt): model
                for model in _profile_models(other)
            }
            for item in items:
                bind_layers(
                    item, counterparts.get(item_identity(item, spec.paths, spec.alt))
                )


def _profile_models(value: object) -> list[Layered[Profile]]:
    """The ``Layered`` models in *value*: the model itself, or those among the items of a list."""
    from .layers import Layered

    items = cast("list[object]", value) if isinstance(value, list) else [value]
    return [
        cast("Layered[Profile]", item) for item in items if isinstance(item, Layered)
    ]


def item_identity(
    item: object, paths: tuple[str, ...], alt: str | None
) -> tuple[str, object]:
    """What names *item* in its list: its alt key if the list has one and it has a value, else its key."""
    if alt is not None:
        found = item_value_at(item, alt)
        if found not in (None, MISSING):
            return ("alt", found)
    return ("key", tuple(item_value_at(item, path) for path in paths))


def item_value_at(item: object, path: str) -> object:
    value = item
    for part in path.split("."):
        value = getattr(value, part, None)
    return value
