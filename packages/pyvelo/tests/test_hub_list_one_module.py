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
from pyvelo.client.exceptions import HubListError
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import DeviceSettingsConfigurationModule
from pyvelo.core import ConfigContext
from segment_helpers import GLOBAL, profile_rows

LIST_PATHS = {
    "hub": ("edgeToEdgeHub", "vpnHubs"),
    "bridge": ("edgeToEdgeDetail", "vpnHubs"),
    "backhaul": ("backHaulEdges",),
}
WIRE_LISTS = {
    "hub": "vpn.edgeToEdgeHub.vpnHubs",
    "bridge": "vpn.edgeToEdgeDetail.vpnHubs",
    "backhaul": "vpn.backHaulEdges",
}


def ds_module(
    *,
    hubs: tuple[Hub, ...] = (),
    bridges: tuple[Hub, ...] = (),
    backhaul: tuple[Hub, ...] = (),
) -> DeviceSettingsConfigurationModule:
    """The device settings module of a profile whose Global Segment has these hubs, bridges and backhaul hubs."""
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
def test_a_hub_added_to_a_list_is_refused(role: str, added: Hub):
    module = ds_module(hubs=(HUB_B,), bridges=(HUB_B,), backhaul=(HUB_B,))
    raw = edited_raw(module)
    raw_list(raw, role).append(hub_entry(added))
    with pytest.raises(HubListError) as caught:
        plan_module(module, raw)
    error = caught.value
    assert error.segment == "Global Segment"
    assert error.lists == (WIRE_LISTS[role],)
    assert error.hubs == (added.name,)
    message = str(error)
    assert WIRE_LISTS[role] in message
    assert "pipeline.apply" in message and "pipeline.save" in message
    assert '"_op": "add"' in message


@pytest.mark.parametrize("role", ["hub", "bridge", "backhaul"])
def test_a_hub_removed_from_a_list_is_refused(role: str):
    module = ds_module(hubs=(HUB_A,), bridges=(HUB_A,), backhaul=(HUB_A,))
    raw = edited_raw(module)
    raw_list(raw, role).clear()
    with pytest.raises(HubListError) as caught:
        plan_module(module, raw)
    assert caught.value.lists == (WIRE_LISTS[role],)
    assert caught.value.hubs == (HUB_A.name,)
    assert "pipeline.save" in str(caught.value)


def test_a_model_edit_that_adds_a_hub_is_refused():
    module = ds_module()
    raw = edited_raw(module)
    raw_list(raw, "backhaul").append(hub_entry(HUB_A))
    edited = type(module.data).model_validate(
        raw, context={"config_level": ConfigContext.PROFILE}
    )
    with pytest.raises(HubListError) as caught:
        plan_module(module, edited)
    assert caught.value.lists == (WIRE_LISTS["backhaul"],)


def test_the_error_names_every_list_and_hub_it_found():
    module = ds_module(hubs=(HUB_A,))
    raw = edited_raw(module)
    raw_list(raw, "hub").clear()
    raw_list(raw, "backhaul").append(hub_entry(HUB_B))
    with pytest.raises(HubListError) as caught:
        plan_module(module, raw)
    error = caught.value
    assert error.lists == (WIRE_LISTS["hub"], WIRE_LISTS["backhaul"])
    assert error.hubs == (HUB_A.name, HUB_B.name)


def test_the_message_gives_the_json_form_before_any_python_helper():
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
                    "vpn": {"backHaulEdges": {"+": {"_op": "add", "name": "Hub-A"}}}
                }
            }
        }
    )
    assert patch in message
    assert message.index(patch) < message.index("pipeline.apply")


def test_via_pipeline_does_not_refuse():
    module = ds_module()
    raw = edited_raw(module)
    raw_list(raw, "hub").append(hub_entry(HUB_A))
    plan = plan_module(module, raw, via_pipeline=True)
    assert plan.operation == "update"


def test_a_reorder_is_no_change_of_members():
    module = ds_module(hubs=(HUB_A, HUB_B), backhaul=(HUB_A, HUB_B))
    raw = edited_raw(module)
    raw_list(raw, "hub").reverse()
    raw_list(raw, "backhaul").reverse()
    plan = plan_module(module, raw)
    assert plan.operation == "update"


def test_a_plan_with_no_list_change_is_not_refused():
    module = ds_module(hubs=(HUB_A,))
    raw = edited_raw(module)
    raw["segments"][0]["vpn"]["enabled"] = False
    assert plan_module(module, raw).operation == "update"


def test_an_edge_level_module_is_not_refused():
    rows = profile_rows([ds_with_vpn(GLOBAL, hubs=(HUB_A,))], [], [])
    edge_module = DeviceSettingsConfigurationModule.model_validate(
        rows[0], context={"config_level": ConfigContext.EDGE}
    )
    raw = edited_raw(edge_module)
    raw_list(raw, "hub").append(hub_entry(HUB_B))
    plan = plan_module(edge_module, raw, level=ConfigContext.EDGE)
    assert plan.operation in ("update", "none")


def test_a_hub_list_error_keeps_its_attributes_through_a_pickle():
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
