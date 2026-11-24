"""The keys the composer adds to a firewall rule, expected from the security objects its group names.

A rule that names a security service group (``securityServiceGroup``, the
``logicalId`` of a ``securityServiceGroup`` security object) gets three keys in
the VCO's composed module that neither layer stores:

- ``urlCategoryFiltering`` is the ``data`` of the ``urlCategoryFiltering``
  object the group names, and ``urlReputationFiltering`` that of the
  ``urlReputationFiltering`` object it names;
- ``atp_action`` is built from the ``idps`` object the group names:
  ``atp_enabled`` is true, and ``ids_enabled``, ``ips_enabled`` and
  ``atp_logging_enabled`` are the object's ``idsEnabled``, ``ipsEnabled`` and
  ``logEnabled``.

A group names an object of a kind by ``{"id": <the object's id>}`` under that
kind's name in its ``data``. A group that names no object of a kind expects no
key for it. What the composer adds for a ``maliciousIpFiltering`` object is
not known, so none is expected for it.
"""

import copy
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import cast

from pyvelo.client.enums.enterprise import SecurityObjectType
from pyvelo.core.base import JsonObject, is_dict, is_list

__all__ = [
    "SECURITY_OBJECT_TYPES",
    "SYNTHETIC_RULE_KEYS",
    "UNRESOLVED",
    "ExpectedKeys",
    "RuleGroup",
    "SecurityObjects",
    "expected_rule_keys",
    "rules_naming_a_group",
]

SYNTHETIC_RULE_KEYS: tuple[str, ...] = (
    "atp_action",
    "urlCategoryFiltering",
    "urlReputationFiltering",
)
"""The keys the composer adds to a rule that names a security service group."""

SECURITY_OBJECT_TYPES: tuple[SecurityObjectType, ...] = (
    SecurityObjectType.SECURITY_SERVICE_GROUP,
    SecurityObjectType.URL_CATEGORY_FILTERING,
    SecurityObjectType.URL_REPUTATION_FILTERING,
    SecurityObjectType.IDPS,
)
"""The types of security object read to expect those keys."""

UNRESOLVED = (
    "the rule's security service group, or an object it names, "
    + "is not among the enterprise's security objects"
)

_ATP_FLAGS: tuple[tuple[str, str], ...] = (
    ("ids_enabled", "idsEnabled"),
    ("ips_enabled", "ipsEnabled"),
    ("atp_logging_enabled", "logEnabled"),
)


def _same_id(logical_id: object) -> str:
    """*logical_id* in one spelling, so that two UUIDs differing in case are the same."""
    text = str(logical_id)
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return text.lower()


def _object(value: object) -> JsonObject | None:
    return cast("JsonObject", value) if is_dict(value) else None


@dataclass(frozen=True)
class SecurityObjects:
    """The enterprise's security objects, as rows the VCO returns them (``type``, ``id``, ``logicalId`` and ``data``)."""

    groups: Mapping[str, Mapping[str, object]]
    """The ``securityServiceGroup`` rows by ``logicalId`` (as ``_same_id`` spells it)."""
    by_id: Mapping[tuple[str, int], Mapping[str, object]]
    """The rows by type and ``id``."""

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping[str, object]]) -> "SecurityObjects":
        """The objects of *rows*; a row with no usable ``type`` and ``id`` is left out."""
        groups: dict[str, Mapping[str, object]] = {}
        by_id: dict[tuple[str, int], Mapping[str, object]] = {}
        for row in rows:
            type_ = row.get("type")
            id_ = row.get("id")
            if not isinstance(type_, str) or not isinstance(id_, int):
                continue
            by_id[type_, id_] = row
            logical_id = row.get("logicalId")
            if type_ == SecurityObjectType.SECURITY_SERVICE_GROUP and logical_id:
                groups[_same_id(logical_id)] = row
        return cls(groups, by_id)

    def group(self, logical_id: str) -> Mapping[str, object] | None:
        """The security service group with *logical_id* (in any case), or ``None``."""
        return self.groups.get(_same_id(logical_id))

    def named(self, type_: str, id_: int) -> Mapping[str, object] | None:
        """The object of *type_* with *id_*, or ``None``."""
        return self.by_id.get((type_, id_))


@dataclass(frozen=True)
class ExpectedKeys:
    """What the composer adds to a rule: the keys with their values, and why some cannot be expected."""

    values: Mapping[str, object]
    """The expected value of each of ``SYNTHETIC_RULE_KEYS`` the group gives the rule."""
    unresolved: str | None
    """``UNRESOLVED`` when the group, or an object it names, is not among the security objects."""


def expected_rule_keys(group_logical_id: str, objects: SecurityObjects) -> ExpectedKeys:
    """The keys the composer adds to a rule that names the group *group_logical_id*."""
    group = objects.group(group_logical_id)
    if group is None:
        return ExpectedKeys({}, UNRESOLVED)
    named = _object(group.get("data")) or {}
    values: dict[str, object] = {}
    unresolved = False

    for key, type_ in (
        ("urlCategoryFiltering", SecurityObjectType.URL_CATEGORY_FILTERING),
        ("urlReputationFiltering", SecurityObjectType.URL_REPUTATION_FILTERING),
        ("atp_action", SecurityObjectType.IDPS),
    ):
        reference = _object(named.get(type_.value))
        if reference is None:
            continue
        found = _lookup(objects, type_, reference.get("id"))
        if found is None:
            unresolved = True
        elif key == "atp_action":
            values[key] = _atp_action(found)
        else:
            values[key] = copy.deepcopy(found)
    return ExpectedKeys(values, UNRESOLVED if unresolved else None)


def _lookup(
    objects: SecurityObjects, type_: SecurityObjectType, id_: object
) -> JsonObject | None:
    """The ``data`` of the object of *type_* with *id_*, or ``None`` when there is none."""
    if not isinstance(id_, int):
        return None
    row = objects.named(type_.value, id_)
    return None if row is None else _object(row.get("data"))


def _atp_action(idps: JsonObject) -> JsonObject:
    action: JsonObject = {"atp_enabled": True}
    for key, flag in _ATP_FLAGS:
        if flag in idps:
            action[key] = idps[flag]
    return action


@dataclass(frozen=True)
class RuleGroup:
    """An outbound rule of a dump that names a security service group."""

    path: str
    """The rule's path in the view (``segments[<segmentId>].outbound[<index>]``)."""
    rule: JsonObject
    """The rule as the dump holds it."""
    group: str
    """The group's ``logicalId``."""


def rules_naming_a_group(dump: JsonObject) -> list[RuleGroup]:
    """The outbound rules in the firewall module dump *dump* that name a security service group."""
    found: list[RuleGroup] = []
    segments = dump.get("segments")
    for position, entry in enumerate(segments if is_list(segments) else []):
        segment = _object(entry)
        if segment is None:
            continue
        descriptor = _object(segment.get("segment")) or {}
        text = descriptor.get("segmentId", position)
        outbound = segment.get("outbound")
        for index, item in enumerate(outbound if is_list(outbound) else []):
            rule = _object(item)
            group = None if rule is None else rule.get("securityServiceGroup")
            if rule is not None and isinstance(group, str) and group:
                found.append(
                    RuleGroup(f"segments[{text}].outbound[{index}]", rule, group)
                )
    return found
