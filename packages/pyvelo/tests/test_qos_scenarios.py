"""The QoS ownership scenarios, each through ``plan_module`` on edge modules bound to a profile."""

import copy
import json
from typing import Any

import pytest
from pydantic import MISSING
from pyvelo.client.exceptions import UnboundError
from pyvelo.client.models.configuration.profile_segments import module_template_value
from pyvelo.client.models.configuration.qos import CosMapping, QosRule, SlaSettings
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import QosConfigurationModule
from pyvelo.core import ConfigContext, Edge, PatchError, Profile
from segment_helpers import GLOBAL, GUEST, descriptor, qos_entry

from .write_helpers import qos_mod

EDGE = ConfigContext.EDGE
PROFILE = ConfigContext.PROFILE

COS_PROFILE: dict[str, Any] = {
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
}
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


def segment_of(name: str = "Global Segment", **extra: Any) -> dict[str, Any]:
    entry = {"segment": descriptor(GLOBAL if name == "Global Segment" else GUEST)}
    return {**entry, "rules": [], **extra}


def bound(
    edge_raw: dict[str, Any], profile_raw: dict[str, Any] | None = None
) -> tuple[QosConfigurationModule[Edge], QosConfigurationModule[Profile]]:
    """An edge module of *edge_raw* bound to a profile module of *profile_raw*, and that one."""
    edge = qos_mod({"segments": [], **edge_raw}, layer=Edge)
    profile = qos_mod(
        {"segments": [], **(profile_raw or {})}, layer=Profile, module_id=2
    )
    edge.data.bind_profile(profile.raw_data)
    return edge, profile


def payload(edge: Any, profile: Any) -> dict[str, Any]:
    return plan_module(edge, level=EDGE, profile=profile).data


def test_set_local_on_cos_mapping_writes_it_with_override_true() -> None:
    edge, profile = bound({"segments": [segment_of()]}, {"segments": [segment_of()]})
    owned = edge.data.segments[0].cos_mapping
    assert owned.is_local is False
    owned.set_local(CosMapping())
    plan = plan_module(edge, level=EDGE, profile=profile)
    written = plan.data["segments"][0]["cosMapping"]
    assert written["override"] is True
    assert written["lsInputType"] == "weight"
    assert [str(c.path) for c in plan.changes] == [
        'segments["Global Segment"].cosMapping'
    ]
    assert owned.is_local


def test_inherit_on_cos_mapping_omits_it() -> None:
    stored = {**COS_PROFILE, "override": True}
    edge, profile = bound(
        {"segments": [segment_of(cosMapping=stored)]}, {"segments": [segment_of()]}
    )
    owned = edge.data.segments[0].cos_mapping
    assert owned.is_local
    owned.inherit()
    plan = plan_module(edge, level=EDGE, profile=profile)
    assert "cosMapping" not in plan.data["segments"][0]
    assert not owned.is_local


def test_inherit_removes_a_cos_mapping_stored_with_its_flag_off_too() -> None:
    # The backend takes a present object without its flag, so only removing it inherits.
    stored = {**COS_PROFILE, "override": False}
    edge, profile = bound(
        {"segments": [segment_of(cosMapping=stored)]}, {"segments": [segment_of()]}
    )
    edge.data.segments[0].cos_mapping.inherit()
    assert "cosMapping" not in payload(edge, profile)["segments"][0]


def test_localize_cos_mapping_in_a_placeholder_copies_the_profile_segment_s() -> None:
    edge, profile = bound(
        {"segments": []},
        {"segments": [segment_of("Guest", cosMapping=COS_PROFILE)]},
    )
    placeholder = edge.data.segment("Guest")
    assert placeholder.cos_mapping.stored is MISSING
    mapping = placeholder.cos_mapping.localize()
    assert mapping.override is True
    assert mapping.realtime.high.value == 40
    mapping.realtime.high.value = 41
    (written,) = payload(edge, profile)["segments"]
    assert written["segment"]["name"] == "Guest"
    assert written["cosMapping"] == {
        **COS_PROFILE,
        "realtime": {
            **COS_PROFILE["realtime"],
            "high": {"value": 41, "ratelimit": True},
        },
        "override": True,
    }
    # The profile is unchanged.
    assert isinstance(profile.raw_data, dict)
    assert profile.raw_data["segments"][0]["cosMapping"] == COS_PROFILE


def test_a_localized_cos_mapping_of_a_segment_the_profile_gives_the_default_is_written_whole() -> (
    None
):
    edge, profile = bound({"segments": []}, {"segments": [segment_of("Guest")]})
    placeholder = edge.data.segment("Guest")
    _ = placeholder.cos_mapping.localize()
    (written,) = payload(edge, profile)["segments"]
    assert written["cosMapping"] == {
        "lsInputType": "weight",
        "realtime": COS_PROFILE["realtime"]
        | {"high": {"value": 35, "ratelimit": False}},
        "transactional": COS_PROFILE["transactional"],
        "bulk": COS_PROFILE["bulk"],
        "override": True,
    }


def test_a_localized_service_rate_limit_from_the_display_default_is_written_whole() -> (
    None
):
    edge, profile = bound({})
    assert isinstance(profile.raw_data, dict)
    assert "serviceRateLimit" not in profile.raw_data
    limit = edge.data.service_rate_limit.localize()
    limit.value = 7
    data = payload(edge, profile)
    template = module_template_value("QOS", "serviceRateLimit")
    assert isinstance(template, dict)
    assert data["serviceRateLimit"] == {**template, "value": 7}
    assert set(data["serviceRateLimit"]) == {"enabled", "inputType", "value"}
    assert "qoe" not in data


def test_a_localized_qoe_from_the_display_default_is_written_whole() -> None:
    edge, profile = bound({})
    edge.data.qoe.localize().voice.latency.red = 70
    expected = module_template_value("QOS", "qoe")
    assert isinstance(expected, dict)
    expected["voice"]["latency"]["red"] = 70
    assert payload(edge, profile)["qoe"] == expected


def test_a_profile_without_the_key_gives_the_edge_what_the_profile_displays() -> None:
    edge, profile = bound(
        {}, {"serviceRateLimit": {"enabled": True, "inputType": "rate", "value": 9}}
    )
    limit = edge.data.service_rate_limit.localize()
    assert (limit.enabled, limit.input_type, limit.value) == (True, "rate", 9)
    assert isinstance(profile.raw_data, dict)
    assert (
        payload(edge, profile)["serviceRateLimit"]
        == profile.raw_data["serviceRateLimit"]
    )


def test_setting_the_qoe_changes_the_write_and_inheriting_removes_it() -> None:
    qoe = module_template_value("QOS", "qoe")
    edge, profile = bound({"qoe": qoe})
    assert edge.data.qoe.is_local
    edge.data.qoe.inherit()
    plan = plan_module(edge, level=EDGE, profile=profile)
    assert "qoe" not in plan.data
    assert [str(c.path) for c in plan.changes] == ["qoe"]


def test_inherit_on_the_client_connector_removes_the_key() -> None:
    edge, profile = bound({"qosClientConnector": {"data": {"a": 1}}})
    edge.data.qos_client_connector.inherit()
    plan = plan_module(edge, level=EDGE, profile=profile)
    assert "qosClientConnector" not in plan.data
    assert [str(c.path) for c in plan.changes] == ["qosClientConnector"]


def test_set_local_to_null_is_refused_and_means_inherit() -> None:
    edge, _ = bound({})
    with pytest.raises(PatchError):
        edge.data.qos_client_connector.set_local(None)


def test_localize_needs_a_bound_profile() -> None:
    edge = qos_mod({"segments": []}, layer=Edge)
    with pytest.raises(UnboundError):
        edge.data.qoe.localize()


def test_rules_added_at_the_edge_stay_out_of_the_profile_s() -> None:
    edge_rule = {**copy.deepcopy(RULE), "name": "e1"}
    profile_rule = {**copy.deepcopy(RULE), "name": "p1"}
    edge, profile = bound(
        {"segments": [segment_of(rules=[edge_rule])]},
        {"segments": [segment_of(rules=[profile_rule])]},
    )
    edge.data.segments[0].rules.append(
        QosRule.model_validate({**copy.deepcopy(RULE), "name": "e2"})
    )
    names = [r["name"] for r in payload(edge, profile)["segments"][0]["rules"]]
    assert names == ["e1", "e2"]


FULL_MODULE: dict[str, Any] = {
    "serviceRateLimit": {"enabled": True, "inputType": "rate", "value": 9},
    "qoe": module_template_value("QOS", "qoe"),
    "qosClientConnector": {"data": {"a": 1}},
}


def full_segment() -> dict[str, Any]:
    entry = qos_entry(GUEST, rules=[RULE])
    entry["cosMapping"] = {**COS_PROFILE, "override": True}
    entry["webProxy"] = {"providers": []}
    return entry


@pytest.mark.parametrize("layer", [Edge, Profile])
def test_an_untouched_qos_save_is_unchanged(layer: type[Edge] | type[Profile]) -> None:
    raw = {**copy.deepcopy(FULL_MODULE), "segments": [full_segment()]}
    module = qos_mod(raw, layer=layer)
    level = EDGE if layer is Edge else PROFILE
    plan = plan_module(module, level=level)
    assert (plan.operation, plan.changes) == ("none", ())
    assert plan.data == raw
    assert set(plan.data) == {"segments", *FULL_MODULE}
    assert set(plan.data["segments"][0]) == {
        "segment",
        "rules",
        "defaults",
        "cosMapping",
        "webProxy",
    }


@pytest.mark.parametrize("layer", [Edge, Profile])
def test_an_unrelated_edit_writes_every_other_key_as_read(
    layer: type[Edge] | type[Profile],
) -> None:
    raw = {**copy.deepcopy(FULL_MODULE), "segments": [full_segment()]}
    module = qos_mod(raw, layer=layer)
    module.data.segments[0].rules.append(
        QosRule.model_validate({**copy.deepcopy(RULE), "name": "added"})
    )
    plan = plan_module(module, level=EDGE if layer is Edge else PROFILE)
    written = copy.deepcopy(plan.data)
    (appended,) = written["segments"][0]["rules"][1:]
    assert appended["name"] == "added"
    del written["segments"][0]["rules"][1:]
    assert written == raw


def test_plan_warns_of_a_cos_mapping_the_backend_uses_with_its_flag_off() -> None:
    stored = {**COS_PROFILE, "override": False}
    raw = {"segments": [segment_of(cosMapping=stored)]}
    edge, profile = bound(raw, {"segments": [segment_of()]})
    assert edge.data.segments[0].cos_mapping.is_local is False

    plan = plan_module(edge, level=EDGE, profile=profile)
    assert plan.operation == "none"
    assert [(w.code, w.path) for w in plan.warnings] == [
        ("flag_off_in_use", 'segments["Global Segment"].cosMapping')
    ]
    message = plan.warnings[0].message
    assert (
        "The edge stores cosMapping of segment 'Global Segment' with override false or absent."
        in message
    )
    assert '{"segments": {"Global Segment": {"cosMapping": null}}}' in message
    assert "inherit()" in message
    assert "localize()" in message
    assert '{"segments": {"Global Segment": {"cosMapping": {}}}}' in message

    edge.data.segments[0].cos_mapping.inherit()
    after = plan_module(edge, level=EDGE, profile=profile)
    assert not [w for w in after.warnings if w.code == "flag_off_in_use"]

    other, profile = bound(raw, {"segments": [segment_of()]})
    other.data.segments[0].cos_mapping.set_local(CosMapping())
    local = plan_module(other, level=EDGE, profile=profile)
    assert not [w for w in local.warnings if w.code == "flag_off_in_use"]


def flag_off_voice(realtime_high: int = 50) -> dict[str, Any]:
    stored = {
        **COS_PROFILE,
        "override": False,
        "realtime": {
            **COS_PROFILE["realtime"],
            "high": {"value": realtime_high, "ratelimit": True},
        },
    }
    return {"segments": [segment_of("Guest", cosMapping=stored)]}


def test_localize_of_a_cos_mapping_stored_with_its_flag_off_keeps_its_values() -> None:
    profile = {"segments": [segment_of("Guest", cosMapping=COS_PROFILE)]}
    edge, profile = bound(flag_off_voice(50), profile)
    owned = edge.data.segments[0].cos_mapping
    mapping = owned.localize()
    assert mapping.override is True
    assert mapping.realtime.high.value == 50
    plan = plan_module(edge, level=EDGE, profile=profile)
    assert [str(c.path) for c in plan.changes] == [
        "segments[Guest].cosMapping.override"
    ]
    written = plan.data["segments"][0]["cosMapping"]
    assert written == {
        **flag_off_voice(50)["segments"][0]["cosMapping"],
        "override": True,
    }
    assert not [w for w in plan.warnings if w.code == "flag_off_in_use"]


def test_the_flag_off_warning_gives_the_keep_patch_and_localize() -> None:
    edge, profile = bound(
        flag_off_voice(50),
        {"segments": [segment_of("Guest", cosMapping=COS_PROFILE)]},
    )
    plan = plan_module(edge, level=EDGE, profile=profile)
    (warning,) = plan.warnings
    patch = {"segments": {"Guest": {"cosMapping": {}}}}
    assert json.dumps(patch) in warning.message
    assert "localize()" in warning.message


def test_an_unrelated_edit_keeps_the_flag_off_warning() -> None:
    # The warning is made from the payload when the module is written.
    raw = flag_off_voice(50)
    edge, profile = bound(raw, {"segments": [segment_of("Guest")]})
    edge.data.segments[0].rules.append(QosRule.model_validate(copy.deepcopy(RULE)))
    plan = plan_module(edge, level=EDGE, profile=profile)
    assert plan.operation == "update"
    assert [(w.code, w.path) for w in plan.warnings] == [
        ("flag_off_in_use", 'segments["Guest"].cosMapping')
    ]
    assert plan.data["segments"][0]["cosMapping"] == raw["segments"][0]["cosMapping"]


def test_a_profile_module_with_the_same_data_has_no_flag_off_warning() -> None:
    stored = {**COS_PROFILE, "override": False}
    profile = qos_mod({"segments": [segment_of(cosMapping=stored)]}, layer=Profile)
    plan = plan_module(profile, level=PROFILE)
    assert "flag_off_in_use" not in [w.code for w in plan.warnings]


def sla_rule(**sla: Any) -> dict[str, Any]:
    action = {**RULE["action"], "sla": {"latencyMs": "10", "jitterMs": "5", **sla}}
    return {**copy.deepcopy(RULE), "action": action}


def test_a_rule_s_sla_keeps_the_vco_s_lossPct_key_for_key() -> None:
    # The VCO stores the key as lossPct; a loss_pct key is not one the VCO uses.
    raw = {"segments": [segment_of(rules=[sla_rule(lossPct="0.5")])]}
    module = qos_mod(raw, layer=Edge)
    sla = module.data.segments[0].rules[0].action.sla
    assert isinstance(sla, SlaSettings)
    assert sla.loss_percent == "0.5"
    plan = plan_module(module, level=EDGE)
    assert (plan.operation, plan.data) == ("none", raw)
    sla.loss_percent = "1.5"
    written = plan_module(module, level=EDGE).data["segments"][0]["rules"][0]
    assert written["action"]["sla"]["lossPct"] == "1.5"
    assert "loss_pct" not in written["action"]["sla"]


def test_a_new_qos_rule_with_an_sla_is_written_with_lossPct_and_not_loss_pct() -> None:
    edge, profile = bound({"segments": [segment_of()]}, {"segments": [segment_of()]})
    edge.data.segments[0].rules.append(QosRule.model_validate(sla_rule()))
    (written,) = payload(edge, profile)["segments"][0]["rules"]
    assert written["action"]["sla"]["lossPct"] == "0.0"
    assert "loss_pct" not in written["action"]["sla"]
