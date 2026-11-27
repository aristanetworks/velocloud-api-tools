"""``references``, ``provided``, ``check_references`` and the patches that mend a hub.

What the data refers to, what it provides, and which references name nothing.
"""

import dataclasses
import uuid
from typing import Any, Literal

import pytest
import segment_helpers
from hub_helpers import HUB_A, HUB_B, backhaul_entry, backhaul_rule, ds_with_vpn
from pyvelo.client.exceptions import DanglingReferenceError
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.config.reference_mending import hub_reference_mending
from pyvelo.core import Key, MendingPatch, Path, Profile, VcoModel, backhaul_hub_mending
from pyvelo.core.references import (
    Reference,
    ReferenceFindings,
    Sets,
    can_provide,
    can_refer,
    check_references,
    provided,
    reference_text,
    references,
    removal_patch,
    union,
)

GLOBAL = segment_helpers.GLOBAL
GUEST = segment_helpers.GUEST
VOICE = segment_helpers.VOICE
SERVICE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def ds_data(
    *segments: segment_helpers.Segment | dict[str, Any], **extra: Any
) -> DeviceSettingsData:
    """Device settings data with *segments* and the keys of *extra* beside them.

    Each of *segments* is a segment, or a built entry.
    """
    entries = [
        segment_helpers.ds_entry(s) if isinstance(s, tuple) else s for s in segments
    ]
    return DeviceSettingsData.model_validate(
        {"lan": {"networks": [], "interfaces": []}, "segments": entries, **extra}
    )


def qos_data(
    *segments: tuple[segment_helpers.Segment, list[dict[str, Any]]],
) -> QosData[Profile]:
    """QoS data with one entry per ``(segment, rules)`` of *segments*."""
    return QosData[Profile].model_validate(
        {"segments": [segment_helpers.qos_entry(s, rules=r) for s, r in segments]}
    )


def hub_rule_data(segment: segment_helpers.Segment, hub_id: str) -> QosData[Profile]:
    entry = backhaul_entry(HUB_A, SERVICE)
    entry["edgeLogicalId"] = hub_id
    return qos_data((segment, [backhaul_rule("Web", entry)]))


def sets(**named: set[object]) -> Sets:
    """Sets by name.

    A name ``segment_2`` is the set ``segment`` of segment 2 (a number after the last
    underscore).
    """
    result: dict[tuple[str, int | None], frozenset[object]] = {}
    for name, members in named.items():
        base, _, tail = name.rpartition("_")
        key = (base, int(tail)) if tail.isdigit() else (name, None)
        result[key] = frozenset(members)
    return result


def test_routed_interface_minus_one_is_not_checked() -> None:
    data = ds_data(GLOBAL, routedInterfaces=[{"name": "GE3", "segmentId": -1}])
    assert references("deviceSettings", data) == ()


def test_each_declared_field_is_found() -> None:
    data = ds_data(
        GLOBAL,
        GUEST,
        routedInterfaces=[
            {
                "name": "GE3",
                "segmentId": 2,
                "subinterfaces": [
                    {
                        "subinterfaceId": 1,
                        "subinterfaceType": "SUB_INTERFACE",
                        "segmentId": 2,
                    }
                ],
            }
        ],
        loopbackInterfaces={"LO1": {"cidrIp": "10.0.0.1", "segmentId": 2}},
        lan={
            "networks": [{"vlanId": 100, "name": "users", "segmentId": 2}],
            "interfaces": [],
        },
    )
    found = {str(r.path): r for r in references("deviceSettings", data)}
    expected = {
        "routed_interfaces[GE3].segment_id",
        "routed_interfaces[GE3].subinterfaces[#0].segment_id",
        "lan.networks[100].segment_id",
        "loopback_interfaces.LO1.segment_id",
    }
    assert set(found) == expected
    for reference in found.values():
        assert reference == Reference(
            "deviceSettings", reference.path, 2, "segment", None
        )


def test_a_backhaul_entry_is_found_in_its_segment() -> None:
    data = qos_data(
        (GLOBAL, []), (GUEST, [backhaul_rule("Web", backhaul_entry(HUB_A, SERVICE))])
    )
    (reference,) = references("QOS", data)
    assert reference.module == "QOS"
    assert reference.value == uuid.UUID(HUB_A.logical_id)
    assert (reference.set_name, reference.segment_id) == ("backhaul_hub", 2)
    assert reference.path.parts[:4] == ("segments", Key("Guest"), "rules", Key("Web"))
    assert reference.path.parts[-1] == "edge_logical_id"


def test_a_value_that_is_missing_or_none_is_left_out() -> None:
    entry = backhaul_entry(HUB_A, SERVICE)
    del entry["edgeLogicalId"]
    data = qos_data((GUEST, [backhaul_rule("Web", entry)]))
    assert references("QOS", data) == ()


def test_a_module_is_walked_for_what_it_can_provide_or_refer_to_only() -> None:
    # A QoS module refers to hubs and provides nothing, so the walk for the
    # sets skips its rules; a firewall module does neither.
    assert (can_provide(QosData), can_refer(QosData)) == (False, True)
    assert (can_provide(DeviceSettingsData), can_refer(DeviceSettingsData)) == (
        True,
        True,
    )
    assert (can_provide(FirewallData), can_refer(FirewallData)) == (False, False)
    qos = hub_rule_data(GLOBAL, HUB_A.logical_id)
    assert provided(qos) == {}
    assert references("QOS", qos)


def test_provided_sets() -> None:
    data = ds_data(GLOBAL, ds_with_vpn(GUEST, backhaul=(HUB_A,)))
    assert provided(data) == {
        ("segment", None): {0, 2},
        ("backhaul_hub", 2): {uuid.UUID(HUB_A.logical_id)},
    }


def test_a_segment_with_no_backhaul_hubs_provides_no_set() -> None:
    data = ds_data(GLOBAL)
    assert provided(data) == {("segment", None): {0}}


def test_the_hubs_of_each_segment_are_apart() -> None:
    data = ds_data(
        ds_with_vpn(GLOBAL, backhaul=(HUB_A,)), ds_with_vpn(GUEST, backhaul=(HUB_B,))
    )
    found = provided(data)
    assert found[("backhaul_hub", 0)] == {uuid.UUID(HUB_A.logical_id)}
    assert found[("backhaul_hub", 2)] == {uuid.UUID(HUB_B.logical_id)}


def test_union_joins_the_sets_of_the_same_name() -> None:
    joined = union(
        {("segment", None): {0}, ("backhaul_hub", 2): {1}},
        {("segment", None): {2}},
        {},
    )
    assert joined == {("segment", None): {0, 2}, ("backhaul_hub", 2): {1}}
    assert all(isinstance(v, frozenset) for v in joined.values())


def _segment_data(segment_id: int, *, path: str = "GE3") -> DeviceSettingsData:
    return ds_data(GLOBAL, routedInterfaces=[{"name": path, "segmentId": segment_id}])


NOW = sets(segment={0, 2})


def _check(
    edited: VcoModel,
    read: VcoModel | None,
    now: Sets,
    as_read: Sets,
    module: str = "deviceSettings",
) -> ReferenceFindings:
    return check_references([(module, edited, read)], now, as_read)


def test_a_segment_reference_to_a_member_is_accepted() -> None:
    findings = _check(_segment_data(2), None, NOW, NOW)
    assert findings.dangling == findings.kept == ()


def test_a_segment_reference_to_a_non_member_is_dangling() -> None:
    findings = _check(_segment_data(7), None, NOW, NOW)
    assert [r.value for r in findings.dangling] == [7]
    assert findings.kept == ()


def test_a_segment_reference_held_as_read_that_was_no_member_is_kept() -> None:
    read = _segment_data(7)
    findings = _check(_segment_data(7), read, NOW, NOW)
    assert findings.dangling == ()
    assert [r.value for r in findings.kept] == [7]


def test_a_segment_reference_that_was_a_member_as_read_and_no_longer_is_dangling() -> (
    None
):
    read = _segment_data(2)
    findings = _check(_segment_data(2), read, sets(segment={0}), NOW)
    assert [r.value for r in findings.dangling] == [2]
    assert findings.kept == ()


def test_a_segment_reference_with_a_new_value_is_dangling() -> None:
    read = _segment_data(7)
    findings = _check(_segment_data(8), read, NOW, NOW)
    assert [r.value for r in findings.dangling] == [8]


def test_a_segment_reference_at_a_new_path_is_dangling() -> None:
    read = _segment_data(7, path="GE3")
    findings = _check(_segment_data(7, path="GE4"), read, NOW, NOW)
    assert [r.value for r in findings.dangling] == [7]
    assert findings.kept == ()


NOW_HUBS = sets(backhaul_hub_2={uuid.UUID(HUB_A.logical_id)})
HUB_B_ID = uuid.UUID(HUB_B.logical_id)


def _hub_data(
    hub_id: str, segment: segment_helpers.Segment = GUEST
) -> QosData[Profile]:
    return hub_rule_data(segment, hub_id)


def test_a_backhaul_reference_to_a_member_is_accepted() -> None:
    findings = _check(_hub_data(HUB_A.logical_id), None, NOW_HUBS, NOW_HUBS, "QOS")
    assert findings.dangling == findings.kept == ()


def test_a_backhaul_reference_to_a_non_member_is_dangling() -> None:
    findings = _check(_hub_data(HUB_B.logical_id), None, NOW_HUBS, NOW_HUBS, "QOS")
    assert [r.value for r in findings.dangling] == [HUB_B_ID]
    assert findings.kept == ()


def test_a_backhaul_reference_held_as_read_that_was_no_member_is_kept() -> None:
    read = _hub_data(HUB_B.logical_id)
    findings = _check(_hub_data(HUB_B.logical_id), read, NOW_HUBS, NOW_HUBS, "QOS")
    assert findings.dangling == ()
    assert [r.value for r in findings.kept] == [HUB_B_ID]


def test_a_backhaul_reference_that_was_a_member_as_read_and_no_longer_is_dangling() -> (
    None
):
    read = _hub_data(HUB_A.logical_id)
    findings = _check(_hub_data(HUB_A.logical_id), read, sets(), NOW_HUBS, "QOS")
    assert [r.value for r in findings.dangling] == [uuid.UUID(HUB_A.logical_id)]
    assert findings.kept == ()


def test_a_backhaul_reference_with_a_new_value_is_dangling() -> None:
    read = _hub_data(HUB_B.logical_id)
    findings = _check(
        _hub_data("99999999-9999-4999-8999-999999999999"),
        read,
        NOW_HUBS,
        NOW_HUBS,
        "QOS",
    )
    assert len(findings.dangling) == 1
    assert findings.kept == ()


def test_a_backhaul_reference_at_a_new_path_is_dangling() -> None:
    read = _hub_data(HUB_B.logical_id)
    edited = qos_data(
        (
            GUEST,
            [
                backhaul_rule(
                    "Other",
                    {**backhaul_entry(HUB_B, SERVICE)},
                )
            ],
        )
    )
    findings = _check(edited, read, NOW_HUBS, NOW_HUBS, "QOS")
    assert [r.value for r in findings.dangling] == [HUB_B_ID]
    assert findings.kept == ()


def test_a_per_segment_reference_needs_its_own_segment() -> None:
    other_segment_only = sets(
        backhaul_hub_0={uuid.UUID(HUB_A.logical_id)}, backhaul_hub_2=set()
    )
    findings = _check(
        _hub_data(HUB_A.logical_id), None, other_segment_only, other_segment_only, "QOS"
    )
    assert [r.segment_id for r in findings.dangling] == [2]


def test_a_reference_in_a_segment_with_no_set_is_dangling() -> None:
    findings = _check(_hub_data(HUB_A.logical_id), None, sets(), sets(), "QOS")
    assert len(findings.dangling) == 1


def test_several_targets_are_checked_each_with_its_own_module() -> None:
    findings = check_references(
        [
            ("deviceSettings", _segment_data(7), None),
            ("QOS", _hub_data(HUB_B.logical_id), None),
        ],
        union(NOW, NOW_HUBS),
        union(NOW, NOW_HUBS),
    )
    assert [r.module for r in findings.dangling] == ["deviceSettings", "QOS"]


def test_the_text_of_a_reference() -> None:
    reference = Reference(
        "QOS",
        Path(("segments", Key("Guest"), "rules", Key("Web"), "edge_logical_id")),
        HUB_B_ID,
        "backhaul_hub",
        2,
    )
    assert reference_text(reference) == (
        f"QOS segments[Guest].rules[Web].edge_logical_id: {HUB_B_ID} "
        "is not a backhaul_hub of segment 2"
    )


def test_the_text_of_a_reference_names_its_segment_where_it_has_the_name() -> None:
    reference = Reference(
        "business_policy",
        Path(("segments", Key("Guest"), "rules", Key("Web"), "edge_logical_id")),
        HUB_B_ID,
        "backhaul_hub",
        2,
        "Guest",
    )
    assert reference_text(reference) == (
        f"business_policy segments[Guest].rules[Web].edge_logical_id: {HUB_B_ID} "
        "is not a backhaul_hub of segment Guest"
    )


def test_the_text_of_a_reference_to_a_global_set_has_no_segment() -> None:
    reference = Reference(
        "deviceSettings",
        Path(("lan", "networks", Key(100), "segment_id")),
        7,
        "segment",
        None,
    )
    assert reference_text(reference) == (
        "deviceSettings lan.networks[100].segment_id: 7 is not a segment"
    )


def test_the_error_lists_every_reference() -> None:
    findings = check_references(
        [
            ("deviceSettings", _segment_data(7), None),
            ("QOS", _hub_data(HUB_B.logical_id), None),
        ],
        union(NOW, NOW_HUBS),
        union(NOW, NOW_HUBS),
    )
    error = DanglingReferenceError(findings.dangling)
    assert error.references == findings.dangling
    assert len(error.references) == 2
    text = str(error)
    for reference in findings.dangling:
        assert reference_text(reference) in text
    assert "refused" in text
    assert "vpn.backhaul_edges" in text
    assert "segments" in text
    with pytest.raises(DanglingReferenceError):
        raise error


def test_the_message_names_a_rule_that_backhauls_nowhere_only_for_a_hub() -> None:
    segment = DanglingReferenceError(
        [Reference("deviceSettings", Path(("x",)), 7, "segment", None)]
    )
    hub = DanglingReferenceError(
        [Reference("QOS", Path(("x",)), "x", "backhaul_hub", 0)]
    )

    assert "backhauls nowhere" not in str(segment)
    assert "backhauls nowhere" in str(hub)


def test_the_sources_are_a_sentence_of_their_own() -> None:
    both = DanglingReferenceError(
        [
            Reference("deviceSettings", Path(("x",)), 7, "segment", None),
            Reference("QOS", Path(("x",)), "x", "backhaul_hub", 0),
        ]
    )

    assert (
        ". Where each comes from: a segment is one of the device settings "
        "`segments` of the profile; a backhaul hub is one of the segment's "
        "`vpn.backhaul_edges` in the profile's device settings." in str(both)
    )
    assert "reference; " not in str(both)


def test_with_no_member_patch_the_only_hubs_sentence_names_the_list_by_attribute() -> (
    None
):
    reference = Reference("business_policy", Path(("x",)), "x", "backhaul_hub", 0)

    text = str(DanglingReferenceError([reference], only_hubs=[reference]))

    assert (
        "Make the hub a backhaul hub again (one of the segment's "
        "`vpn.backhaul_edges`); or first give the rule another hub or change its "
        "`route_policy`, and the next `plan()` offers"
    ) in text
    for wire in ("backHaulEdges", "routePolicy"):
        assert wire not in text


# -- the patches that mend a hub reference ------------------------------------------


def _dangling_hub(
    *,
    named: bool = True,
    segments: tuple[segment_helpers.Segment, ...] = (GUEST,),
    rules: tuple[str, ...] = ("Web",),
) -> tuple[QosData[Profile], tuple[Reference, ...]]:
    """QoS data whose *rules* in each of *segments* backhaul to Hub-B, and its refs.

    Hub-B is no backhaul hub of any segment (``NOW_HUBS``), so every reference
    dangles.
    """
    entry = backhaul_entry(HUB_B, SERVICE)
    if not named:
        del entry["name"]
    data = qos_data(
        *((s, [backhaul_rule(rule, entry) for rule in rules]) for s in segments)
    )
    return data, _check(data, None, NOW_HUBS, NOW_HUBS, "QOS").dangling


def _out_of_the_rule(hub: str, segment: str = GUEST[1]) -> dict[str, Any]:
    """The patch that deletes *hub* from the backhaul entries of rule ``Web``."""
    entries = {"backhaul_edges": {hub: {"_op": "delete"}}}
    action = {"edge_to_edge_route_action": {"route_cfg": entries}}
    return {"segments": {segment: {"rules": {"Web": {"action": action}}}}}


def _mending(
    data: VcoModel,
    dangling: tuple[Reference, ...],
    *,
    hub_lists: dict[int, set[object]] | None = None,
    target_type: Literal["edge", "profile"] = "profile",
) -> tuple[MendingPatch, ...]:
    return hub_reference_mending(
        dangling, {"QOS": data}, hub_lists or {}, target_type=target_type
    )


def test_the_removal_patch_deletes_the_entry_from_its_rule() -> None:
    data, (reference,) = _dangling_hub()

    assert removal_patch(data, reference) == _out_of_the_rule(HUB_B.name)


def test_the_removal_patch_for_an_entry_with_no_name_gives_its_logical_id() -> None:
    data, (reference,) = _dangling_hub(named=False)

    assert removal_patch(data, reference) == _out_of_the_rule(HUB_B.logical_id)


def test_there_is_no_removal_patch_for_a_path_that_names_no_item() -> None:
    data, (reference,) = _dangling_hub()
    parts = list(reference.path.parts)
    parts[parts.index(Key("Web"))] = Key("Nope")

    assert removal_patch(data, dataclasses.replace(reference, path=Path(parts))) is None


def test_each_patch_names_its_module_by_its_python_name() -> None:
    data, dangling = _dangling_hub()

    mending = _mending(data, dangling)

    assert [(m.kind, m.module) for m in mending] == [
        ("remove_reference", "business_policy"),
        ("add_member", "device_settings"),
    ]


def test_the_member_patch_makes_the_hub_a_backhaul_hub_of_its_segment() -> None:
    data, dangling = _dangling_hub()

    (_, add) = _mending(data, dangling)

    assert add == backhaul_hub_mending(GUEST[1], HUB_B.name)


def test_the_member_patch_for_an_entry_with_no_name_gives_its_logical_id() -> None:
    data, dangling = _dangling_hub(named=False)

    (_, add) = _mending(data, dangling)

    assert add == backhaul_hub_mending(GUEST[1], HUB_B.logical_id, by_logical_id=True)


def test_the_member_patch_leaves_the_hub_list_alone_when_the_hub_is_in_it() -> None:
    data, dangling = _dangling_hub()

    (_, add) = _mending(data, dangling, hub_lists={GUEST[0]: {HUB_B_ID}})

    assert add == backhaul_hub_mending(GUEST[1], HUB_B.name, in_hub_list=True)


def test_two_rules_of_one_segment_need_one_member_patch() -> None:
    data, dangling = _dangling_hub(rules=("Web", "Mail"))

    mending = _mending(data, dangling)

    assert [m.kind for m in mending] == [
        "remove_reference",
        "remove_reference",
        "add_member",
    ]


def test_two_rules_that_name_one_hub_two_ways_need_one_member_patch() -> None:
    named = backhaul_entry(HUB_B, SERVICE)
    unnamed = {key: value for key, value in named.items() if key != "name"}
    data = qos_data(
        (GUEST, [backhaul_rule("Web", named), backhaul_rule("Mail", unnamed)])
    )
    dangling = _check(data, None, NOW_HUBS, NOW_HUBS, "QOS").dangling

    mending = _mending(data, dangling)

    assert [m.kind for m in mending] == [
        "remove_reference",
        "remove_reference",
        "add_member",
    ]
    assert mending[2] == backhaul_hub_mending(GUEST[1], HUB_B.name)


def test_the_same_rule_in_two_segments_needs_a_member_patch_for_each() -> None:
    data, dangling = _dangling_hub(segments=(GLOBAL, GUEST))

    mending = _mending(data, dangling)

    assert [m.kind for m in mending] == [
        "remove_reference",
        "remove_reference",
        "add_member",
        "add_member",
    ]
    assert [m.patch for m in mending[:2]] == [
        _out_of_the_rule(HUB_B.name, GLOBAL[1]),
        _out_of_the_rule(HUB_B.name, GUEST[1]),
    ]
    assert list(mending[2:]) == [
        backhaul_hub_mending(GLOBAL[1], HUB_B.name),
        backhaul_hub_mending(GUEST[1], HUB_B.name),
    ]


def test_a_segment_reference_gets_no_patch() -> None:
    segment_data = _segment_data(7)
    (segment,) = _check(segment_data, None, NOW, NOW).dangling
    data, dangling = _dangling_hub()
    edited = {"deviceSettings": segment_data, "QOS": data}

    alone = hub_reference_mending([segment], edited, {}, target_type="profile")
    both = hub_reference_mending(
        [segment, *dangling], edited, {}, target_type="profile"
    )

    assert alone == ()
    assert both == _mending(data, dangling)


def test_on_an_edge_the_member_patch_is_for_the_profile() -> None:
    data, dangling = _dangling_hub()

    remove, add = _mending(data, dangling, target_type="edge")

    assert (remove.target_type, add.target_type) == ("edge", "profile")


def test_the_message_says_that_on_an_edge_the_profile_is_edited() -> None:
    data, dangling = _dangling_hub()
    mending = _mending(data, dangling, target_type="edge")

    on_edge = str(DanglingReferenceError(dangling, on_edge=True, mending=mending))
    on_profile = str(DanglingReferenceError(dangling, mending=mending))
    unmended = str(DanglingReferenceError(dangling, on_edge=True))

    assert "This is an Edge" in on_edge
    assert "profile's device settings" in on_edge
    # The Edge state holds the profile as loaded, so the Edge is loaded again.
    assert "then load the Edge again and redo its edit" in on_edge
    assert "This is an Edge" not in on_profile
    assert "send that patch to the profile's device settings" not in on_profile
    assert "send {" not in unmended
