"""ConfigState.replace_module: the module as stored in place.

The old objects are detached.
"""

import copy
import gc
import weakref
from typing import Any, cast

import pytest
from pydantic import MISSING
from pyvelo.client.exceptions import DetachedObjectError
from pyvelo.client.models.configuration.firewall.rules import FirewallOutboundRule
from pyvelo.client.models.configuration.firewall.segment import FirewallSegment
from pyvelo.client.models.configuration.qos.root import ServiceRateLimit
from pyvelo.client.schemas.configuration import (
    DeviceSettingsConfigurationModule,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
)
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.state import ConfigPlan, ConfigState
from pyvelo.core import VcoModel
from pyvelo.core.layers import Edge
from pyvelo.core.rebase import Key, Path
from pyvelo.testing import as_stored, make_module_json


def desc(sid: int, name: str) -> dict[str, object]:
    return {
        "segmentId": sid,
        "name": name,
        "type": "REGULAR",
        "segmentLogicalId": f"00000000-0000-0000-0000-{sid:012d}",
    }


EDGE_FW: dict[str, Any] = {
    "segments": [
        {"segment": desc(0, "Global Segment"), "outbound": [], "futureKey": 1}
    ],
    "syslog_forwarding": False,
}
EDGE_QOS: dict[str, Any] = {
    "segments": [{"segment": desc(0, "Global Segment"), "rules": []}]
}
EDGE_DS: dict[str, Any] = {
    "lan": {},
    "segments": [{"segment": desc(0, "Global Segment")}],
}
PROFILE_FW: dict[str, Any] = {
    "segments": [
        {
            "segment": desc(0, "Global Segment"),
            "outbound": [
                {"name": "allow-all", "action": {"allow_or_deny": "allow"}, "match": {}}
            ],
        },
        {"segment": desc(2, "Guest"), "outbound": []},
    ]
}
PROFILE_QOS: dict[str, Any] = {
    "segments": [{"segment": desc(0, "Global Segment"), "rules": []}]
}
IN_PLACE = "objects taken from the state before a save were changed in place at: "
REDO = (
    ". The state holds the module as stored; take the objects from the state "
    "again and redo the change"
)


def fw_row() -> dict[str, Any]:
    return make_module_json(
        "firewall",
        copy.deepcopy(EDGE_FW),
        module_id=1,
        configuration_id=10,
        version="e1",
    )


def ds_row() -> dict[str, Any]:
    return make_module_json(
        "deviceSettings",
        copy.deepcopy(EDGE_DS),
        module_id=5,
        configuration_id=10,
        version="e1",
    )


def qos_row() -> dict[str, Any]:
    return make_module_json(
        "QOS", copy.deepcopy(EDGE_QOS), module_id=4, configuration_id=10, version="e1"
    )


def make_state(
    edge_rows: list[dict[str, Any]] | None = None, *, modules: bool = True
) -> ConfigState:
    """An edge state, its edge firewall module 1 by default.

    No ``modules`` without *modules*.
    """
    edge: dict[str, Any] = {"name": "Edge Specific Profile", "id": 10}
    if modules:
        edge["modules"] = [fw_row()] if edge_rows is None else edge_rows
    profile = EnterpriseConfigurationProfile.model_validate(
        {
            "name": "P",
            "id": 20,
            "modules": [
                make_module_json(
                    "firewall",
                    copy.deepcopy(PROFILE_FW),
                    module_id=2,
                    configuration_id=20,
                ),
                make_module_json(
                    "QOS", copy.deepcopy(PROFILE_QOS), module_id=3, configuration_id=20
                ),
            ],
        }
    )
    state = ConfigState(
        edge_config=EdgeConfiguration.model_validate(edge),
        profile_config=profile,
        target_type="edge",
        resolver_context=ResolverContext(enterprise_id=5),
    )
    state.take_snapshot()
    return state


def replace_firewall(
    state: ConfigState, data: dict[str, Any] | None = None
) -> tuple[FirewallConfigurationModule[Edge], FirewallConfigurationModule[Edge]]:
    """Replace the edge firewall module by it as stored with *data*.

    *data* is EDGE_FW by default.
    """
    old = state.module("firewall")
    assert isinstance(old, FirewallConfigurationModule)
    old = cast("FirewallConfigurationModule[Edge]", old)  # the state's edge layer
    new = as_stored(old, copy.deepcopy(EDGE_FW) if data is None else data)
    state.replace_module(old, new)
    return old, new


def rule(name: str) -> FirewallOutboundRule:
    return FirewallOutboundRule.model_validate(
        {"name": name, "match": {}, "action": {"allow_or_deny": "deny"}}
    )


def dump(model: VcoModel) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True)


def outbound_path(name: str) -> Path:
    return Path(("data", "segments", Key("Global Segment"), "outbound", Key(name)))


def test_replace_puts_the_stored_module_in_the_state() -> None:
    state = make_state()
    state.edge.firewall.firewall_enabled.set_local(False)

    _, new = replace_firewall(
        state, {**copy.deepcopy(EDGE_FW), "firewall_enabled": False}
    )

    assert state.module("firewall") is new
    assert state.edge.firewall is new.data
    assert state.plan().changed == ()
    assert state.get_field_changes() == []


def test_replace_binds_the_new_module_to_the_profile() -> None:
    expected = make_state().edge.firewall.segment("Guest")
    state = make_state()

    _, new = replace_firewall(state)

    guest = state.edge.firewall.segment("Guest")
    assert guest.segment.name == "Guest"
    assert dump(guest) == dump(expected)
    assert any(s is guest for s in new.data.segments)


def test_replace_leaves_the_other_modules_edits() -> None:
    state = make_state([fw_row(), qos_row()])
    state.edge.firewall.firewall_enabled.set_local(False)
    state.edge.qos.service_rate_limit.set_local(
        ServiceRateLimit(enabled=True, input_type="percent", value=50)
    )

    _ = replace_firewall(state, {**copy.deepcopy(EDGE_FW), "firewall_enabled": False})

    assert [m.name for m in state.plan().changed] == ["QOS"]
    assert [(c.module, c.path, c.before) for c in state.get_field_changes()] == [
        ("QOS", "service_rate_limit", MISSING)
    ]


def test_objects_taken_before_are_detached() -> None:
    state = make_state()
    segment = state.edge.firewall.segment("Global Segment")

    old, new = replace_firewall(state)

    with pytest.raises(
        DetachedObjectError, match=r"FirewallSegment\[Edge\]\.firewall_logging"
    ):
        segment.firewall_logging_enabled = True
    assert old.is_detached
    assert old.data.is_detached
    assert segment.is_detached
    assert new.is_detached is False
    assert new.data.is_detached is False
    state.edge.firewall.segment("Global Segment").firewall_logging_enabled = True
    snapshot = state.snapshot_for_target()
    assert snapshot is not None
    assert snapshot.modules is not MISSING
    assert not any(m.is_detached for m in snapshot.modules)


def test_in_place_edit_is_reported_by_the_next_plan() -> None:
    state = make_state()
    old_segment = state.edge.firewall.segment("Global Segment")
    rules = old_segment.outbound
    # The old module is held too: it and the segment both hold the edit, one path.
    old, _ = replace_firewall(state)

    rules.append(rule("new"))

    with pytest.raises(DetachedObjectError) as exc:
        _ = state.plan()
    (path,) = exc.value.paths
    assert path == outbound_path("new")
    assert path.parts[-2:] == ("outbound", Key("new"))
    assert str(exc.value) == f"firewall: {IN_PLACE}{path}{REDO}"
    assert old.is_detached


def test_it_is_reported_once() -> None:
    state = make_state()
    old_segment = state.edge.firewall.segment("Global Segment")
    rules = old_segment.outbound
    old, new = replace_firewall(state)
    del old, new  # only the segment and its list are held now
    rules.append(rule("new"))
    with pytest.raises(DetachedObjectError):
        _ = state.plan()

    assert state.plan().changed == ()

    # Compared from the objects as they are now: a further edit is its own report.
    rules.append(rule("newer"))
    with pytest.raises(DetachedObjectError) as exc:
        _ = state.plan()
    assert exc.value.paths == [outbound_path("newer")]
    assert state.plan().changed == ()
    assert old_segment.is_detached


def test_in_place_edits_of_two_replaced_modules_are_one_report() -> None:
    state = make_state([fw_row(), qos_row()])
    fw_segment = state.edge.firewall.segment("Global Segment")
    qos = state.edge.qos
    _ = replace_firewall(state)
    old_qos = state.module("QOS")
    state.replace_module(old_qos, as_stored(old_qos, copy.deepcopy(EDGE_QOS)))
    del old_qos

    fw_segment.outbound.append(rule("new"))
    qos.segments.clear()

    with pytest.raises(DetachedObjectError) as exc:
        _ = state.plan()
    removed = Path(("data", "segments", Key("Global Segment")))
    assert exc.value.paths == [outbound_path("new"), removed]
    assert str(exc.value) == (
        f"firewall: {IN_PLACE}{outbound_path('new')}{REDO}\n"
        f"QOS: {IN_PLACE}{removed}{REDO}"
    )
    assert state.plan().changed == ()


def test_an_object_moved_by_an_in_place_edit_is_not_reported_again() -> None:
    state = make_state()
    segment = state.edge.firewall.segment("Global Segment")
    old, _ = replace_firewall(state)
    guest = FirewallSegment[Edge].model_validate({"segment": desc(2, "Guest")})
    old.data.segments.insert(0, guest)  # the segment held is at position 1 now
    with pytest.raises(DetachedObjectError) as exc:
        _ = state.plan()
    assert exc.value.paths == [Path(("data", "segments", Key("Guest")))]

    ref = weakref.ref(old)
    del old
    _ = gc.collect()
    assert ref() is None

    # The segment's place as read, position 0, holds Guest in the module as
    # retaken: the segment is not compared there.
    assert state.plan().changed == ()
    assert segment.is_detached


def test_dropped_objects_cost_nothing() -> None:
    state = make_state()
    old = state.module("firewall")
    ref = weakref.ref(old.data)
    state.replace_module(old, as_stored(old, copy.deepcopy(EDGE_FW)))
    del old
    _ = gc.collect()
    assert ref() is None
    assert state.plan().changed == ()
    assert state._replaced == []  # pyright: ignore[reportPrivateUsage]

    # An inserted module: module(create=True) holds what it made until it is replaced.
    state = make_state(modules=False)
    made = state.module("qos", create=True)
    made_ref = weakref.ref(made)
    state.replace_module(made, as_stored(made, {"segments": []}))
    del made
    _ = gc.collect()
    assert made_ref() is None
    assert state.plan().changed == ()
    assert state._replaced == []  # pyright: ignore[reportPrivateUsage]


def test_a_held_model_the_dump_leaves_out_does_not_break_plan() -> None:
    state = make_state([ds_row()])
    old = state.module("deviceSettings")
    assert isinstance(old, DeviceSettingsConfigurationModule)
    _ = old.for_edge()
    vpn = old.data.segments[0].vpn  # ProfileOnly: an edge-level dump leaves it out
    assert "vpn" not in dump(old.data.segments[0])
    state.replace_module(old, as_stored(old, copy.deepcopy(EDGE_DS)))
    del old
    _ = gc.collect()

    assert isinstance(state.plan(), ConfigPlan)
    assert isinstance(state.plan(), ConfigPlan)
    with pytest.raises(DetachedObjectError, match=r"SegmentVpn\.enabled"):
        vpn.enabled = True


def test_replace_of_a_module_that_is_not_in_the_state_raises() -> None:
    state = make_state()
    twin = make_state().module("firewall")  # equal to the state's, not the same object

    with pytest.raises(ValueError, match="'firewall' is not in the target config"):
        state.replace_module(twin, as_stored(twin, copy.deepcopy(EDGE_FW)))
    assert twin.is_detached is False
    assert state.module("firewall").version == "e1"

    unset = make_state(modules=False)
    with pytest.raises(ValueError, match="'firewall' is not in the target config"):
        unset.replace_module(twin, as_stored(twin, copy.deepcopy(EDGE_FW)))


def test_an_inserted_module_is_replaced_and_no_longer_new() -> None:
    state = make_state()
    made = state.module("qos", create=True)
    assert made.is_new
    new = as_stored(made, {"segments": []})

    state.replace_module(made, new)

    assert state.module("qos") is new
    assert state.module("qos").is_new is False
    with pytest.raises(ValueError, match="read from the VCO"):
        state.drop_module(new)
    snapshot = state.snapshot_for_target()
    assert snapshot is not None
    assert snapshot.modules is not MISSING
    assert [m.name for m in snapshot.modules] == ["firewall", "QOS"]
    stored = snapshot.modules[1]
    assert stored is not new
    assert dump(stored) == dump(new)
    assert state.get_field_changes() == []


WEB: dict[str, Any] = {"name": "web", "match": {}, "action": {"allow_or_deny": "allow"}}


def test_a_deep_copy_of_a_detached_rule_can_go_into_the_state() -> None:
    raw = copy.deepcopy(EDGE_FW)
    raw["segments"][0]["outbound"] = [WEB]
    row = make_module_json(
        "firewall", raw, module_id=1, configuration_id=10, version="e1"
    )
    state = make_state([row])
    held = state.edge.firewall.segment("Global Segment").outbound[0]
    old = state.module("firewall")
    assert isinstance(old, FirewallConfigurationModule)
    state.replace_module(old, as_stored(old, copy.deepcopy(raw)))
    assert held.is_detached

    again = copy.deepcopy(held)
    again.name = "web-2"
    state.edge.firewall.segment("Global Segment").outbound.append(again)

    (module,) = state.plan().changed
    assert [str(change.path) for change in module.changes] == [
        'segments["Global Segment"].outbound[web-2]'
    ]
    assert again.is_detached is False
    assert held.name == "web"


def test_a_held_model_that_is_not_a_vco_model_is_compared() -> None:
    raw: dict[str, Any] = {
        "lan": {},
        "segments": [
            {
                "segment": desc(0, "Global Segment"),
                "vrrp": {"enabled": False, "data": []},
            }
        ],
    }
    row = make_module_json(
        "deviceSettings", raw, module_id=5, configuration_id=10, version="e1"
    )
    state = make_state([row])
    old = state.module("deviceSettings")
    assert isinstance(old, DeviceSettingsConfigurationModule)
    vrrp = old.data.segments[0].vrrp
    assert vrrp is not MISSING
    state.replace_module(old, as_stored(old, copy.deepcopy(raw)))
    del old
    _ = gc.collect()  # only the vrrp object is held now

    vrrp.root = {"enabled": True, "data": []}

    with pytest.raises(DetachedObjectError) as exc:
        _ = state.plan()
    assert exc.value.paths == [
        Path(("data", "segments", Key("Global Segment"), "vrrp", "enabled"))
    ]
    assert state.plan().changed == ()
