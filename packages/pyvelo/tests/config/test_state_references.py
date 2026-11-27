"""``ConfigState.plan`` refuses a value that names what the configuration lacks.

That is a segment or a backhaul hub the configuration lacks.

A profile state is built by ``profile_state``; an edge state is an edge whose
device settings (or QoS) hold the edit, over that profile. A reference that
already named nothing as read is a ``reference_kept`` warning, not an error.
"""

import copy
import uuid
from typing import Any

import pytest
from hub_helpers import (
    HUB_A,
    HUB_B,
    Hub,
    backhaul_entry,
    backhaul_row,
    backhaul_rule,
    ds_with_vpn,
    hub_row,
)
from hub_state import (
    hub_profile_state,
    profile_hub_rows,
)
from profile_state import (
    module_store_pipeline,
    profile_state,
)
from pydantic import MISSING
from pyvelo.client.exceptions import DanglingReferenceError
from pyvelo.client.hub_services import BACKHAUL_REF
from pyvelo.client.module_write import ReadData
from pyvelo.client.schemas.configuration import (
    DeviceSettingsConfigurationModule,
    EdgeConfiguration,
)
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.state import ConfigState
from pyvelo.core import add, backhaul_hub_mending, delete
from pyvelo.testing import MockVcoClient, make_module_json
from refs_helpers import QOS_ID, segment_row
from segment_helpers import GLOBAL, GUEST, ds_entry, firewall_entry, qos_entry

DS = "deviceSettings"
QOS = "QOS"
EDGE_CONFIG = 50
EDGE_DS_ID = 51
EDGE_QOS_ID = 52
SERVICE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
UPDATE = "configuration/updateConfigurationModule"
USERS = {"vlanId": 100, "name": "users", "segmentId": GLOBAL[0], "override": True}


def network(segment_id: int, vlan_id: int = 200) -> dict[str, Any]:
    """A LAN network on segment *segment_id*."""
    return {
        **USERS,
        "vlanId": vlan_id,
        "name": f"vlan{vlan_id}",
        "segmentId": segment_id,
    }


async def profile_of(
    *,
    ds: list[dict[str, Any]] | None = None,
    qos: list[dict[str, Any]] | None = None,
    networks: list[dict[str, Any]] | None = None,
) -> ConfigState:
    """A profile with Global and Guest in its three modules.

    *ds* or *qos* may say otherwise.
    """
    return await profile_state(
        ds if ds is not None else [ds_entry(GLOBAL), ds_entry(GUEST)],
        qos if qos is not None else [qos_entry(GLOBAL), qos_entry(GUEST)],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
        networks=networks or [],
    )


async def edge_over(
    profile: ConfigState,
    *,
    networks: list[dict[str, Any]] | None = None,
    with_qos: bool = False,
) -> ConfigState:
    """An edge state over *profile*'s profile.

    Its device settings hold only the Global Segment and *networks*.

    With *with_qos* the edge also has a QoS module of its own, with the Global
    Segment, no rule and no `cosMapping` (it inherits the profile's).
    """
    modules = [
        make_module_json(
            DS,
            {
                "lan": {"networks": copy.deepcopy(networks or []), "interfaces": []},
                "segments": [ds_entry(GLOBAL)],
            },
            module_id=EDGE_DS_ID,
            configuration_id=EDGE_CONFIG,
            refs={},
        )
    ]
    if with_qos:
        inheriting = qos_entry(GLOBAL)
        del inheriting["cosMapping"]  # the template's is stored with its flag off
        modules.append(
            make_module_json(
                QOS,
                {"segments": [inheriting]},
                module_id=EDGE_QOS_ID,
                configuration_id=EDGE_CONFIG,
            )
        )
    edge = EdgeConfiguration.model_validate(
        {"name": "Edge Specific Profile", "id": EDGE_CONFIG, "modules": modules}
    )
    state = ConfigState(
        edge_config=edge,
        profile_config=profile.profile_config,
        target_type="edge",
        resolver_context=ResolverContext(),
        resolvers=profile.resolvers,
    )
    state.take_snapshot()
    return state


async def patch(state: ConfigState, module: str, patch: dict[str, Any]) -> None:
    _ = await ConfigPipeline(client=None).apply(state, module=module, patch=patch)


def add_network(segment_id: int, vlan_id: int = 200) -> dict[str, Any]:
    return {"lan": {"networks": {"+": add(network(segment_id, vlan_id))}}}


async def test_an_edge_vlan_naming_a_profile_only_segment_is_accepted() -> None:
    profile = await profile_of()
    state = await edge_over(profile, networks=[network(GUEST[0])])

    plan = state.plan()

    assert plan.warnings == ()


async def test_a_new_vlan_naming_no_segment_raises() -> None:
    state = await profile_of()
    await patch(state, DS, add_network(9))

    with pytest.raises(DanglingReferenceError) as error:
        _ = state.plan()

    (reference,) = error.value.references
    assert (reference.module, reference.value, reference.set_name) == (DS, 9, "segment")
    assert "lan.networks" in str(error.value)


async def test_a_new_vlan_on_an_edge_naming_no_segment_raises() -> None:
    state = await edge_over(await profile_of())
    await patch(state, DS, add_network(9))

    with pytest.raises(DanglingReferenceError) as error:
        _ = state.plan()

    assert [r.value for r in error.value.references] == [9]


async def test_a_vlan_left_as_read_on_a_missing_segment_warns() -> None:
    state = await profile_of(networks=[network(9, 100)])
    state.profile.device_settings.lan.networks[0].name = "renamed"

    plan = state.plan()

    (warning,) = plan.warnings
    assert warning.code == "reference_kept"
    assert warning.path.startswith("deviceSettings: lan.networks")
    assert "9 is not a segment" in warning.message
    assert "it was so as read" in warning.message
    assert f"warning: {warning.message}" in plan.render()
    assert [m.name for m in plan.changed] == [DS]  # the save goes ahead


async def test_a_vlan_moved_onto_a_missing_segment_raises() -> None:
    state = await profile_of(networks=[network(9, 100)])
    state.profile.device_settings.lan.networks[0].segment_id = 8

    with pytest.raises(DanglingReferenceError) as error:
        _ = state.plan()

    assert [r.value for r in error.value.references] == [8]


SERVICE_ID = 601


def hub_refs(*hubs: Hub, segment: tuple[int, str, str, int] = GLOBAL) -> dict[str, Any]:
    """The device settings refs of a profile with the Global Segment and Guest.

    They hold a hub row in *segment* (service ``SERVICE``) for each of *hubs*.
    """
    return profile_hub_rows(
        segment_row(GLOBAL[0], GLOBAL[3], GLOBAL[2]),
        segment_row(GUEST[0], GUEST[3], GUEST[2]),
        *(
            hub_row(
                hub,
                service_id=SERVICE_ID + n,
                service_logical_id=SERVICE,
                segment=segment,
                roles=("hub", "backhaul"),
            )
            for n, hub in enumerate(hubs)
        ),
    )


def hub_profile_ds() -> list[dict[str, Any]]:
    """Global, and Guest (segment 2) with ``HUB_A`` as hub and backhaul hub."""
    return [
        ds_entry(GLOBAL),
        ds_with_vpn(GUEST, hubs=(HUB_A,), backhaul=(HUB_A,)),
    ]


def guest_rule_qos(hub: Any) -> list[dict[str, Any]]:
    """QoS entries: Global, and Guest with rule ``Web`` that backhauls to *hub*."""
    rule = backhaul_rule("Web", backhaul_entry(hub, SERVICE))
    return [qos_entry(GLOBAL), qos_entry(GUEST, rules=[rule])]


async def hub_profile_of(
    ds: list[dict[str, Any]],
    qos: list[dict[str, Any]],
    refs: dict[str, Any],
    **more: Any,
) -> ConfigState:
    """A profile of *ds* and *qos* with the device settings refs *refs*.

    The Global Segment and Guest are in the firewall too.
    """
    return await hub_profile_state(
        ds,
        qos,
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
        ds_refs=refs,
        **more,
    )


async def test_removing_a_hub_a_rule_uses_raises_and_names_the_rule() -> None:
    state = await hub_profile_of(
        hub_profile_ds(),
        guest_rule_qos(HUB_A),
        hub_refs(HUB_A, segment=GUEST),
        qos_refs={
            BACKHAUL_REF: backhaul_row(
                HUB_A,
                service_id=SERVICE_ID,
                service_logical_id=SERVICE,
                segment=GUEST,
                module_id=QOS_ID,
            )
        },
    )
    assert state.plan().warnings == ()

    await patch(
        state,
        DS,
        {
            "segments": {
                "Guest": {
                    "vpn": {
                        "backHaulEdges": {HUB_A.name: delete()},
                        "edgeToEdgeHub": {
                            "enabled": False,
                            "vpnHubs": {HUB_A.name: delete()},
                        },
                    }
                }
            }
        },
    )

    with pytest.raises(DanglingReferenceError) as error:
        _ = state.plan()

    (reference,) = error.value.references
    assert (reference.module, reference.set_name) == (QOS, "backhaul_hub")
    assert reference.segment_id == GUEST[0]
    assert "rules[Web]" in str(error.value)
    assert not error.value.on_edge
    assert "This is an Edge" not in str(error.value)


async def test_an_edge_rule_must_name_a_backhaul_hub_of_the_profile() -> None:
    # Hub-B has a service in the Global Segment, and the segment does not list it for
    # backhaul.
    profile = await hub_profile_of(
        [
            ds_with_vpn(GLOBAL, hubs=(HUB_A, HUB_B), backhaul=(HUB_A,)),
            ds_entry(GUEST),
        ],
        [qos_entry(GLOBAL), qos_entry(GUEST)],
        hub_refs(HUB_B),
    )
    state = await edge_over(profile, with_qos=True)
    await patch(
        state,
        QOS,
        {
            "segments": {
                "Global Segment": {
                    "rules": {
                        "+": add(backhaul_rule("Web", backhaul_entry(HUB_B, SERVICE)))
                    }
                }
            }
        },
    )

    with pytest.raises(DanglingReferenceError) as error:
        _ = state.plan()

    (reference,) = error.value.references
    assert (reference.module, reference.value) == (QOS, uuid.UUID(HUB_B.logical_id))
    # The hub lists are the profile's: the advice names its segment and hub.
    assert error.value.on_edge
    assert "This is an Edge" in str(error.value)
    assert f'"{GLOBAL[1]}": {{"vpn"' in str(error.value)
    assert f'"name": "{HUB_B.name}"' in str(error.value)
    remove, member = error.value.mending
    assert (remove.kind, remove.target_type, remove.module) == (
        "remove_reference",
        "edge",
        QOS,
    )
    assert member == backhaul_hub_mending(GLOBAL[1], HUB_B.name, in_hub_list=True)
    await patch(state, remove.module, remove.patch)
    assert state.plan().warnings == ()


async def test_an_edge_rule_naming_a_backhaul_hub_of_the_profile_is_accepted() -> None:
    profile = await hub_profile_of(
        [ds_with_vpn(GLOBAL, hubs=(HUB_A,), backhaul=(HUB_A,)), ds_entry(GUEST)],
        [qos_entry(GLOBAL), qos_entry(GUEST)],
        hub_refs(HUB_A),
    )
    state = await edge_over(profile, with_qos=True)
    await patch(
        state,
        QOS,
        {
            "segments": {
                "Global Segment": {
                    "rules": {
                        "+": add(backhaul_rule("Web", backhaul_entry(HUB_A, SERVICE)))
                    }
                }
            }
        },
    )

    assert state.plan().warnings == ()


async def test_validate_false_does_not_skip_it() -> None:
    state = await profile_of()
    await patch(state, DS, add_network(9))
    pipeline, store = module_store_pipeline(state)

    with pytest.raises(DanglingReferenceError):
        _ = await pipeline.save(state, validate=False)

    assert [m for m, _ in store.requests if m == UPDATE] == []


async def test_update_module_does_not_run_it() -> None:
    state = await profile_of(networks=[network(GLOBAL[0], 100)])
    client = MockVcoClient.for_state(state)
    modules = state.profile_config.modules
    assert modules is not MISSING
    module = next(
        m for m in modules if isinstance(m, DeviceSettingsConfigurationModule)
    )
    data = module.mutable_data()
    data.lan.networks.append(type(data.lan.networks[0]).model_validate(network(9)))

    _ = await client.update_module(module, data)

    assert [m for m, _ in client.store.requests if m == UPDATE] == [UPDATE]


# -- what a plan parses -------------------------------------------------------


def count_parses(monkeypatch: pytest.MonkeyPatch) -> list[type]:
    """Record the class of every module as read that ``ReadData.model`` parses.

    Recording starts now.
    """
    parsed: list[type] = []
    original = ReadData.model

    def counting(self: ReadData, model_type: type) -> Any:
        parsed.append(model_type)
        return original(self, model_type)

    monkeypatch.setattr(ReadData, "model", counting)
    return parsed


async def test_a_profile_plan_parses_each_module_as_read_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = await profile_of()
    await patch(state, DS, {"lan": {"networks": {"+": add(network(GUEST[0]))}}})
    parsed = count_parses(monkeypatch)

    plan = state.plan()

    assert plan.changed
    assert sorted(t.__name__ for t in parsed) == [
        "DeviceSettingsData",
        "FirewallData[Profile]",
        "QosData[Profile]",
    ]


async def test_an_edge_plan_parses_only_the_profile_modules_that_provide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The profile's QoS and firewall provide nothing a reference names: their
    # rules are not parsed again for the sets.
    state = await edge_over(await profile_of(), with_qos=True)
    await patch(state, DS, add_network(GUEST[0]))
    parsed = count_parses(monkeypatch)

    plan = state.plan()

    assert plan.warnings == ()
    assert sorted(t.__name__ for t in parsed) == [
        "DeviceSettingsData",  # the edge's, once
        "DeviceSettingsData",  # the profile's, for the sets
        "QosData[Edge]",  # the edge's, once
    ]
