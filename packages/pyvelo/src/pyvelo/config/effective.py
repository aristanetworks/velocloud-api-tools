"""Checking the effective view against the VCO's composed modules.

``compare_module`` is the pure comparison of one module: the view's dump
(``pyvelo.core.effective.effective_dump``) against the raw data the VCO
composed for the same edge. ``ConfigPipeline.compare_effective`` reads the
composed modules and calls it for each module that is converted.

Paths are wire keys, dotted. A segment is ``segments[<segmentId>]`` and any
other list item ``[<index>]``; a path in an ``Additive`` list is the item's
position in the view (the edge's entries, then the profile's).

How each part of a module is matched:

- keys by key; a key either side lacks is reported once, at the highest path
  that is absent; under a display default (``FACTORY``) a key the server lacks
  is ``factory``, and an empty list the view builds itself (an ``Additive``
  list, ``segments``) equals an absent one while a stored one does not;
- the items of a keyed list of ``Layered`` models (the ``segments``) by their
  key, the alt key first, in any order: the view puts the profile's segments
  first and the composer the edge's;
- the items of any other list by position;
- a scalar by value, where ``True`` is not ``1``.

The keys the composer adds to a firewall rule that names a security service
group are expected from the enterprise's security objects
(``composed_security``) when ``compare_module`` is given them, and compared like
the rule's own keys.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from typing import Annotated, Any, cast, get_args, get_origin

from pydantic import MISSING
from pydantic.fields import FieldInfo

from pyvelo.config.composed_security import (
    UNRESOLVED,
    SecurityObjects,
    expected_rule_keys,
    rules_naming_a_group,
)
from pyvelo.core import Origin, Resolved, effective_dump
from pyvelo.core.base import JsonObject, VcoModel, is_dict, is_list, wire_name
from pyvelo.core.fields import get_key_spec, is_secret
from pyvelo.core.layers import Effective, Kind, Layered, field_kind
from pyvelo.core.rebase import KeyValue, key_text

__all__ = [
    "CONVERTED_MODULES",
    "EFFECTIVE_IGNORES",
    "FINDINGS",
    "UNMODELED_KEYS",
    "EffectiveCategory",
    "EffectiveComparison",
    "EffectiveDifference",
    "EffectiveIgnore",
    "compare_module",
    "module_attribute",
]

OMITTED_SEGMENT = (
    "the composer keeps only the segments in use on the edge "
    "(VLANs, routed interfaces, loopbacks and the Global Segment)"
)
SECRET_NOT_COMPARED = "a secret value is not compared"  # noqa: S105 (a finding's reason, not a password)
UNCONVERTED_FIELD = "the library does not model this yet, so the view has no value"
UNMODELED_KEY = "the library does not know this key yet, so the view has no value"
FACTORY_ABSENT = "the view gives the display default and the server has no value"
FACTORY_SAME = "the view gives the display default and the server has the same value"

_HIDDEN = "***"
_LONGEST_VALUE = 80


class EffectiveCategory(StrEnum):
    """What a difference between the view and the server's composed module is."""

    DIFFERS = "differs"
    """Both sides have a value and they differ. A finding."""
    CLIENT_ONLY = "client_only"
    """The view gives an ``EDGE`` or ``PROFILE`` value the server's answer lacks. A finding."""
    SERVER_ONLY = "server_only"
    """The server's answer has a value the view lacks. A finding."""
    FACTORY = "factory"
    """The view gives a display default where the server has no value, or the same one. Information."""
    IGNORED = "ignored"
    """The path is on the ignore list, is a secret, or is a segment the composer left out. Reported with its reason."""
    UNCONVERTED = "unconverted"
    """The path is not converted yet, so the view has no value to compare. Information."""


FINDINGS: frozenset[EffectiveCategory] = frozenset(
    {
        EffectiveCategory.DIFFERS,
        EffectiveCategory.CLIENT_ONLY,
        EffectiveCategory.SERVER_ONLY,
    }
)
"""The categories that say the view and the VCO disagree."""

CONVERTED_MODULES: tuple[str, ...] = ("firewall", "QOS")
"""The modules whose effective view exists: what ``compare_effective`` compares when it is given none."""

UNMODELED_KEYS: Mapping[str, frozenset[str]] = {
    "firewall": frozenset({"etm"}),
    "QOS": frozenset(),
}
"""Keys of a module that no model declares, as paths (``[*]`` for any one list item): reported as ``unconverted`` and not as ``server_only``."""


def module_attribute(name: str) -> str:
    """The attribute of ``EffectiveView`` and of the VCO's composed-modules answer that holds the module *name* (a wire name): ``QOS`` is ``qos``, any other is its own."""
    return "qos" if name == "QOS" else name


@dataclass(frozen=True)
class EffectiveIgnore:
    """A divergence that is known and understood: its module, the path it applies to, and why.

    *path* is matched against whole parts of a wire path (a key, or a
    ``[...]`` list item), from its start: ``segments[*].outbound[0]`` matches
    that path and everything below it. ``*`` matches any one part and ``[*]``
    any one list item.
    """

    module: str
    path: str
    reason: str


EFFECTIVE_IGNORES: tuple[EffectiveIgnore, ...] = ()
"""The known divergences, each with its reason. An entry is added only after the divergence is understood, so that none is hidden."""


@dataclass(frozen=True)
class EffectiveDifference:
    """One difference between the view and the server's composed module.

    *client* and *server* are the two values as JSON, ``MISSING`` where a side
    has none; a secret is shown as ``***``. *origin* and *source* are the
    view's (``source`` is where the value sits in the layer it came from), and
    ``None`` where the view's value is not a single resolved one. *reason*
    says why a path is ``ignored``, ``unconverted`` or ``factory``.
    """

    module: str
    path: str
    client: object
    server: object
    origin: Origin | None
    source: str | None
    category: EffectiveCategory
    reason: str | None

    def render(self) -> str:
        """The entry as one line."""
        line = (
            f"{self.category.value} {self.path}: "
            f"client {_show(self.client)}, server {_show(self.server)}"
        )
        notes: list[str] = []
        if self.origin is not None:
            notes.append(f"{self.origin.value} {self.source}")
        if self.reason is not None:
            notes.append(self.reason)
        return f"{line} [{'; '.join(notes)}]" if notes else line


@dataclass(frozen=True)
class EffectiveComparison:
    """The differences between the effective view and the VCO's composed modules."""

    entries: tuple[EffectiveDifference, ...]

    @property
    def findings(self) -> tuple[EffectiveDifference, ...]:
        """The entries that say the view and the VCO disagree (``FINDINGS``)."""
        return tuple(e for e in self.entries if e.category in FINDINGS)

    def render(self) -> str:
        """Text by module and path: a header line per module, then a line per entry."""
        if not self.entries:
            return "no differences"
        lines: list[str] = []
        for module in sorted({e.module for e in self.entries}):
            lines.append(module)
            lines.extend(
                f"  {e.render()}"
                for e in sorted(
                    (e for e in self.entries if e.module == module),
                    key=lambda e: e.path,
                )
            )
        return "\n".join(lines)


def _show(value: object) -> str:
    if value is MISSING:
        return "(none)"
    text = json.dumps(value, sort_keys=True, default=str)
    if len(text) > _LONGEST_VALUE:
        return f"{text[: _LONGEST_VALUE - 3]}..."
    return text


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


def compare_module(
    module: str,
    view: Layered[Effective],
    server: JsonObject,
    *,
    ignores: Sequence[EffectiveIgnore],
    security_objects: SecurityObjects | None = None,
) -> list[EffectiveDifference]:
    """The differences between *view* and *server*, the raw data the VCO composed for *module*.

    The ignores of other modules are not applied. See the module's text for how
    the two are matched.

    With *security_objects*, each rule of the view that names a security
    service group also has the keys the composer adds to it
    (``composed_security``), expected from the objects the group names: they
    are compared like the rule's own keys, with the rule's origin and a source
    of its ``securityServiceGroup``. A key that cannot be expected because the
    group, or an object it names, is not among the objects is reported with
    that reason. Without *security_objects* those keys are not expected, and a
    rule with a group has them as ``server_only``.
    """
    client, resolved = effective_dump(view)
    reasons: dict[str, str] = {}
    if security_objects is not None:
        resolved = dict(resolved)
        _expect_security_keys(client, resolved, reasons, security_objects)
    run = _Comparison(module, ignores, resolved, reasons)
    run.model(view, client, server, "")
    return run.entries


def _expect_security_keys(
    client: JsonObject,
    resolved: dict[str, Resolved[object]],
    reasons: dict[str, str],
    objects: SecurityObjects,
) -> None:
    """Add the keys the composer adds to each rule of *client* that names a group, expected from *objects*.

    The expected values go in the dump, and a ``Resolved`` for each in
    *resolved* (the rule's origin, a source of its ``securityServiceGroup``).
    Where something cannot be expected, the rule's other keys get a reason in
    *reasons*.
    """
    for named in rules_naming_a_group(client):
        rule = resolved.get(named.path)
        if rule is None:
            continue
        expected = expected_rule_keys(named.group, objects)
        for key, value in expected.values.items():
            if key in named.rule:
                continue
            where = _join(named.path, key)
            named.rule[key] = value
            resolved[where] = Resolved(
                rule.origin, value, _join(rule.source, "securityServiceGroup")
            )
        for key in expected.unresolved:
            reasons[_join(named.path, key)] = UNRESOLVED


_PART = re.compile(r"[^.\[\]]+|\[[^\]]*\]")


def _parts(path: str) -> list[str]:
    return _PART.findall(path)


def _matches(pattern: list[str], path: list[str]) -> bool:
    return len(pattern) <= len(path) and all(
        want in (have, "*") or (want == "[*]" and have.startswith("["))
        for want, have in zip(pattern, path, strict=False)
    )


def _join(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else key


def _same(client: object, server: object) -> bool:
    """Equal as JSON: a number is a number and ``True`` is not ``1``."""
    if isinstance(client, bool) or isinstance(server, bool):
        return type(client) is type(server) and client == server
    if isinstance(client, int | float) and isinstance(server, int | float):
        return client == server
    return type(client) is type(server) and client == server


def _built_empty(client: object, server: object) -> bool:
    """The view holds an empty list it built itself and the server has none: no difference.

    Only where the view materialises the list (an ``Additive`` list, which is
    always there, and ``segments``); a stored empty list the server lacks is
    ``client_only``.
    """
    return client == [] and server is MISSING


def _model_of(annotation: object) -> type[VcoModel] | None:
    """The model a value of *annotation* is, or its items are, or its non-``None`` variant is; ``None`` if it is plain."""
    if isinstance(annotation, type):
        return annotation if issubclass(annotation, VcoModel) else None
    origin = get_origin(annotation)
    args: tuple[object, ...] = get_args(annotation)
    if origin is Annotated or origin is list:
        return _model_of(args[0]) if args else None
    for arg in args:
        found = _model_of(arg)
        if found is not None:
            return found
    return None


@cache
def _wire_fields(model: type[VcoModel]) -> dict[str, FieldInfo]:
    return {wire_name(model, name): info for name, info in model.model_fields.items()}


def _wire_path(model: type[VcoModel], dotted: str) -> tuple[str, ...]:
    """The wire keys of the python path *dotted* from *model*."""
    keys: list[str] = []
    current: type[VcoModel] | None = model
    for name in dotted.split("."):
        if current is None:
            keys.append(name)
            continue
        keys.append(wire_name(current, name))
        current = _model_of(field_kind(current.model_fields[name]).inner_type)
    return tuple(keys)


def _dig(item: object, keys: tuple[str, ...]) -> object:
    for key in keys:
        if not is_dict(item):
            return MISSING
        item = cast("dict[str, object]", item).get(key, MISSING)
    return item


class _Comparison:
    """One run of ``compare_module``: the entries found so far and what it needs to attribute them."""

    def __init__(
        self,
        module: str,
        ignores: Sequence[EffectiveIgnore],
        resolved: Mapping[str, Resolved[object]],
        reasons: Mapping[str, str],
    ) -> None:
        self.module: str = module
        self.ignores: list[tuple[list[str], str]] = [
            (_parts(i.path), i.reason) for i in ignores if i.module == module
        ]
        self.resolved: Mapping[str, Resolved[object]] = resolved
        self.reasons: Mapping[str, str] = reasons
        self.entries: list[EffectiveDifference] = []

    def emit(  # noqa: PLR0913 (existing: split it when it next changes)
        self,
        path: str,
        category: EffectiveCategory,
        client: object,
        server: object,
        *,
        cover: Resolved[object] | None = None,
        reason: str | None = None,
    ) -> None:
        if reason is None:
            reason = self.reasons.get(path)
        self.entries.append(
            EffectiveDifference(
                self.module,
                path,
                client,
                server,
                None if cover is None else cover.origin,
                None if cover is None else cover.source,
                category,
                reason,
            )
        )

    def ignored(
        self,
        path: str,
        client: object,
        server: object,
        cover: Resolved[object] | None,
    ) -> bool:
        """Report *path* as ignored and return True when an ignore applies to it."""
        where = _parts(path)
        for pattern, reason in self.ignores:
            if _matches(pattern, where):
                self.emit(
                    path,
                    EffectiveCategory.IGNORED,
                    client,
                    server,
                    cover=cover,
                    reason=reason,
                )
                return True
        return False

    def secret(
        self,
        path: str,
        client: object,
        server: object,
        cover: Resolved[object] | None,
    ) -> None:
        """A secret is never compared, and never shown."""
        if client is MISSING and server is MISSING:
            return
        self.emit(
            path,
            EffectiveCategory.IGNORED,
            MISSING if client is MISSING else _HIDDEN,
            MISSING if server is MISSING else _HIDDEN,
            cover=cover,
            reason=SECRET_NOT_COMPARED,
        )

    def unmodeled(self, path: str) -> bool:
        """Whether *path* is a key of this module that no model declares (``UNMODELED_KEYS``)."""
        where = _parts(path)
        return any(
            len(pattern) == len(where) and _matches(pattern, where)
            for pattern in map(_parts, UNMODELED_KEYS.get(self.module, frozenset()))
        )

    def unconverted(self, path: str, server: object, reason: str) -> None:
        if not self.ignored(path, MISSING, server, None):
            self.emit(
                path,
                EffectiveCategory.UNCONVERTED,
                MISSING,
                server,
                reason=reason,
            )

    # -- a model -----------------------------------------------------------

    def model(
        self,
        view: Layered[Effective],
        client: Mapping[str, object],
        server: Mapping[str, object],
        path: str,
    ) -> None:
        """Compare the fields of *view* (its dump is *client*) with *server*, and then the keys of *server* that no field names."""
        cls = type(view)
        known: set[str] = set()
        for name, info in cls.model_fields.items():
            key = wire_name(cls, name)
            known.add(key)
            where = _join(path, key)
            found = client.get(key, MISSING)
            theirs = server.get(key, MISSING)
            kind = field_kind(info)
            if kind.kind is Kind.UNCONVERTED:
                if theirs is not MISSING:
                    self.unconverted(where, theirs, UNCONVERTED_FIELD)
            elif is_secret(info):
                self.secret(where, found, theirs, self.resolved.get(where))
            else:
                self.field(view, name, info, kind.kind, where, found, theirs)
        for key, theirs in server.items():
            if key in known:
                continue
            where = _join(path, key)
            if self.unmodeled(where):
                self.unconverted(where, theirs, UNMODELED_KEY)
            elif not self.ignored(where, MISSING, theirs, None):
                self.emit(where, EffectiveCategory.SERVER_ONLY, MISSING, theirs)

    def field(  # noqa: PLR0913, PLR0917 (existing: split it when it next changes)
        self,
        view: Layered[Effective],
        name: str,
        info: FieldInfo,
        kind: Kind,
        path: str,
        client: object,
        server: object,
    ) -> None:
        held = cast("object", getattr(view, name))
        inner = field_kind(info).inner_type
        spec = get_key_spec(info)
        if isinstance(held, Layered):
            self.child(cast("Layered[Effective]", held), path, client, server)
        elif spec is not None and (item_class := _layered_items(inner)) is not None:
            items = cast("list[Layered[Effective]]", held)
            self.segments(items, item_class, spec.alt, spec.paths, path, client, server)
        elif kind is Kind.ADDITIVE:
            self.additive(_model_of(inner), path, client, server)
        else:
            self.value(
                path,
                client,
                server,
                cover=self.resolved.get(path),
                typ=_model_of(inner),
            )

    def child(
        self, held: Layered[Effective], path: str, client: object, server: object
    ) -> None:
        if client is MISSING and server is MISSING:
            return
        if self.ignored(path, client, server, None):
            return
        if is_dict(client) and is_dict(server):
            self.model(
                held,
                cast("dict[str, object]", client),
                cast("dict[str, object]", server),
                path,
            )
        elif server is MISSING:
            self.emit(path, EffectiveCategory.CLIENT_ONLY, client, MISSING)
        elif client is MISSING:
            self.emit(path, EffectiveCategory.SERVER_ONLY, MISSING, server)
        else:
            self.emit(path, EffectiveCategory.DIFFERS, client, server)

    # -- the segments ------------------------------------------------------

    def segments(  # noqa: C901, PLR0912, PLR0913, PLR0917 (existing: split it when it next changes)
        self,
        items: list[Layered[Effective]],
        item_class: type[Layered[Any]],
        alt: str | None,
        key_paths: tuple[str, ...],
        path: str,
        client: object,
        server: object,
    ) -> None:
        """A keyed list of ``Layered`` models, matched by key in any order.

        *items* are the view's, in the dump's order, and a view item the server
        lacks is the composer's omission of a segment not in use.
        """
        if _built_empty(client, server) or (client is MISSING and server is MISSING):
            return
        if self.ignored(path, client, server, None):
            return
        if server is MISSING and is_list(client):
            # An answer with no list at all omitted every segment: each of the
            # view's is the composer's omission, not one finding for the list.
            server = []
        if not is_list(client) or not is_list(server):
            if server is MISSING:
                self.emit(path, EffectiveCategory.CLIENT_ONLY, client, MISSING)
            elif client is MISSING:
                self.emit(path, EffectiveCategory.SERVER_ONLY, MISSING, server)
            else:
                self.emit(path, EffectiveCategory.DIFFERS, client, server)
            return
        alt_keys = None if alt is None else _wire_path(item_class, alt)
        key_keys = [_wire_path(item_class, p) for p in key_paths]

        def identity(item: object) -> str | None:
            if alt_keys is not None:
                found = _dig(item, alt_keys)
                if found not in (None, MISSING):
                    return str(found)
            values = [_dig(item, keys) for keys in key_keys]
            if any(v in (None, MISSING) for v in values):
                return None
            return (
                str(values[0])
                if len(values) == 1
                else key_text(cast("KeyValue", tuple(values)))
            )

        waiting: dict[str, list[object]] = {}
        unnamed: list[tuple[int, object]] = []
        for position, item in enumerate(server):
            text = identity(item)
            if text is None:
                unnamed.append((position, item))
            else:
                waiting.setdefault(text, []).append(item)
        for position, item in enumerate(client):
            text = identity(item) or str(position)
            where = f"{path}[{text}]"
            counterpart = waiting.get(text)
            if not counterpart:
                if not self.ignored(where, item, MISSING, None):
                    self.emit(
                        where,
                        EffectiveCategory.IGNORED,
                        item,
                        MISSING,
                        reason=OMITTED_SEGMENT,
                    )
                continue
            theirs = counterpart.pop(0)
            if (
                not self.ignored(where, item, theirs, None)
                and is_dict(item)
                and is_dict(theirs)
            ):
                self.model(
                    items[position],
                    cast("dict[str, object]", item),
                    cast("dict[str, object]", theirs),
                    where,
                )
        for text, leftovers in waiting.items():
            for item in leftovers:
                where = f"{path}[{text}]"
                if not self.ignored(where, MISSING, item, None):
                    self.emit(where, EffectiveCategory.SERVER_ONLY, MISSING, item)
        for position, item in unnamed:
            where = f"{path}[{position}]"
            if not self.ignored(where, MISSING, item, None):
                self.emit(where, EffectiveCategory.SERVER_ONLY, MISSING, item)

    # -- an additive list ----------------------------------------------------

    def additive(
        self, typ: type[VcoModel] | None, path: str, client: object, server: object
    ) -> None:
        """The entries of an ``Additive`` list, by position."""
        if _built_empty(client, server) or (client is MISSING and server is MISSING):
            return
        if self.ignored(path, client, server, None):
            return
        if not is_list(client) or not is_list(server):
            if server is MISSING:
                self.emit(path, EffectiveCategory.CLIENT_ONLY, client, MISSING)
            elif client is MISSING:
                self.emit(path, EffectiveCategory.SERVER_ONLY, MISSING, server)
            else:
                self.emit(path, EffectiveCategory.DIFFERS, client, server)
            return
        mine, theirs = client, server
        for position in range(max(len(mine), len(theirs))):
            self.value(
                f"{path}[{position}]",
                mine[position] if position < len(mine) else MISSING,
                theirs[position] if position < len(theirs) else MISSING,
                cover=self.resolved.get(f"{path}[{position}]"),
                typ=typ,
            )

    # -- a value -----------------------------------------------------------

    def value(
        self,
        path: str,
        client: object,
        server: object,
        *,
        cover: Resolved[object] | None,
        typ: type[VcoModel] | None,
    ) -> None:
        """A resolved value, or a plain one: *cover* says where the view's came from."""
        if client is MISSING and server is MISSING:
            return
        if self.ignored(path, client, server, cover):
            return
        display = cover is not None and cover.origin is Origin.FACTORY
        if server is MISSING:
            self.absent_from_server(path, client, cover)
        elif client is MISSING:
            self.emit(path, EffectiveCategory.SERVER_ONLY, MISSING, server, cover=cover)
        else:
            before = len(self.entries)
            self.deep(path, client, server, cover, typ)
            if display and len(self.entries) == before:
                self.emit(
                    path,
                    EffectiveCategory.FACTORY,
                    client,
                    server,
                    cover=cover,
                    reason=FACTORY_SAME,
                )

    def absent_from_server(
        self, path: str, client: object, cover: Resolved[object] | None
    ) -> None:
        """The view has a value the server lacks: information under a display default, else a finding."""
        display = cover is not None and cover.origin is Origin.FACTORY
        self.emit(
            path,
            EffectiveCategory.FACTORY if display else EffectiveCategory.CLIENT_ONLY,
            client,
            MISSING,
            cover=cover,
            reason=FACTORY_ABSENT if display else None,
        )

    def deep(  # noqa: C901, PLR0912 (existing: split it when it next changes)
        self,
        path: str,
        client: object,
        server: object,
        cover: Resolved[object] | None,
        typ: type[VcoModel] | None,
    ) -> None:
        """Both sides have a value: compare them down to the leaves, finding a secret by the model's field.

        A key with a ``Resolved`` of its own (a key expected from the security
        objects) is attributed to it, and what is below it too.
        """
        if is_dict(client) and is_dict(server):
            mine = cast("dict[str, object]", client)
            theirs = cast("dict[str, object]", server)
            fields = {} if typ is None else _wire_fields(typ)
            for key in (*mine, *(k for k in theirs if k not in mine)):
                where = _join(path, key)
                here = self.resolved.get(where, cover)
                found = mine.get(key, MISSING)
                other = theirs.get(key, MISSING)
                if self.ignored(where, found, other, here):
                    continue
                info = fields.get(key)
                if info is not None and is_secret(info):
                    self.secret(where, found, other, here)
                elif found is MISSING and self.unmodeled(where):
                    self.unconverted(where, other, UNMODELED_KEY)
                elif found is MISSING:
                    self.emit(
                        where,
                        EffectiveCategory.SERVER_ONLY,
                        MISSING,
                        other,
                        cover=here,
                    )
                elif other is MISSING:
                    self.absent_from_server(where, found, here)
                else:
                    nested = (
                        None if info is None else _model_of(field_kind(info).inner_type)
                    )
                    self.deep(where, found, other, here, nested)
        elif is_list(client) and is_list(server):
            mine, theirs = client, server
            for position in range(max(len(mine), len(theirs))):
                where = f"{path}[{position}]"
                if position >= len(theirs):
                    self.absent_from_server(where, mine[position], cover)
                elif position >= len(mine):
                    self.emit(
                        where,
                        EffectiveCategory.SERVER_ONLY,
                        MISSING,
                        theirs[position],
                        cover=cover,
                    )
                else:
                    self.deep(where, mine[position], theirs[position], cover, typ)
        elif not _same(client, server):
            self.emit(path, EffectiveCategory.DIFFERS, client, server, cover=cover)


def _layered_items(inner: object) -> type[Layered[Any]] | None:
    """The item class of a ``list[...]`` of ``Layered`` models, else ``None``."""
    if get_origin(inner) is not list:
        return None
    args = get_args(inner)
    if args and isinstance(args[0], type) and issubclass(args[0], Layered):
        return cast("type[Layered[Any]]", args[0])
    return None
