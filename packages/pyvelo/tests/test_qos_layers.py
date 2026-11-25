"""The QoS module's kinds as declared, the display defaults it reads, and what each layer holds."""

import copy
from typing import Any, cast

import pytest
from pydantic import MISSING
from pyvelo.client.models.configuration import QosData
from pyvelo.client.models.configuration.qos import (
    CosMapping,
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
    Profile,
    field_kind,
    wire_name,
)
from pyvelo.core.base import VcoModel
from pyvelo.core.effective import Origin, effective_dump, effective_view
from pyvelo.core.layers import Effective
from segment_helpers import GLOBAL, GUEST, descriptor, qos_entry

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
    (
        QosData,
        "qos_client_connector",
        Kind.INHERITABLE,
        ByPresence,
        "qosClientConnector",
    ),
    (QosSegment, "segment", Kind.STRUCTURAL, None, "segment"),
    (QosSegment, "rules", Kind.ADDITIVE, None, "rules"),
    (QosSegment, "defaults", Kind.PROFILE_ONLY, None, "defaults"),
    (QosSegment, "cos_mapping", Kind.INHERITABLE, ByFlag, "cosMapping"),
    (QosSegment, "web_proxy", Kind.UNCONVERTED, None, "webProxy"),
]


def test_the_qos_kinds_are_as_declared():
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


def test_profile_qos_reads_the_template_display_defaults():
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
    assert profile.qos_client_connector is None
    assert profile.model_fields_set == {"segments"}
    assert profile.model_dump(mode="json", by_alias=True) == {"segments": []}


def test_a_display_default_is_a_fresh_object():
    one = QosData[Profile].model_validate({"segments": []})
    two = QosData[Profile].model_validate({"segments": []})
    assert one.qoe is not two.qoe
    one.service_rate_limit.value = 50
    assert two.service_rate_limit.value == 1


def test_a_profile_segment_without_a_cos_mapping_reads_the_default_one():
    segment = QosSegment[Profile].model_validate(
        {"segment": descriptor(GLOBAL), "rules": []}
    )
    assert segment.cos_mapping == CosMapping()
    assert segment.cos_mapping.override is False
    assert "cos_mapping" not in segment.model_fields_set
    assert "cosMapping" not in segment.model_dump(mode="json", by_alias=True)


def test_edge_qos_values_are_owned():
    raw: dict[str, Any] = {
        "segments": [],
        "serviceRateLimit": {"enabled": False, "inputType": "rate", "value": 5},
    }
    edge = QosData[Edge].model_validate(raw)
    assert edge.service_rate_limit.is_local  # a disabled limit is local
    assert edge.qoe.is_local is False
    assert edge.qos_client_connector.is_local is False
    assert edge.model_dump(mode="json", by_alias=True) == raw


def test_a_cos_mapping_stored_with_its_flag_off_is_kept_and_not_local():
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


def _rule():
    from pyvelo.client.models.configuration.qos import QosRule

    return QosRule.model_validate(RULE)


def test_a_stored_null_client_connector_is_kept_and_not_local():
    edge = qos_mod({"segments": [], "qosClientConnector": None}, layer=Edge)
    profile = qos_mod({"segments": []}, layer=Profile, module_id=2)
    owned = edge.data.qos_client_connector
    assert owned.stored is None
    assert owned.is_local is False
    view = effective_view(edge.data, profile.data, QosData[Effective])
    assert view.qos_client_connector.origin is Origin.FACTORY
    assert plan_module(edge, level=EDGE).operation == "none"
    edge.data.service_rate_limit.set_local(
        ServiceRateLimit(enabled=True, input_type="rate", value=3)
    )
    data = plan_module(edge, level=EDGE).data
    assert data["qosClientConnector"] is None
    assert "serviceRateLimit" in data


def test_an_edge_s_stored_null_client_connector_shows_the_profile_s_block():
    # ByPresence: a null is not local, so the profile's block is what the view shows.
    block = {"data": {"a": 1}}
    edge = qos_mod({"segments": [], "qosClientConnector": None}, layer=Edge)
    profile = qos_mod(
        {"segments": [], "qosClientConnector": block}, layer=Profile, module_id=2
    )
    view = effective_view(edge.data, profile.data, QosData[Effective])
    connector = view.qos_client_connector
    assert connector.origin is Origin.PROFILE
    assert connector.value is not None
    assert connector.value.model_dump(mode="json", by_alias=True) == block


def test_a_stored_client_connector_is_local():
    raw: dict[str, Any] = {"segments": [], "qosClientConnector": {"data": {"a": 1}}}
    edge = qos_mod(raw, layer=Edge)
    assert edge.data.qos_client_connector.is_local
    assert edge.data.model_dump(mode="json", by_alias=True) == raw


def test_a_profile_segment_without_defaults_stays_without_them():
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


def test_edge_defaults_are_not_the_edge_s():
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


def test_the_effective_rules_are_the_edge_s_then_the_profile_s():
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
def test_an_untouched_module_is_planned_as_none(layer: type[Edge] | type[Profile]):
    raw = {"segments": [qos_entry(GLOBAL, rules=[RULE])]}
    module = qos_mod(raw, layer=layer)
    plan = plan_module(module, level=EDGE if layer is Edge else PROFILE)
    assert plan.operation == "none"
    assert plan.data == module.raw_data
