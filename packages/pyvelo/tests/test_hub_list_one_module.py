"""One module alone cannot add a hub to a segment's hub lists or remove one.

A hub in a list needs its role set on the hub's service, which the pipeline
does as a step of the save; a plan of the device settings module alone would
list a hub no edge can use, or lose one's roles. A reorder is no change of
members.
"""

import copy
import json
import pickle
from typing import Any

import pytest
from hub_helpers import CLUSTER, HUB_A, HUB_B, Hub, ds_with_vpn, hub_entry
from hub_state import hub_profile_state, profile_hub_rows
from pyvelo.client.exceptions import HubListError
from pyvelo.client.hub_services import hub_list_fields
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import DeviceSettingsConfigurationModule
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.state import ConfigState
from pyvelo.core import ConfigContext
from refs_helpers import segment_row
from segment_helpers import GLOBAL, GUEST, firewall_entry, profile_rows, qos_entry

LIST_PATHS = {
    "hub": ("edgeToEdgeHub", "vpnHubs"),
    "bridge": ("edgeToEdgeDetail", "vpnHubs"),
    "backhaul": ("backHaulEdges",),
}
SHOWN_LISTS = {
    "hub": "vpn.hub_edges.vpn_hubs",
    "bridge": "vpn.edge_to_edge_detail.vpn_hubs",
    "backhaul": "vpn.backhaul_edges",
}


def ds_module(
    *,
    hubs: tuple[Hub, ...] = (),
    bridges: tuple[Hub, ...] = (),
    backhaul: tuple[Hub, ...] = (),
) -> DeviceSettingsConfigurationModule:
    """The device settings module of a profile, with hubs in its Global Segment.

    The Global Segment has these hubs, bridges and backhaul hubs.
    """
    entry = ds_with_vpn(GLOBAL, hubs=hubs, bridges=bridges, backhaul=backhaul)
    row = profile_rows([entry], [], [])[0]
    return DeviceSettingsConfigurationModule.model_validate(
        row, context={"config_level": ConfigContext.PROFILE}
    )


def raw_list(raw: dict[str, Any], role: str) -> list[dict[str, Any]]:
    """The hub list of *role* in the Global Segment of the raw data *raw*."""
    node: Any = raw["segments"][0]["vpn"]
    for key in LIST_PATHS[role]:
        node = node[key]
    return node


def edited_raw(module: DeviceSettingsConfigurationModule) -> dict[str, Any]:
    raw = copy.deepcopy(module.raw_data)
    assert isinstance(raw, dict)
    return raw


@pytest.mark.parametrize("role", ["hub", "bridge", "backhaul"])
@pytest.mark.parametrize("added", [HUB_A, CLUSTER], ids=lambda h: h.kind)
def test_a_hub_added_to_a_list_is_refused(role: str, added: Hub) -> None:
    module = ds_module(hubs=(HUB_B,), bridges=(HUB_B,), backhaul=(HUB_B,))
    raw = edited_raw(module)
    raw_list(raw, role).append(hub_entry(added))
    with pytest.raises(HubListError) as caught:
        plan_module(module, raw)
    error = caught.value
    assert error.segment == "Global Segment"
    assert error.lists == (SHOWN_LISTS[role],)
    assert error.hubs == (added.name,)
    message = str(error)
    assert SHOWN_LISTS[role] in message
    assert "pipeline.apply" in message
    assert "pipeline.save" in message
    assert '"_op": "add"' in message


@pytest.mark.parametrize("field", hub_list_fields(), ids=lambda f: f.role)
def test_the_refusal_covers_each_field_marked_hub_role(field: Any) -> None:
    module = ds_module()
    raw = edited_raw(module)
    node: Any = raw["segments"][0]
    for key in field.path:
        node = node[key]
    node.append(hub_entry(HUB_A))
    with pytest.raises(HubListError) as caught:
        plan_module(module, raw)
    assert caught.value.lists == (".".join(field.python_path),)


def test_a_hub_added_to_a_second_segment_is_refused_and_names_it() -> None:
    row = profile_rows(
        [ds_with_vpn(GLOBAL, hubs=(HUB_B,)), ds_with_vpn(GUEST, hubs=(HUB_B,))],
        [],
        [],
    )[0]
    module = DeviceSettingsConfigurationModule.model_validate(
        row, context={"config_level": ConfigContext.PROFILE}
    )
    raw = edited_raw(module)
    raw["segments"][1]["vpn"]["backHaulEdges"].append(hub_entry(HUB_A))
    with pytest.raises(HubListError) as caught:
        plan_module(module, raw)
    assert caught.value.segment == "Guest"
    assert caught.value.lists == (SHOWN_LISTS["backhaul"],)
    assert "Guest" in str(caught.value)


@pytest.mark.parametrize("role", ["hub", "bridge", "backhaul"])
def test_a_hub_removed_from_a_list_is_refused(role: str) -> None:
    module = ds_module(hubs=(HUB_A,), bridges=(HUB_A,), backhaul=(HUB_A,))
    raw = edited_raw(module)
    raw_list(raw, role).clear()
    with pytest.raises(HubListError) as caught:
        plan_module(module, raw)
    assert caught.value.lists == (SHOWN_LISTS[role],)
    assert caught.value.hubs == (HUB_A.name,)
    assert "pipeline.save" in str(caught.value)


def test_a_model_edit_that_adds_a_hub_is_refused() -> None:
    module = ds_module()
    raw = edited_raw(module)
    raw_list(raw, "backhaul").append(hub_entry(HUB_A))
    edited = type(module.data).model_validate(
        raw, context={"config_level": ConfigContext.PROFILE}
    )
    with pytest.raises(HubListError) as caught:
        plan_module(module, edited)
    assert caught.value.lists == (SHOWN_LISTS["backhaul"],)


def test_the_error_names_every_list_and_hub_it_found() -> None:
    module = ds_module(hubs=(HUB_A,))
    raw = edited_raw(module)
    raw_list(raw, "hub").clear()
    raw_list(raw, "backhaul").append(hub_entry(HUB_B))
    with pytest.raises(HubListError) as caught:
        plan_module(module, raw)
    error = caught.value
    assert error.lists == (SHOWN_LISTS["hub"], SHOWN_LISTS["backhaul"])
    assert error.hubs == (HUB_A.name, HUB_B.name)


def test_the_message_gives_the_json_form_before_any_python_helper() -> None:
    module = ds_module()
    raw = edited_raw(module)
    raw_list(raw, "backhaul").append(hub_entry(HUB_A))
    with pytest.raises(HubListError) as caught:
        plan_module(module, raw)
    message = str(caught.value)
    patch = json.dumps(
        {
            "segments": {
                "Global Segment": {
                    "vpn": {"backhaul_edges": {"+": {"_op": "add", "name": "Hub-A"}}}
                }
            }
        }
    )
    assert patch in message
    assert message.index(patch) < message.index("pipeline.apply")


def guest_removal_rows() -> list[dict[str, Any]]:
    """The device settings entries of a profile whose Guest backhauls to Hub-A."""
    return [
        ds_with_vpn(GLOBAL, hubs=(HUB_A,)),
        ds_with_vpn(GUEST, hubs=(HUB_A,), backhaul=(HUB_A,)),
    ]


def refused_removal() -> HubListError:
    """The refusal of a plan of one module that takes Hub-A out of Guest's backhaul."""
    row = profile_rows(guest_removal_rows(), [], [])[0]
    module = DeviceSettingsConfigurationModule.model_validate(
        row, context={"config_level": ConfigContext.PROFILE}
    )
    raw = edited_raw(module)
    raw["segments"][1]["vpn"]["backHaulEdges"].clear()
    with pytest.raises(HubListError) as caught:
        plan_module(module, raw)
    return caught.value


def shown_patch(text: str) -> dict[str, Any]:
    """The first patch a message shows, as a text-only caller would send it."""
    patch, _ = json.JSONDecoder().raw_decode(text[text.index('{"segments"') :])
    return patch


def test_the_refusal_names_the_list_and_the_module_by_name() -> None:
    error = refused_removal()
    assert error.lists == ("vpn.backhaul_edges",)
    text = str(error)
    assert text.startswith(
        "device_settings: the edit removes 'Hub-A' from vpn.backhaul_edges of 'Guest'."
    )
    removal = {"backhaul_edges": {"Hub-A": {"_op": "delete"}}}
    assert json.dumps({"segments": {"Guest": {"vpn": removal}}}) in text
    assert 'pipeline.apply(state, module="device_settings", patch=...)' in text


async def guest_state() -> ConfigState:
    """A profile state whose device settings are those ``refused_removal`` plans."""
    return await hub_profile_state(
        guest_removal_rows(),
        [qos_entry(GLOBAL), qos_entry(GUEST)],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
        ds_refs=profile_hub_rows(
            segment_row(GLOBAL[0], GLOBAL[3], GLOBAL[2]),
            segment_row(GUEST[0], GUEST[3], GUEST[2]),
        ),
    )


async def test_the_removal_the_refusal_shows_is_accepted_by_the_module_it_names() -> (
    None
):
    text = str(refused_removal())
    state = await guest_state()

    _ = await ConfigPipeline(client=None).apply(
        state, module="device_settings", patch=shown_patch(text)
    )

    guest = state.profile.device_settings.segments[1]
    assert guest.segment.name == "Guest"
    assert guest.vpn.backhaul_edges == []


async def test_the_addition_the_refusal_shows_is_accepted_by_the_module_it_names() -> (
    None
):
    row = profile_rows(guest_removal_rows(), [], [])[0]
    module = DeviceSettingsConfigurationModule.model_validate(
        row, context={"config_level": ConfigContext.PROFILE}
    )
    raw = edited_raw(module)
    raw["segments"][1]["vpn"]["edgeToEdgeDetail"]["vpnHubs"].append(hub_entry(HUB_A))
    with pytest.raises(HubListError) as caught:
        plan_module(module, raw)
    text = str(caught.value)
    assert caught.value.lists == ("vpn.edge_to_edge_detail.vpn_hubs",)
    assert 'module="device_settings"' in text
    state = await guest_state()

    _ = await ConfigPipeline(client=None).apply(
        state, module="device_settings", patch=shown_patch(text)
    )

    bridges = state.profile.device_settings.segments[1].vpn.edge_to_edge_detail
    assert [hub.name for hub in bridges.vpn_hubs] == ["Hub-A"]


def test_each_hub_list_has_its_python_path_beside_its_wire_path() -> None:
    assert {
        field.role: (field.path, field.python_path) for field in hub_list_fields()
    } == {
        "hub": (("vpn", "edgeToEdgeHub", "vpnHubs"), ("vpn", "hub_edges", "vpn_hubs")),
        "bridge": (
            ("vpn", "edgeToEdgeDetail", "vpnHubs"),
            ("vpn", "edge_to_edge_detail", "vpn_hubs"),
        ),
        "backhaul": (("vpn", "backHaulEdges"), ("vpn", "backhaul_edges")),
    }


def test_via_pipeline_does_not_refuse() -> None:
    module = ds_module()
    raw = edited_raw(module)
    raw_list(raw, "hub").append(hub_entry(HUB_A))
    plan = plan_module(module, raw, via_pipeline=True)
    assert plan.operation == "update"


def test_a_reorder_is_no_change_of_members() -> None:
    module = ds_module(hubs=(HUB_A, HUB_B), backhaul=(HUB_A, HUB_B))
    raw = edited_raw(module)
    raw_list(raw, "hub").reverse()
    raw_list(raw, "backhaul").reverse()
    plan = plan_module(module, raw)
    assert plan.operation == "update"


def test_a_plan_with_no_list_change_is_not_refused() -> None:
    module = ds_module(hubs=(HUB_A,))
    raw = edited_raw(module)
    raw["segments"][0]["vpn"]["enabled"] = False
    assert plan_module(module, raw).operation == "update"


def test_an_edge_level_module_is_not_refused() -> None:
    rows = profile_rows([ds_with_vpn(GLOBAL, hubs=(HUB_A,))], [], [])
    edge_module = DeviceSettingsConfigurationModule.model_validate(
        rows[0], context={"config_level": ConfigContext.EDGE}
    )
    raw = edited_raw(edge_module)
    raw_list(raw, "hub").append(hub_entry(HUB_B))
    plan = plan_module(edge_module, raw, level=ConfigContext.EDGE)
    assert plan.operation == "update"


def test_a_hub_list_error_keeps_its_attributes_through_a_pickle() -> None:
    error = HubListError(
        "no",
        segment="Global Segment",
        lists=("vpn.backHaulEdges",),
        hubs=("Hub-A",),
        edges=((1, "Edge-1"),),
    )
    copied = pickle.loads(pickle.dumps(error))
    assert type(copied) is HubListError
    assert (copied.segment, copied.lists, copied.hubs, copied.edges) == (
        "Global Segment",
        ("vpn.backHaulEdges",),
        ("Hub-A",),
        ((1, "Edge-1"),),
    )
