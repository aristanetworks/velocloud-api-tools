"""Builders for the tests of hubs: the Edges and clusters that can be hubs, and their entries in the modules.

``tests/support`` is on pytest's ``pythonpath`` and basedpyright's
``extraPaths``, so the tests of both packages import this as ``hub_helpers``.
"""

import copy
from typing import Any, Final, Literal, NamedTuple

import segment_helpers
from pyvelo.client.models.configuration.profile_segments import segment_template


class Hub(NamedTuple):
    """An Edge or a cluster a test uses as a hub: its kind, its Edge or cluster ID, its logical ID and its name."""

    kind: Literal["edge", "cluster"]
    id: int
    logical_id: str
    name: str

    @property
    def type_(self) -> str:
        """The ``type`` text of a hub list entry for this hub."""
        return "edgeHub" if self.kind == "edge" else "edgeHubCluster"


HUB_A: Final = Hub("edge", 31, "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1", "Hub-A")
HUB_B: Final = Hub("edge", 32, "b2b2b2b2-b2b2-4b2b-8b2b-b2b2b2b2b2b2", "Hub-B")
CLUSTER: Final = Hub("cluster", 41, "c3c3c3c3-c3c3-4c3c-8c3c-c3c3c3c3c3c3", "Cluster-1")
MEMBER: Final = Hub("edge", 33, "d4d4d4d4-d4d4-4d4d-8d4d-d4d4d4d4d4d4", "Member-1")
"""A member of ``CLUSTER``."""
NOT_A_HUB: Final = Hub("edge", 34, "e5e5e5e5-e5e5-4e5e-8e5e-e5e5e5e5e5e5", "Branch-1")


def hub_entry(hub: Hub) -> dict[str, Any]:
    """The entry of a device settings hub list for *hub*: ``name``, ``type`` and ``logicalId``."""
    return {"name": hub.name, "type": hub.type_, "logicalId": hub.logical_id}


def backhaul_entry(hub: Hub, service_logical_id: str) -> dict[str, Any]:
    """The entry of a QoS ``routeCfg.backhaulEdges`` list for *hub*, whose service in the profile and segment has *service_logical_id*."""
    return {
        "type": hub.type_,
        "logicalId": service_logical_id,
        "edgeLogicalId": hub.logical_id,
        "edgeId": hub.id if hub.kind == "edge" else None,
        "name": hub.name,
    }


def backhaul_rule(name: str, *entries: dict[str, Any]) -> dict[str, Any]:
    """A QoS rule named *name* whose Edge to Edge route has *entries* as its ``backhaulEdges``."""
    rule = segment_template("QOS")["rules"][0]
    rule["name"] = name
    rule["ruleLogicalId"] = "f6f6f6f6-f6f6-4f6f-8f6f-f6f6f6f6f6f6"
    rule["action"]["edge2EdgeRouteAction"]["routeCfg"] = {
        "backhaulEdges": copy.deepcopy(list(entries)),
    }
    return rule


def ds_with_vpn(
    segment: segment_helpers.Segment,
    *,
    enabled: bool = True,
    hubs: tuple[Hub, ...] = (),
    bridges: tuple[Hub, ...] = (),
    backhaul: tuple[Hub, ...] = (),
    hub_list_enabled: bool | None = None,
    edge_to_edge: bool = False,
    cloud_gateway: bool = True,
) -> dict[str, Any]:
    """A device settings segment entry for *segment* whose ``vpn`` has the given hubs, bridges and backhaul hubs.

    *hub_list_enabled* is the ``enabled`` flag of the hub list and is true
    when there are *hubs*, unless given.
    """
    entry = segment_helpers.ds_entry(segment)
    entry["vpn"] = {
        "enabled": enabled,
        "edgeToEdgeHub": {
            "enabled": bool(hubs) if hub_list_enabled is None else hub_list_enabled,
            "ref": "deviceSettings:vpn:edgeHub",
            "vpnHubs": [hub_entry(h) for h in hubs],
        },
        "edgeToEdge": edge_to_edge,
        "edgeToEdgeDetail": {
            "useCloudGateway": cloud_gateway,
            "vpnHubs": [hub_entry(h) for h in bridges],
        },
        "backHaulEdges": [hub_entry(h) for h in backhaul],
        "conditionalBackhaul": False,
    }
    return entry
