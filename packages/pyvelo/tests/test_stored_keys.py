"""Fields are read under the key the VCO stores them at.

Each document here is shaped as a VCO stores it. A field holds the stored
value, a plan with no edit sends nothing and holds the bytes read, and an edit
is written at the stored key.
"""

import copy
import json
from typing import Any

from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import (
    ConfigurationModuleBase,
    WanConfigurationModule,
)
from pyvelo.core import ConfigContext
from pyvelo.testing import make_module_json

from ._type_support import require_present

EDGE, PROFILE = ConfigContext.EDGE, ConfigContext.PROFILE


def u(n: int) -> str:
    return f"00000000-0000-0000-0000-{n:012x}"


def both_ways[M: ConfigurationModuleBase](
    cls: type[M], name: str, raw: dict[str, Any]
) -> tuple[M, M]:
    """*raw* as a module row, parsed from python objects and from JSON text."""
    row = make_module_json(name, copy.deepcopy(raw))
    return cls.model_validate(copy.deepcopy(row)), cls.model_validate_json(
        json.dumps(row)
    )


def assert_untouched(
    module: ConfigurationModuleBase, raw: dict[str, Any], level: ConfigContext
) -> None:
    plan = plan_module(module, level=level)
    assert plan.operation == "none"
    assert plan.data == raw
    assert module.raw_data == raw


# --- WAN ---------------------------------------------------------------------

LINK_ID = u(0xA1)


def cos_entry(n: int, name: str, percent: int, sla: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": u(n),
        "name": name,
        "dscpTags": ["EF"],
        "bandwidthPct": percent,
        "bandwidthGuaranteed": False,
        "defaultClassOfService": False,
        "policing": False,
        "staticSLA": sla,
    }


# A class's static SLA is stored as numbers by some writers and as text by others.
SLA_NUMBERS: dict[str, Any] = {"latencyMs": 400, "jitterMs": 15, "lossPct": 10}
SLA_TEXT: dict[str, Any] = {"latencyMs": "0", "jitterMs": "0", "lossPct": "0.0"}
WAN: dict[str, Any] = {
    "links": [
        {
            "internalId": LINK_ID,
            "logicalId": "00:00:00:00:00:01:0000",
            "name": "ISP A",
            "type": "WIRED",
            "mode": "PUBLIC",
            "discovery": "USER_DEFINED",
            "addressingVersion": "IPv4",
            "classesofServiceEnabled": True,
            "staticSLA": {"latencyMs": 0, "jitterMs": 0, "lossPct": 0},
            "classesOfService": {
                "classId": None,
                "classesOfService": [
                    cos_entry(1, "voice", 30, SLA_NUMBERS),
                    cos_entry(2, "rest", 70, SLA_TEXT),
                ],
            },
        }
    ],
    "networks": [],
}


def wan(raw: dict[str, Any] = WAN) -> WanConfigurationModule:
    return both_ways(WanConfigurationModule, "WAN", raw)[0]


def test_a_wan_link_is_read_at_the_keys_the_vco_stores() -> None:
    for module in both_ways(WanConfigurationModule, "WAN", WAN):
        link = require_present(module.data.links)[0]
        assert link.class_of_service_enabled is True
        voice, rest = link.classes_of_service.classes_of_service
        assert (voice.bandwidth_percent, rest.bandwidth_percent) == (30, 70)
        assert type(voice.bandwidth_percent) is int
        sla = voice.static_sla
        assert (sla.latency_ms, sla.jitter_ms, sla.loss_pct) == (400, 15, 10)
        sla = rest.static_sla
        assert (sla.latency_ms, sla.jitter_ms, sla.loss_pct) == ("0", "0", "0.0")
        assert_untouched(module, WAN, EDGE)


def test_a_link_s_static_sla_holds_the_numbers_a_vco_stores() -> None:
    raw = copy.deepcopy(WAN)
    raw["links"][0]["staticSLA"] = {"latencyMs": 10, "jitterMs": 20, "lossPct": 0.1}
    for module in both_ways(WanConfigurationModule, "WAN", raw):
        sla = require_present(module.data.links)[0].static_sla
        assert (sla.latency_ms, sla.jitter_ms, sla.loss_pct) == (10, 20, 0.1)
        assert_untouched(module, raw, EDGE)


def test_turning_class_of_service_off_is_written_at_the_stored_key() -> None:
    module = wan()
    require_present(module.data.links)[0].class_of_service_enabled = False
    plan = plan_module(module, level=EDGE)
    expected = copy.deepcopy(WAN)
    expected["links"][0]["classesofServiceEnabled"] = False
    assert plan.data == expected
    assert [str(c.path) for c in plan.changes] == [
        f"links[{LINK_ID},IPv4].classesofServiceEnabled"
    ]


def test_an_edit_to_one_class_keeps_what_every_class_stores() -> None:
    module = wan()
    link = require_present(module.data.links)[0]
    link.classes_of_service.classes_of_service[0].policing = True
    sent = plan_module(module, level=EDGE).data["links"][0]["classesOfService"]
    stored = WAN["links"][0]["classesOfService"]["classesOfService"]
    # The list has no key, so it is written whole from the models.
    kept = ("id", "bandwidthPct", "staticSLA")
    assert [[e[k] for k in kept] for e in sent["classesOfService"]] == [
        [e[k] for k in kept] for e in stored
    ]
    assert [e["policing"] for e in sent["classesOfService"]] == [True, False]
    assert not any(
        "bandwidth_pct" in e or "staticSla" in e for e in sent["classesOfService"]
    )


def test_a_link_that_also_stores_the_key_this_library_wrote_keeps_it_as_read() -> None:
    raw = copy.deepcopy(WAN)
    raw["links"][0]["classesOfServiceEnabled"] = False  # beside the stored key
    for module in both_ways(WanConfigurationModule, "WAN", raw):
        assert require_present(module.data.links)[0].class_of_service_enabled is True
        assert_untouched(module, raw, EDGE)
    module = wan(raw)
    require_present(module.data.links)[0].description = "uplink"
    expected = copy.deepcopy(raw)
    expected["links"][0]["description"] = "uplink"
    assert plan_module(module, level=EDGE).data == expected
