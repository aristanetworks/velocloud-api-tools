"""The factory content of a new segment of a profile, as the Orchestrator UI writes it.

``new_segment_templates.json`` ships beside this module. It holds, for device
settings, QoS and firewall, the entry a profile gets when a segment is
assigned to it, and the parts of each module that belong to no segment. It has
no segment descriptor and no ``ruleLogicalId``: both are added when an entry is
made. A QoS rule takes the ID here. A firewall rule takes none, the VCO issues
it.

Every function returns a copy, so a caller can change what it gets.
"""

import copy
import json
import uuid
from collections.abc import Mapping, Sequence
from functools import cache
from importlib.resources import files
from typing import Any, Final

from pyvelo.core.identity import SegmentIdentity

TEMPLATE_RESOURCE: Final = "new_segment_templates.json"
DEFAULT_NAMES: Final = (
    "Default-Internet-UDP",
    "Default-Internet-Other",
    "Default-Any-UDP",
    "Default-Any-Other",
)

# A module's name on the wire, and its key in the template file.
_TEMPLATE_KEYS: Final = {
    "deviceSettings": "deviceSettings",
    "QOS": "qos",
    "firewall": "firewall",
}


@cache
def _template() -> dict[str, Any]:
    """The parsed template file, read once. Callers copy what they take from it."""
    text = files(__package__).joinpath(TEMPLATE_RESOURCE).read_text(encoding="utf-8")
    return json.loads(text)


def segment_template(module_name: str) -> dict[str, Any]:
    """A copy of the template's segment entry for *module_name*, without a descriptor and without rule IDs.

    *module_name* is a module's name on the wire: ``"deviceSettings"``,
    ``"QOS"`` or ``"firewall"``. Any other name raises ``ValueError``.
    """
    key = _TEMPLATE_KEYS.get(module_name)
    if key is None:
        raise ValueError(
            f"A new segment has a template for {', '.join(map(repr, _TEMPLATE_KEYS))} "
            f"only, not for {module_name!r}."
        )
    return copy.deepcopy(_template()[key]["segment"])


def default_rules() -> list[dict[str, Any]]:
    """A copy of the template's four QoS ``defaults``, without IDs, in ``DEFAULT_NAMES`` order."""
    return copy.deepcopy(_template()["qos"]["segment"]["defaults"])


def descriptor_of(identity: SegmentIdentity) -> dict[str, object]:
    """The ``segment`` descriptor of an entry for *identity*, as the wire has it."""
    return {
        "segmentId": identity.segment_id,
        "name": identity.name,
        "type": identity.type_.value,
        "segmentLogicalId": str(identity.logical_id),
    }


def new_segment_entry(
    module_name: str,
    identity: SegmentIdentity,
    *,
    qos_rules: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, Any]:
    """The entry of segment *identity* in module *module_name*: its descriptor, then the template's entry.

    For QoS, every rule of ``rules`` and of ``defaults`` gets a new
    ``ruleLogicalId``. With *qos_rules*, ``rules`` is a deep copy of them
    instead of the template's, and each keeps its own ``ruleLogicalId``. A
    firewall rule gets none.
    """
    entry: dict[str, Any] = {
        "segment": descriptor_of(identity),
        **segment_template(module_name),
    }
    if module_name == "QOS":
        if qos_rules is not None:
            entry["rules"] = copy.deepcopy([dict(rule) for rule in qos_rules])
        else:
            _give_ids(entry["rules"])
        _give_ids(entry["defaults"])
    return entry


def _give_ids(rules: list[dict[str, Any]]) -> None:
    for rule in rules:
        rule["ruleLogicalId"] = str(uuid.uuid4())
