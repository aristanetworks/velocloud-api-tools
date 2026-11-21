import copy
import json
from typing import Any

import pytest
from pydantic import MISSING, Field
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import LossyFieldError
from pyvelo.client.models.configuration import FirewallData
from pyvelo.client.models.configuration.firewall.rules import FirewallOutboundRule
from pyvelo.client.module_write import missing_segments, plan_module
from pyvelo.client.schemas.configuration import (
    ConfigurationModuleList,
    DeviceSettingsConfigurationModule,
    FirewallConfigurationModule,
    GenericConfigurationModule,
    QosConfigurationModule,
    new_edge_module,
)
from pyvelo.core import ConfigContext, VcoModel
from pyvelo.core.rebase import Key
from pyvelo.testing import make_module_json

EDGE = ConfigContext.EDGE


def desc(sid: int, name: str, **extra: object) -> dict[str, object]:
    return {
        "segmentId": sid,
        "name": name,
        "type": "REGULAR",
        "segmentLogicalId": f"00000000-0000-0000-0000-{sid:012d}",
        **extra,
    }


EDGE_RAW: dict[str, Any] = {
    "segments": [
        {"segment": desc(0, "Global Segment"), "outbound": [], "futureKey": 1}
    ],
    "syslog_forwarding": False,
    "firewall_logging_enabled": False,
    "futureTopLevel": [1, 2.5, None],
}
PROFILE_RAW: dict[str, Any] = {
    "firewall_enabled": True,
    "segments": [
        {
            "segment": desc(0, "Global Segment"),
            "outbound": [
                {"name": "allow-all", "match": {}, "action": {"allow_or_deny": "allow"}}
            ],
        },
        {"segment": desc(2, "Guest", vendorKey="x"), "outbound": []},
    ],
}


def fw(raw: dict[str, Any], *, module_id: int = 1) -> FirewallConfigurationModule:
    return FirewallConfigurationModule.model_validate(
        make_module_json("firewall", copy.deepcopy(raw), module_id=module_id)
    )


def rule(name: str) -> FirewallOutboundRule:
    return FirewallOutboundRule.model_validate(
        {"name": name, "action": {"allow_or_deny": "deny"}, "match": {}}
    )


def test_no_edit_plans_nothing_and_keeps_raw():
    plan = plan_module(fw(EDGE_RAW), level=EDGE)
    assert (plan.operation, plan.data, plan.changes, plan.refs) == (
        "none",
        EDGE_RAW,
        (),
        None,
    )


def test_unrelated_edit_leaves_inherited_keys_absent_and_local_false_present():
    mod = fw(EDGE_RAW)
    mod.data.firewall_enabled = False
    plan = plan_module(mod, level=EDGE)
    assert plan.operation == "update"
    assert plan.data == {**EDGE_RAW, "firewall_enabled": False}
    assert [(c.path, c.before, c.after) for c in plan.changes] == [
        ("firewall_enabled", MISSING, False)
    ]


def test_edit_is_written_to_the_snake_case_key_as_read():
    mod = fw(EDGE_RAW)
    mod.data.firewall_logging_enabled = True
    plan = plan_module(mod, level=EDGE)
    assert plan.data["firewall_logging_enabled"] is True
    assert "firewallLoggingEnabled" not in plan.data


def test_inserted_rules_keep_order_and_unknown_segment_keys():
    mod = fw(EDGE_RAW)
    seg = mod.data.segments[0]
    seg.outbound.insert(0, rule("b"))
    seg.outbound.insert(0, rule("a"))
    plan = plan_module(mod, level=EDGE)
    out = plan.data["segments"][0]
    assert [r["name"] for r in out["outbound"]] == ["a", "b"]
    assert out["futureKey"] == 1 and plan.data["futureTopLevel"] == [1, 2.5, None]


def test_lossy_assignment_raises():
    mod = fw(EDGE_RAW)
    mod.data.atp_enabled = False  # absent in raw, equals the model default
    with pytest.raises(LossyFieldError, match="atp_enabled") as exc:
        plan_module(mod, level=EDGE)
    assert exc.value.fields == ["atp_enabled"]


def test_reconciliation_rides_along_with_a_write():
    mod, profile = fw(EDGE_RAW), fw(PROFILE_RAW, module_id=2)
    mod.data.firewall_enabled = False
    plan = plan_module(mod, level=EDGE, profile=profile)
    assert plan.reconciled_segments == ("Guest",)
    assert plan.data["segments"][0] == EDGE_RAW["segments"][0]
    assert plan.data["segments"][1] == {
        "segment": desc(2, "Guest", vendorKey="x"),
        "outbound": [],
    }
    assert [c.path for c in plan.changes] == ["firewall_enabled"]


def test_no_edit_does_not_write_just_to_reconcile():
    plan = plan_module(fw(EDGE_RAW), level=EDGE, profile=fw(PROFILE_RAW, module_id=2))
    assert (plan.operation, plan.data, plan.reconciled_segments) == (
        "none",
        EDGE_RAW,
        ("Guest",),
    )


def test_edge_only_segment_is_kept_and_profile_level_never_reconciles():
    raw = copy.deepcopy(EDGE_RAW)
    raw["segments"].append({"segment": desc(9, "EdgeOnly"), "outbound": []})
    mod = fw(raw)
    mod.data.firewall_enabled = False
    plan = plan_module(mod, level=EDGE, profile=fw(PROFILE_RAW, module_id=2))
    assert [s["segment"]["name"] for s in plan.data["segments"]] == [
        "Global Segment",
        "EdgeOnly",
        "Guest",
    ]
    prof = fw(PROFILE_RAW)
    prof.data.firewall_enabled = False
    assert (
        plan_module(
            prof, level=ConfigContext.PROFILE, profile=fw(PROFILE_RAW)
        ).reconciled_segments
        == ()
    )


def guest_rule() -> dict[str, Any]:
    return {"name": "g", "match": {}, "action": {"allow_or_deny": "allow"}}


def test_deleting_an_edge_segment_the_profile_has_leaves_its_placeholder():
    raw = copy.deepcopy(EDGE_RAW)
    raw["segments"].append({"segment": desc(2, "Guest"), "outbound": [guest_rule()]})
    mod, profile = fw(raw), fw(PROFILE_RAW, module_id=2)
    del mod.data.segments[1]
    plan = plan_module(mod, level=EDGE, profile=profile)
    assert plan.operation == "update"
    assert plan.data["segments"] == [
        raw["segments"][0],
        {"segment": desc(2, "Guest", vendorKey="x"), "outbound": []},
    ]
    assert plan.reconciled_segments == ("Guest",)


def test_deleting_a_segment_that_is_only_a_placeholder_is_no_change():
    raw = copy.deepcopy(EDGE_RAW)
    raw["segments"].append(missing_segments(EDGE_RAW, PROFILE_RAW, "firewall")[0])
    mod, profile = fw(raw), fw(PROFILE_RAW, module_id=2)
    del mod.data.segments[1]
    plan = plan_module(mod, level=EDGE, profile=profile)
    assert (plan.operation, plan.data) == ("none", raw)


def test_edit_inside_a_missing_segment_is_relative_to_its_placeholder():
    mod, profile = fw(EDGE_RAW), fw(PROFILE_RAW, module_id=2)
    guest = type(mod.data.segments[0]).model_validate(
        {"segment": desc(2, "Guest", vendorKey="x"), "outbound": []}
    )
    guest.outbound.append(rule("block"))
    mod.data.segments.append(guest)
    plan = plan_module(mod, level=EDGE, profile=profile)
    written = plan.data["segments"][1]
    assert written["segment"] == desc(2, "Guest", vendorKey="x")
    assert [r["name"] for r in written["outbound"]] == ["block"]
    assert set(written) == {"segment", "outbound"}


def test_insert_from_an_empty_layer_has_only_local_content_plus_placeholders():
    mod = new_edge_module(ConfigurationModuleName.FIREWALL, 10)
    assert isinstance(mod, FirewallConfigurationModule)
    profile = fw(PROFILE_RAW, module_id=2)
    assert plan_module(mod, level=EDGE, profile=profile).operation == "none"
    mod.data.firewall_enabled = False
    plan = plan_module(mod, level=EDGE, profile=profile)
    assert plan.operation == "insert"
    assert plan.data == {
        "segments": [
            {"segment": desc(0, "Global Segment"), "outbound": []},
            {"segment": desc(2, "Guest", vendorKey="x"), "outbound": []},
        ],
        "firewall_enabled": False,
    }


def test_placeholders_per_module():
    prof = {
        "segments": [
            {
                "segment": desc(2, "Guest"),
                "rules": [{"name": "p"}],
                "routes": {
                    "static": [{"destination": "192.0.2.0"}],
                    "staticV6": [{"destination": "2001:db8::"}],
                },
            }
        ]
    }
    assert missing_segments({"segments": []}, prof, "QOS") == [
        {"segment": desc(2, "Guest"), "rules": []}
    ]
    assert missing_segments({"segments": []}, prof, "deviceSettings") == [
        {"segment": desc(2, "Guest"), "routes": {"static": []}}
    ]
    assert missing_segments({"segments": []}, prof, "WAN") == []


def test_reconcile_tolerates_malformed_segments():
    prof = {
        "segments": [
            {"segment": desc(2, "Guest")},
            {"segment": {"name": "no id"}},
            "junk",
            {},
        ]
    }
    assert missing_segments({}, prof, "firewall") == [
        {"segment": desc(2, "Guest"), "outbound": []}
    ]
    assert missing_segments({"segments": None}, {"segments": None}, "firewall") == []
    edge = {"segments": [{"segment": desc(2, "Renamed")}, {"no": "segment"}]}
    assert missing_segments(edge, prof, "firewall") == []


def test_unparseable_shapes_do_not_cause_a_write():
    # DefaultingScalar turns both of these into False on parse.
    raw = {"segments": [], "syslog_forwarding": None, "atp_enabled": "not-a-bool"}
    mod = fw(raw)
    assert (mod.data.syslog_forwarding, mod.data.atp_enabled) == (False, False)
    plan = plan_module(mod, level=EDGE)
    assert (plan.operation, plan.data) == ("none", raw)


def test_untyped_module_plans_none():
    mod = GenericConfigurationModule.model_validate(
        make_module_json("analyticsSettings", {"a": [1]})
    )
    assert plan_module(mod).operation == "none"
    mod.data["a"].append(2)
    assert plan_module(mod).data == {"a": [1, 2]}


def test_refs_are_sent_only_when_changed_and_then_whole():
    qos = QosConfigurationModule.model_validate(
        make_module_json("QOS", {"segments": []}, refs={"x": [1], "y": 2})
    )
    assert plan_module(qos, level=EDGE).refs is None
    plan = plan_module(qos, level=EDGE, refs={"x": [1, 5], "y": 2})
    assert (plan.operation, plan.refs, plan.data) == (
        "update",
        {"x": [1, 5], "y": 2},
        {"segments": []},
    )


def test_explicit_data_argument_leaves_module_data_out_of_it():
    mod = fw(EDGE_RAW)
    data = mod.mutable_data()
    data.firewall_enabled = False
    assert plan_module(mod, data, level=EDGE).data["firewall_enabled"] is False
    assert plan_module(mod, level=EDGE).operation == "none"


def test_render():
    mod = fw(EDGE_RAW)
    mod.data.firewall_enabled = False
    mod.data.syslog_forwarding = True
    text = plan_module(mod, level=EDGE, profile=fw(PROFILE_RAW, module_id=2)).render()
    assert text.splitlines() == [
        "firewall: update",
        "  ~ syslog_forwarding: false -> true",
        "  + firewall_enabled: false",
        "  reconciled segments: Guest",
    ]


def test_change_paths_quote_keys_with_special_characters():
    mod = fw(EDGE_RAW)
    mod.data.segments[0].outbound.append(rule("b"))
    plan = plan_module(mod, level=EDGE)
    (change,) = plan.changes
    assert change.path.parts == (
        "segments",
        Key("Global Segment"),
        "outbound",
        Key("b"),
    )
    assert change.path == 'segments["Global Segment"].outbound[b]'
    assert (
        plan.render()
        .splitlines()[1]
        .startswith('  + segments["Global Segment"].outbound[b]: ')
    )


def test_lossy_error_carries_structured_paths():
    raw = copy.deepcopy(EDGE_RAW)
    raw["segments"][0]["outbound"] = [
        {"name": "r1", "action": {"allow_or_deny": "allow"}, "match": {}}
    ]
    mod = fw(raw)
    mod.data.segments[0].outbound[
        0
    ].logging_enabled = False  # absent in raw, the default
    with pytest.raises(LossyFieldError) as exc:
        plan_module(mod, level=EDGE)
    parts = (
        "segments",
        Key("Global Segment"),
        "outbound",
        Key("r1"),
        "logging_enabled",
    )
    assert [p.parts for p in exc.value.paths] == [parts]
    assert exc.value.fields == [
        'segments["Global Segment"].outbound[r1].logging_enabled'
    ]
    assert str(exc.value) == (
        "firewall: assignment cannot be expressed on save for: "
        'segments["Global Segment"].outbound[r1].logging_enabled'
    )


# --- behaviour the cases above do not pin ----------------------------------


class _Refs(VcoModel):
    x: list[int] = Field(default_factory=list)


def test_typed_refs_keep_unknown_keys_and_are_sent_only_when_changed():
    raw_refs = {"x": [1], "unknownRef": {"k": 1}}
    qos = QosConfigurationModule.model_validate(
        make_module_json("QOS", {"segments": []}, refs=raw_refs)
    )
    refs = _Refs.model_validate(raw_refs)
    assert plan_module(qos, refs=refs, level=EDGE).refs is None
    refs.x.append(5)
    plan = plan_module(qos, refs=refs, level=EDGE)
    assert plan.operation == "update"
    assert plan.refs == {"x": [1, 5], "unknownRef": {"k": 1}}


def test_refs_for_a_module_that_had_none_are_sent_whole():
    qos = QosConfigurationModule.model_validate(
        make_module_json("QOS", {"segments": []})
    )
    plan = plan_module(qos, level=EDGE, refs=_Refs(x=[2]))
    assert (plan.operation, plan.refs) == ("update", {"x": [2]})


def test_module_that_never_carried_data_sends_the_whole_dump():
    mod = FirewallConfigurationModule.model_construct(
        name="firewall", data=FirewallData(segments=[], firewall_enabled=False)
    )
    assert mod.raw_data is MISSING
    plan = plan_module(mod, level=EDGE)
    assert plan.operation == "update"
    assert plan.data == mod.data.model_dump(
        mode="json", by_alias=True, context={"config_level": EDGE}
    )
    assert plan.data["firewall_enabled"] is False
    assert plan.reconciled_segments == ()
    assert plan.changes[0].path == "firewall_enabled"


def test_a_module_without_a_segments_key_gets_one_for_reconciliation():
    mod = fw({})
    profile = fw(PROFILE_RAW, module_id=2)
    assert plan_module(mod, level=EDGE, profile=profile).operation == "none"
    mod.data.firewall_enabled = False
    plan = plan_module(mod, level=EDGE, profile=profile)
    assert plan.data == {
        "segments": [
            {"segment": desc(0, "Global Segment"), "outbound": []},
            {"segment": desc(2, "Guest", vendorKey="x"), "outbound": []},
        ],
        "firewall_enabled": False,
    }
    assert plan.reconciled_segments == ("Global Segment", "Guest")


def test_a_profile_that_repeats_a_segment_id_reconciles_it_once():
    prof = {"segments": [{"segment": desc(2, "A")}, {"segment": desc(2, "A")}]}
    assert missing_segments({}, prof, "firewall") == [
        {"segment": desc(2, "A"), "outbound": []}
    ]


def test_edge_segments_that_are_not_a_list_are_left_alone():
    prof = {"segments": [{"segment": desc(2, "A")}]}
    assert missing_segments({"segments": 5}, prof, "firewall") == []


def test_json_mode_module_plans_none_and_then_only_the_edit():
    raw = {"segments": [], "syslog_forwarding": None, "futureKey": [1]}
    rows = [make_module_json("firewall", copy.deepcopy(raw))]
    mod = ConfigurationModuleList.model_validate_json(json.dumps(rows)).root[0]
    assert mod.raw_mode == "json"
    assert isinstance(mod, FirewallConfigurationModule)
    plan = plan_module(mod, level=EDGE)
    assert (plan.operation, plan.data) == ("none", raw)
    mod.data.syslog_forwarding = True
    plan = plan_module(mod, level=EDGE)
    assert plan.data == {**raw, "syslog_forwarding": True}


def test_plan_data_does_not_alias_the_module_snapshot():
    mod = fw(EDGE_RAW)
    plan = plan_module(mod, level=EDGE)
    plan.data["segments"].clear()
    assert mod.raw_data == EDGE_RAW


# --- reconciliation with a plain dict as the data --------------------------


def test_dict_data_with_no_edit_does_not_write_just_to_reconcile():
    mod, profile = fw(EDGE_RAW), fw(PROFILE_RAW, module_id=2)
    plan = plan_module(mod, copy.deepcopy(EDGE_RAW), level=EDGE, profile=profile)
    assert (plan.operation, plan.data, plan.changes) == ("none", EDGE_RAW, ())
    assert plan.reconciled_segments == ("Guest",)


def test_dict_data_edit_carries_the_reconciled_segments_along():
    mod, profile = fw(EDGE_RAW), fw(PROFILE_RAW, module_id=2)
    data = copy.deepcopy(EDGE_RAW)
    data["firewall_enabled"] = False
    plan = plan_module(mod, data, level=EDGE, profile=profile)
    assert plan.operation == "update"
    assert plan.data == {
        **EDGE_RAW,
        "firewall_enabled": False,
        "segments": [
            EDGE_RAW["segments"][0],
            {"segment": desc(2, "Guest", vendorKey="x"), "outbound": []},
        ],
    }
    assert [c.path for c in plan.changes] == ["firewall_enabled"]
    assert plan.reconciled_segments == ("Guest",)
    assert plan.render().splitlines() == [
        "firewall: update",
        "  + firewall_enabled: false",
        "  reconciled segments: Guest",
    ]


def test_dict_data_without_a_segments_list_gets_the_reconciled_segments():
    mod, profile = fw({}), fw(PROFILE_RAW, module_id=2)
    assert plan_module(mod, {}, level=EDGE, profile=profile).operation == "none"
    plan = plan_module(mod, {"firewall_enabled": False}, level=EDGE, profile=profile)
    assert [s["segment"]["name"] for s in plan.data["segments"]] == [
        "Global Segment",
        "Guest",
    ]
    assert [c.path for c in plan.changes] == ["firewall_enabled"]


def test_dict_data_keeps_its_own_version_of_a_reconciled_segment():
    mod, profile = fw(EDGE_RAW), fw(PROFILE_RAW, module_id=2)
    data = copy.deepcopy(EDGE_RAW)
    guest = {"segment": desc(2, "Guest", vendorKey="x"), "outbound": [{"name": "b"}]}
    data["segments"].append(guest)
    plan = plan_module(mod, data, level=EDGE, profile=profile)
    assert plan.data["segments"][1] == guest
    assert len(plan.data["segments"]) == 2


# --- levels at which a field is not part of the wire form ------------------

PROFILE = ConfigContext.PROFILE


def ds(raw: dict[str, Any], *, module_id: int = 1) -> DeviceSettingsConfigurationModule:
    return DeviceSettingsConfigurationModule.model_validate(
        make_module_json("deviceSettings", copy.deepcopy(raw), module_id=module_id)
    )


DS_RAW: dict[str, Any] = {
    "lan": {"networks": [], "interfaces": []},
    "segments": [{"segment": desc(0, "Global Segment")}],
}


def test_profile_patch_that_sets_an_override_flag_is_saved():
    mod = fw(PROFILE_RAW, module_id=2)
    _ = mod.data.for_profile()
    _ = mod.data.merge_patch(
        {
            "segments": {
                "Global Segment": {
                    "outbound": {
                        "block": {"action": {"allow_or_deny": "deny"}, "match": {}}
                    }
                }
            }
        }
    )
    plan = plan_module(mod, level=PROFILE)  # merge_patch set outbound_override
    assert plan.operation == "update"
    assert [c.path for c in plan.changes] == [
        'segments["Global Segment"].outbound[block]'
    ]
    assert "outboundOverride" not in plan.data["segments"][0]


def test_profile_ntp_patch_is_saved():
    mod = ds({**DS_RAW, "ntp": {"enabled": False, "servers": []}})
    _ = mod.data.for_profile()
    _ = mod.data.merge_patch({"ntp": {"enabled": True}})
    plan = plan_module(mod, level=PROFILE)  # merge_patch set ntp.override
    assert [c.path for c in plan.changes] == ["ntp.enabled"]
    assert "override" not in plan.data["ntp"]


def test_edge_edit_to_a_section_the_edge_dump_omits_is_refused():
    raw = {**DS_RAW, "ntp": {"override": True, "enabled": False, "servers": []}}
    mod = ds(raw)
    _ = mod.data.for_edge()
    _ = mod.data.merge_patch({"ntp": {"enabled": True}})
    with pytest.raises(LossyFieldError) as exc:
        plan_module(mod, level=EDGE)
    assert exc.value.fields == ["ntp"]


def test_edge_edit_to_a_section_the_edge_raw_lacks_is_refused():
    mod = ds(DS_RAW)
    _ = mod.data.for_edge()
    _ = mod.data.merge_patch({"ntp": {"enabled": True}})
    with pytest.raises(LossyFieldError) as exc:
        plan_module(mod, level=EDGE)
    assert exc.value.fields == ["ntp"]


def test_edge_section_the_dump_omits_does_not_block_an_unrelated_edit():
    raw = {**DS_RAW, "ntp": {"override": True, "enabled": False, "servers": []}}
    mod = ds(raw)
    _ = mod.data.for_edge()
    mod.data.lan.management.cidr_prefix = 24
    plan = plan_module(mod, level=EDGE)
    assert plan.data["ntp"] == raw["ntp"]


def test_override_flag_the_document_lacks_is_named_as_one():
    raw = {
        **DS_RAW,
        "segments": [{"segment": desc(0, "Global Segment"), "bfd": {"enabled": False}}],
    }
    mod = ds(raw)
    _ = mod.data.for_edge()
    _ = mod.data.merge_patch(
        {"segments": {"Global Segment": {"bfd": {"enabled": True}}}}
    )
    with pytest.raises(LossyFieldError) as exc:
        plan_module(mod, level=EDGE)
    assert exc.value.fields == ['segments["Global Segment"].bfd.override']
    assert "override flag" in str(exc.value)


# --- defaults generated per parse are not the caller's change --------------

QOS_RULE: dict[str, Any] = {
    "name": "r1",
    "match": {},
    "action": {
        "edge2EdgeRouteAction": {},
        "edge2DataCenterRouteAction": {},
        "edge2CloudRouteAction": {},
        "QoS": {},
    },
}


def qos(*rules: dict[str, Any]) -> QosConfigurationModule:
    raw = {"segments": [{"segment": desc(0, "Global Segment"), "rules": list(rules)}]}
    return QosConfigurationModule.model_validate(
        make_module_json("QOS", copy.deepcopy(raw))
    )


def bgp_ds() -> DeviceSettingsConfigurationModule:
    bgp = {
        "enabled": True,
        "ASN": "65000",
        "neighbors": [{"neighborIp": "10.0.0.1", "neighborAS": "65001"}],
        "filters": [{"name": "f1", "rules": []}],
    }
    segment = {"segment": desc(0, "Global Segment"), "bgp": bgp}
    return ds({**DS_RAW, "segments": [segment]})


def test_no_edit_does_not_stamp_a_generated_rule_id():
    plan = plan_module(qos(QOS_RULE), level=EDGE)
    assert (plan.operation, plan.changes) == ("none", ())
    assert plan.data["segments"][0]["rules"] == [QOS_RULE]


@pytest.mark.parametrize("level", [EDGE, PROFILE])
def test_no_edit_does_not_stamp_generated_bgp_ids(level: ConfigContext):
    mod = bgp_ds()
    plan = plan_module(mod, level=level)
    assert (plan.operation, plan.changes) == ("none", ())
    assert plan.data == mod.raw_data


def test_an_edit_in_a_rule_without_an_id_does_not_stamp_one():
    other = {
        **QOS_RULE,
        "name": "r2",
        "ruleLogicalId": "00000000-0000-0000-0000-0000000000aa",
    }
    mod = qos(QOS_RULE, other)
    mod.data.segments[0].rules[0].match.source_ip = "10.1.1.1"
    plan = plan_module(mod, level=EDGE)
    r1, r2 = plan.data["segments"][0]["rules"]
    assert "ruleLogicalId" not in r1 and r2 == other
    assert [str(c.path) for c in plan.changes] == [
        'segments["Global Segment"].rules[r1].match.sip'
    ]


def test_an_edit_elsewhere_does_not_stamp_bgp_ids():
    mod = bgp_ds()
    mod.data.lan.management.cidr_prefix = 24
    plan = plan_module(mod, level=EDGE)
    bgp = plan.data["segments"][0]["bgp"]
    assert "id" not in bgp["neighbors"][0] and "id" not in bgp["filters"][0]
    assert [str(c.path) for c in plan.changes] == ["lan.management"]


def test_a_new_item_keeps_the_id_it_is_given():
    mod = qos(QOS_RULE)
    mod.data.segments[0].rules.append(
        type(mod.data.segments[0].rules[0]).model_validate({**QOS_RULE, "name": "r2"})
    )
    plan = plan_module(mod, level=EDGE)
    r1, r2 = plan.data["segments"][0]["rules"]
    assert "ruleLogicalId" not in r1 and "ruleLogicalId" in r2


# --- a deviceSettings segment the edge lacks -------------------------------


def static_route(destination: str) -> dict[str, Any]:
    return {
        "destination": destination,
        "netmask": "255.255.255.0",
        "gateway": "192.0.2.1",
        "cidrPrefix": 24,
    }


DS_PROFILE_RAW: dict[str, Any] = {
    "lan": {"networks": [], "interfaces": []},
    "segments": [
        {"segment": desc(0, "Global Segment"), "routes": {"static": []}},
        {
            "segment": desc(2, "Guest"),
            "routes": {
                "static": [static_route("192.0.2.0")],
                "staticV6": [],
                "icmpProbes": [],
            },
            "bgp": {"enabled": True, "ASN": "65000"},
        },
    ],
}
DS_EDGE_RAW: dict[str, Any] = {
    "lan": {
        "networks": [{"vlanId": 1, "name": "n", "cost": 10, "override": False}],
        "interfaces": [],
    },
    "segments": [{"segment": desc(0, "Global Segment"), "routes": {"static": []}}],
}
DS_PLACEHOLDER: dict[str, Any] = {
    "segment": desc(2, "Guest"),
    "routes": {"static": []},
}


def test_an_unrelated_edit_adds_a_device_settings_segment_with_empty_static_routes():
    mod, profile = ds(DS_EDGE_RAW), ds(DS_PROFILE_RAW, module_id=2)
    assert plan_module(mod, level=EDGE, profile=profile).operation == "none"
    mod.data.lan.networks[0].cost += 1
    plan = plan_module(mod, level=EDGE, profile=profile)
    assert plan.operation == "update"
    assert plan.data["segments"] == [DS_EDGE_RAW["segments"][0], DS_PLACEHOLDER]
    assert plan.reconciled_segments == ("Guest",)
    assert [str(c.path) for c in plan.changes] == ["lan.networks[1].cost"]


def test_the_device_settings_placeholder_copies_nothing_of_the_profile_segment():
    mod, profile = ds(DS_EDGE_RAW), ds(DS_PROFILE_RAW, module_id=2)
    mod.data.lan.networks[0].cost += 1
    written = plan_module(mod, level=EDGE, profile=profile).data["segments"][1]
    assert set(written) == {"segment", "routes"}
    assert written["routes"] == {"static": []}
    assert DS_PROFILE_RAW["segments"][1]["routes"]["static"]  # the profile is untouched


def test_a_device_settings_segment_the_edge_drops_comes_back_with_empty_static_routes():
    raw = copy.deepcopy(DS_EDGE_RAW)
    raw["segments"].append(
        {
            "segment": desc(2, "Guest"),
            "routes": {"static": [static_route("198.51.100.0")]},
        }
    )
    mod, profile = ds(raw), ds(DS_PROFILE_RAW, module_id=2)
    del mod.data.segments[1]
    plan = plan_module(mod, level=EDGE, profile=profile)
    assert plan.operation == "update"
    assert plan.data["segments"] == [raw["segments"][0], DS_PLACEHOLDER]
    assert plan.reconciled_segments == ("Guest",)


def test_a_device_settings_segment_in_a_profile_without_routes_still_gets_static():
    profile_raw = {
        **DS_PROFILE_RAW,
        "segments": [
            DS_PROFILE_RAW["segments"][0],
            {"segment": desc(2, "Guest")},
        ],
    }
    mod, profile = ds(DS_EDGE_RAW), ds(profile_raw, module_id=2)
    mod.data.lan.networks[0].cost += 1
    written = plan_module(mod, level=EDGE, profile=profile).data["segments"][1]
    assert written == DS_PLACEHOLDER
