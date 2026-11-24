"""Builders for the tests of profile segments: descriptors, entries of the three modules, profile rows and registries.

``tests/support`` is on pytest's ``pythonpath`` and basedpyright's
``extraPaths``, so the tests of both packages import this as ``segment_helpers``.
"""

import copy
import uuid
from typing import Any, Final

import refs_helpers
from pydantic import MISSING
from pyvelo.client.models.configuration.profile_segments import (
    default_rules,
    new_segment_entry,
    segment_template,
)
from pyvelo.config.resolver import ResolverRegistry
from pyvelo.core.identity import SegmentIdentity
from pyvelo.testing import make_module_json
from refs_helpers import CONFIG, DS_ID, QOS_ID

FW_ID = 12

# (segment_id, name, logical_id, object_id)
type Segment = tuple[int, str, str, int]

GLOBAL: Final[Segment] = (
    0,
    "Global Segment",
    refs_helpers.SEG0,
    refs_helpers.SEG0_OBJECT,
)
GUEST: Final[Segment] = (2, "Guest", refs_helpers.SEG1, refs_helpers.SEG1_OBJECT)
VOICE: Final[Segment] = (4, "Voice", "44444444-4444-4444-8444-444444444444", 504)

_OBJECT_IDS: Final = {s[0]: s[3] for s in (GLOBAL, GUEST, VOICE)}


def _template_defaults() -> list[dict[str, Any]]:
    defaults = default_rules()
    for rule in defaults:
        rule["ruleLogicalId"] = str(uuid.uuid4())
    return defaults


_FACTORY_DEFAULTS: Final = _template_defaults()


def identity(segment: Segment) -> SegmentIdentity:
    """The identity of *segment*, its logical ID as a ``uuid.UUID``."""
    segment_id, name, logical_id, object_id = segment
    return SegmentIdentity(
        name=name,
        segment_id=segment_id,
        object_id=object_id,
        logical_id=uuid.UUID(logical_id),
    )


def descriptor(segment: Segment) -> dict[str, Any]:
    """The ``segment`` descriptor of *segment*, as the wire has it."""
    segment_id, name, logical_id, _ = segment
    return {
        "segmentId": segment_id,
        "name": name,
        "type": "REGULAR",
        "segmentLogicalId": logical_id,
    }


def qos_entry(
    segment: Segment,
    *,
    defaults: list[dict[str, Any]] | None | MISSING = _FACTORY_DEFAULTS,
    rules: list[dict[str, Any]] = [],  # noqa: B006 -- copied, never changed
) -> dict[str, Any]:
    """A QoS segment entry for *segment*: the template's parts, the given *rules* and *defaults*.

    *defaults* is the template's four, with IDs, unless given: ``None`` puts
    ``"defaults": null`` in the entry and ``MISSING`` leaves the key out.
    """
    entry = new_segment_entry("QOS", identity(segment), qos_rules=rules)
    if defaults is MISSING:
        del entry["defaults"]
    elif defaults is _FACTORY_DEFAULTS:
        entry["defaults"] = copy.deepcopy(_FACTORY_DEFAULTS)
    else:
        entry["defaults"] = copy.deepcopy(defaults)
    return entry


def firewall_entry(segment: Segment) -> dict[str, Any]:
    """A firewall segment entry for *segment*: one ``AllowAny`` rule, with a ``ruleLogicalId``."""
    entry = new_segment_entry("firewall", identity(segment))
    (rule,) = entry["outbound"]
    rule["ruleLogicalId"] = str(uuid.uuid4())
    return entry


def ds_entry(segment: Segment) -> dict[str, Any]:
    """A device settings segment entry for *segment*: the template's ``ospf`` and ``syslog``, and ``vpn``."""
    template = segment_template("deviceSettings")
    return {
        "segment": descriptor(segment),
        "ospf": template["ospf"],
        "syslog": template["syslog"],
        "vpn": {"conditionalBackhaul": False},
    }


_ONE_ROW_PER_ENTRY: Final[dict[str, Any]] = {}


def profile_rows(
    ds: list[dict[str, Any]],
    qos: list[dict[str, Any]],
    firewall: list[dict[str, Any]],
    *,
    ds_refs: dict[str, Any] | MISSING = _ONE_ROW_PER_ENTRY,
    qos_refs: dict[str, Any] | MISSING = MISSING,
    networks: list[dict[str, Any]] = [],  # noqa: B006 -- copied, never changed
) -> list[dict[str, Any]]:
    """The three module rows of a profile of configuration ``CONFIG``, with the given segment lists.

    The device settings module has *ds_refs*, by default one
    ``deviceSettings:segment`` row per entry of *ds* (of one of the three
    segments of this module); ``MISSING`` gives it no refs at all. The device
    settings ``lan.networks`` are *networks*.
    """
    if ds_refs is _ONE_ROW_PER_ENTRY:
        # A ref held by one row is a bare row, by several a list, as the VCO sends it.
        rows = [
            refs_helpers.segment_row(
                e["segment"]["segmentId"],
                _OBJECT_IDS[e["segment"]["segmentId"]],
                e["segment"]["segmentLogicalId"],
            )
            for e in ds
        ]
        refs: Any = (
            {refs_helpers.SEGMENT: rows[0] if len(rows) == 1 else rows} if rows else {}
        )
    else:
        refs = ds_refs
    return [
        make_module_json(
            "deviceSettings",
            {
                "lan": {"networks": copy.deepcopy(networks), "interfaces": []},
                "segments": copy.deepcopy(ds),
            },
            module_id=DS_ID,
            configuration_id=CONFIG,
            refs=copy.deepcopy(refs),
        ),
        make_module_json(
            "QOS",
            {"segments": copy.deepcopy(qos)},
            module_id=QOS_ID,
            configuration_id=CONFIG,
            refs=copy.deepcopy(qos_refs),
        ),
        make_module_json(
            "firewall",
            {"segments": copy.deepcopy(firewall)},
            module_id=FW_ID,
            configuration_id=CONFIG,
        ),
    ]


async def segment_registry(*segments: Segment) -> ResolverRegistry:
    """A registry whose segment resolver is loaded with *segments*."""
    return await refs_helpers.table_registry(*(identity(s) for s in segments))
