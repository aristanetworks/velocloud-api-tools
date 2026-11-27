"""The QoS module's kinds as declared and the display defaults it reads.

And what each layer holds.
"""

import copy
import json
from typing import Any, cast

import pytest
from pydantic import MISSING
from pyvelo.client.models.configuration import QosData
from pyvelo.client.models.configuration.qos import (
    CosMapping,
    QosRule,
    QosSegment,
    ServiceRateLimit,
)
from pyvelo.client.module_write import plan_module
from pyvelo.core import (
    ByFlag,
    ByPresence,
    ConfigContext,
    Default,
    Edge,
    Kind,
    PatchError,
    Profile,
    field_kind,
    wire_name,
)
from pyvelo.core.base import VcoModel
from pyvelo.core.effective import Origin, effective_dump, effective_view
from pyvelo.core.layers import Effective
from pyvelo.testing import store_of
from segment_helpers import GLOBAL, GUEST, descriptor, qos_entry

from ._type_support import MockVcoClientFactory
from .write_helpers import qos_mod

EDGE = ConfigContext.EDGE
PROFILE = ConfigContext.PROFILE

RULE: dict[str, Any] = {
    "name": "r1",
    "match": {},
    "action": {
        "edge2EdgeRouteAction": {},
        "edge2DataCenterRouteAction": {},
        "edge2CloudRouteAction": {},
        "QoS": {},
    },
}

# (class, field, kind, encoding type, wire key)
KIND_TABLE: list[tuple[type[VcoModel], str, Kind, type | None, str]] = [
    (QosData, "segments", Kind.STRUCTURAL, None, "segments"),
    (QosData, "service_rate_limit", Kind.INHERITABLE, ByPresence, "serviceRateLimit"),
    (QosData, "qoe", Kind.INHERITABLE, ByPresence, "qoe"),
    (QosSegment, "segment", Kind.STRUCTURAL, None, "segment"),
    (QosSegment, "rules", Kind.ADDITIVE, None, "rules"),
    (QosSegment, "defaults", Kind.PROFILE_ONLY, None, "defaults"),
    (QosSegment, "cos_mapping", Kind.INHERITABLE, ByFlag, "cosMapping"),
    (QosSegment, "web_proxy", Kind.UNCONVERTED, None, "webProxy"),
]


def test_the_qos_kinds_are_as_declared() -> None:
    for model, name, kind, encoding, key in KIND_TABLE:
        for layer in (Edge, Profile):
            layered: Any = model
            fields = layered[layer].model_fields
            found = field_kind(fields[name])
            where = f"{model.__name__}[{layer.__name__}].{name}"
            assert found.kind is kind, where
            assert (
                found.encoding is None
                if encoding is None
                else isinstance(found.encoding, encoding)
            ), where
            assert wire_name(layered[layer], name) == key, where
            has_default = any(isinstance(m, Default) for m in found.metadata)
            assert has_default is (kind is Kind.INHERITABLE), where
    assert {n for _, n, _, _, _ in KIND_TABLE if n} == set(
        QosData[Edge].model_fields
    ) | set(QosSegment[Edge].model_fields)
    cos = field_kind(QosSegment[Edge].model_fields["cos_mapping"])
    assert cos.encoding == ByFlag("override", presence_wins=True)
    assert not any(
        isinstance(m, ByPresence | ByFlag)
        for m in field_kind(QosSegment[Edge].model_fields["rules"]).metadata
    )


def test_profile_qos_reads_the_template_display_defaults() -> None:
    profile = QosData[Profile].model_validate({"segments": []})
    assert profile.service_rate_limit.model_dump(mode="json", by_alias=True) == {
        "enabled": False,
        "inputType": "percent",
        "value": 1,
    }
    qoe = profile.qoe
    assert (qoe.voice.latency.yellow, qoe.voice.latency.red) == (25, 65)
    assert (qoe.video.latency.yellow, qoe.video.latency.red) == (25, 65)
    assert (qoe.transactional.latency.yellow, qoe.transactional.latency.red) == (50, 80)
    assert profile.model_fields_set == {"segments"}
    assert profile.model_dump(mode="json", by_alias=True) == {"segments": []}


def test_a_display_default_is_a_fresh_object() -> None:
    one = QosData[Profile].model_validate({"segments": []})
    two = QosData[Profile].model_validate({"segments": []})
    assert one.qoe is not two.qoe
    one.service_rate_limit.value = 50
    assert two.service_rate_limit.value == 1


def test_a_profile_segment_without_a_cos_mapping_reads_the_default_one() -> None:
    segment = QosSegment[Profile].model_validate(
        {"segment": descriptor(GLOBAL), "rules": []}
    )
    assert segment.cos_mapping == CosMapping()
    assert segment.cos_mapping.override is False
    assert "cos_mapping" not in segment.model_fields_set
    assert "cosMapping" not in segment.model_dump(mode="json", by_alias=True)


def test_edge_qos_values_are_owned() -> None:
    raw: dict[str, Any] = {
        "segments": [],
        "serviceRateLimit": {"enabled": False, "inputType": "rate", "value": 5},
    }
    edge = QosData[Edge].model_validate(raw)
    assert edge.service_rate_limit.is_local  # a disabled limit is local
    assert edge.qoe.is_local is False
    assert edge.model_dump(mode="json", by_alias=True) == raw


def test_a_cos_mapping_stored_with_its_flag_off_is_kept_and_not_local() -> None:
    stored = {
        "lsInputType": "bandwidth",
        "realtime": {
            "high": {"value": 40, "ratelimit": True},
            "normal": {"value": 15, "ratelimit": False},
            "low": {"value": 1, "ratelimit": False},
        },
        "transactional": {
            "high": {"value": 20, "ratelimit": False},
            "normal": {"value": 7, "ratelimit": False},
            "low": {"value": 1, "ratelimit": False},
        },
        "bulk": {
            "high": {"value": 15, "ratelimit": False},
            "normal": {"value": 5, "ratelimit": False},
            "low": {"value": 1, "ratelimit": False},
        },
        "override": False,
    }
    segment: dict[str, Any] = {
        "segment": descriptor(GLOBAL),
        "rules": [],
        "cosMapping": stored,
    }
    edge = qos_mod({"segments": [segment]}, layer=Edge)
    profile = qos_mod(
        {"segments": [{"segment": descriptor(GLOBAL), "rules": []}]},
        layer=Profile,
        module_id=2,
    )
    owned = edge.data.segments[0].cos_mapping
    assert owned.is_local is False
    assert isinstance(owned.stored, CosMapping)
    assert owned.stored.override is False
    assert owned.stored.realtime.high.value == 40
    assert owned.local is MISSING
    assert (
        edge.data.segments[0].model_dump(mode="json", by_alias=True)["cosMapping"]
        == stored
    )
    # The backend takes a present object without reading its flag.
    view = effective_view(edge.data, profile.data, QosData[Effective])
    assert view.segments[0].cos_mapping.origin is Origin.EDGE
    assert view.segments[0].cos_mapping.value.realtime.high.value == 40
    # No edit: nothing is planned, and an unrelated edit keeps the object.
    assert plan_module(edge, level=EDGE).operation == "none"
    edge.data.segments[0].rules.append(_rule())
    written = plan_module(edge, level=EDGE).data["segments"][0]
    assert written["cosMapping"] == stored


def _rule() -> QosRule:
    return QosRule.model_validate(RULE)


CONNECTORS: list[object] = [None, {"enabled": True, "x": 1}]


@pytest.mark.parametrize("stored", CONNECTORS)
def test_an_edge_s_stored_client_connector_is_kept_as_read(stored: object) -> None:
    raw: dict[str, Any] = {"segments": [], "qosClientConnector": stored}
    edge = qos_mod(raw, layer=Edge)
    assert "qos_client_connector" not in QosData[Edge].model_fields
    plan = plan_module(edge, level=EDGE)
    assert (plan.operation, plan.data) == ("none", raw)
    limit = ServiceRateLimit(enabled=True, input_type="rate", value=3)
    edge.data.service_rate_limit.set_local(limit)
    plan = plan_module(edge, level=EDGE)
    assert plan.data == {
        **raw,
        "serviceRateLimit": {"enabled": True, "inputType": "rate", "value": 3},
    }
    assert [str(c.path) for c in plan.changes] == ["serviceRateLimit"]


@pytest.mark.parametrize("stored", CONNECTORS)
def test_a_profile_s_stored_client_connector_is_kept_as_read(stored: object) -> None:
    raw: dict[str, Any] = {"segments": [], "qosClientConnector": stored}
    profile = qos_mod(raw, layer=Profile)
    plan = plan_module(profile, level=PROFILE)
    assert (plan.operation, plan.data) == ("none", raw)
    profile.data.service_rate_limit = ServiceRateLimit(
        enabled=True, input_type="rate", value=3
    )
    plan = plan_module(profile, level=PROFILE)
    assert plan.data["qosClientConnector"] == stored
    assert "serviceRateLimit" in plan.data


def test_a_patch_cannot_name_the_client_connector() -> None:
    raw: dict[str, Any] = {"segments": [], "qosClientConnector": {"enabled": True}}
    edge = qos_mod(raw, layer=Edge)
    with pytest.raises(PatchError, match="has no field 'qosClientConnector'") as exc:
        _ = edge.data.merge_patch({"qosClientConnector": None})
    assert exc.value.kind == "unknown_field"
    assert plan_module(edge, level=EDGE).operation == "none"


def test_a_stored_client_connector_is_removed_by_planning_the_data_without_it() -> None:
    connector = {"enabled": True, "x": 1}
    raw: dict[str, Any] = {"segments": [], "qosClientConnector": connector}
    edge = qos_mod(raw, layer=Edge)
    data = json.loads(json.dumps(raw))
    del data["qosClientConnector"]
    plan = plan_module(edge, data, level=EDGE)
    assert plan.operation == "update"
    assert plan.data == {"segments": []}
    assert [(str(c.path), c.before, c.after) for c in plan.changes] == [
        ("qosClientConnector", connector, MISSING)
    ]


def test_a_stored_client_connector_is_changed_by_planning_the_data_with_its_value() -> (
    None
):
    raw: dict[str, Any] = {
        "segments": [],
        "serviceRateLimit": {"enabled": True, "inputType": "rate", "value": 3},
        "qosClientConnector": {"enabled": True, "x": 1},
    }
    edge = qos_mod(raw, layer=Edge)
    data = json.loads(json.dumps(raw))
    data["qosClientConnector"] = {"enabled": False}
    plan = plan_module(edge, data, level=EDGE)
    assert plan.operation == "update"
    assert plan.data == data
    # The change is reported leaf by leaf, all of them inside the key.
    assert {str(c.path) for c in plan.changes} == {
        "qosClientConnector.enabled",
        "qosClientConnector.x",
    }


async def test_a_planned_removal_of_a_stored_client_connector_is_stored(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    raw: dict[str, Any] = {"segments": [], "qosClientConnector": {"enabled": True}}
    edge = qos_mod(raw, layer=Edge)
    store = store_of(edge)
    client = await mock_vco_client(store)
    plan = plan_module(edge, {"segments": []}, level=EDGE)
    write = await client.write_module(edge, plan)
    assert write.module.raw_data == {"segments": []}
    assert store.rows[edge.id_]["data"] == {"segments": []}


def test_a_profile_segment_without_defaults_stays_without_them() -> None:
    raw: dict[str, Any] = {"segments": [{"segment": descriptor(GUEST), "rules": []}]}
    module = qos_mod(raw, layer=Profile)
    segment = module.data.segments[0]
    assert segment.defaults is MISSING
    assert "defaults" not in segment.model_dump(mode="json", by_alias=True)
    edge = qos_mod(raw, layer=Edge, module_id=2)
    view = effective_view(edge.data, module.data, QosData[Effective])
    defaults = view.segments[0].defaults
    assert (defaults.origin, defaults.value) == (Origin.FACTORY, MISSING)
    # A dump of the view leaves it out, and gives the rest of the segment.
    dumped, found = effective_dump(view)
    (dumped_segment,) = cast("list[dict[str, object]]", dumped["segments"])
    assert "defaults" not in dumped_segment
    assert not [path for path in found if path.endswith(".defaults")]
    assert "cosMapping" in dumped_segment
    # A write that touches the module still repairs the four defaults, with the warning.
    segment.rules.append(_rule())
    plan = plan_module(module, level=PROFILE)
    assert [w.code for w in plan.warnings] == ["defaults_repaired"]
    assert plan.missing_defaults


def test_edge_defaults_are_not_the_edge_s() -> None:
    entry = qos_entry(GUEST)
    assert entry["defaults"]
    module = qos_mod({"segments": [entry]}, layer=Edge)
    segment = module.data.segments[0]
    assert segment.defaults is MISSING  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
    assert "defaults" not in segment.model_dump(mode="json", by_alias=True)
    segment.rules.append(_rule())
    plan = plan_module(module, level=EDGE)
    assert plan.operation == "update"
    assert plan.data["segments"][0]["defaults"] == entry["defaults"]


def test_the_effective_rules_are_the_edge_s_then_the_profile_s() -> None:
    def rule(name: str) -> dict[str, Any]:
        return {**copy.deepcopy(RULE), "name": name}

    edge = qos_mod(
        {"segments": [{"segment": descriptor(GLOBAL), "rules": [rule("E")]}]},
        layer=Edge,
    )
    profile = qos_mod(
        {
            "segments": [
                {
                    "segment": descriptor(GLOBAL),
                    "rules": [rule("P1"), rule("P2")],
                }
            ]
        },
        layer=Profile,
        module_id=2,
    )
    view = effective_view(edge.data, profile.data, QosData[Effective])
    rules = view.segments[0].rules
    assert [(r.value.name, r.origin) for r in rules] == [
        ("E", Origin.EDGE),
        ("P1", Origin.PROFILE),
        ("P2", Origin.PROFILE),
    ]


@pytest.mark.parametrize("layer", [Edge, Profile])
def test_an_untouched_module_is_planned_as_none(
    layer: type[Edge] | type[Profile],
) -> None:
    raw = {"segments": [qos_entry(GLOBAL, rules=[RULE])]}
    module = qos_mod(raw, layer=layer)
    plan = plan_module(module, level=EDGE if layer is Edge else PROFILE)
    assert plan.operation == "none"
    assert plan.data == module.raw_data
