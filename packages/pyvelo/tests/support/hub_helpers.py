"""Builders for the tests of hubs: the Edges and clusters that can be hubs, and their entries in the modules.

``tests/support`` is on pytest's ``pythonpath`` and basedpyright's
``extraPaths``, so the tests of both packages import this as ``hub_helpers``.
"""

import copy
from typing import Any, Final, Literal, NamedTuple

import segment_helpers
from pyvelo.client.models.configuration.profile_segments import segment_template
from pyvelo.testing.module_store import ModuleStore
from segment_helpers import CONFIG, DS_ID, QOS_ID


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


def hub_row(
    hub: Hub,
    *,
    service_id: int,
    service_logical_id: str,
    segment: segment_helpers.Segment,
    configuration_id: int = CONFIG,
    module_id: int = DS_ID,
    roles: tuple[Literal["hub", "bridge", "backhaul"], ...] = ("hub",),
) -> dict[str, Any]:
    """A device settings hub row for *hub* in *segment*, as the VCO stores it.

    *service_id* and *service_logical_id* are the hub's service in the profile
    and segment (the cluster itself for a cluster). Any of the *roles* makes
    the row's ``edgeHub`` role true, as an enable call does. For an Edge the
    row's ``data`` is the service's: the Edge's ``logicalId`` and the roles; for
    a cluster ``logicalId`` is the cluster's and ``data`` is its role data for
    the profile, from the segment ID to the roles.
    """
    held = {
        "edgeHub": bool(roles),
        "edgeToEdgeBridge": "bridge" in roles,
        "backHaulEdge": "backhaul" in roles,
    }
    cluster = hub.kind == "cluster"
    data: dict[str, Any] = (
        {str(segment[0]): {"roles": held}}
        if cluster
        else {"logicalId": hub.logical_id, "roles": held}
    )
    return {
        "id": 8000 + service_id,
        "enterpriseObjectId": service_id,
        "configurationId": configuration_id,
        "moduleId": module_id,
        "segmentObjectId": segment[3],
        "ref": "deviceSettings:vpn:edgeHubCluster"
        if cluster
        else "deviceSettings:vpn:edgeHub",
        "logicalId": hub.logical_id if cluster else service_logical_id,
        "name": hub.name,
        "type": hub.type_,
        "object": "NETWORK_SERVICE",
        "data": data,
        "segmentLogicalId": segment[2],
    }


def backhaul_row(
    hub: Hub,
    *,
    service_id: int,
    service_logical_id: str,
    segment: segment_helpers.Segment,
    configuration_id: int = CONFIG,
    module_id: int = QOS_ID,
) -> dict[str, Any]:
    """A ``deviceSettings:backHaulEdge`` row of a QoS module, as the VCO stores it.

    It points at the hub's service (*service_id*, *service_logical_id*) in
    *segment*.
    """
    return {
        "id": 7000 + service_id,
        "configurationId": configuration_id,
        "moduleId": module_id,
        "enterpriseObjectId": service_id,
        "ref": "deviceSettings:backHaulEdge",
        "segmentObjectId": segment[3],
        "logicalId": service_logical_id,
        "name": hub.name,
        "type": hub.type_,
        "object": "NETWORK_SERVICE",
        "data": {"logicalId": hub.logical_id},
        "segmentLogicalId": segment[2],
    }


def hub_store(*hubs: Hub, **knobs: Any) -> ModuleStore:
    """A ``ModuleStore`` (with *knobs*) that holds the Edges and clusters of *hubs*.

    ``MEMBER`` is held as a member of ``CLUSTER``.
    """
    store = ModuleStore(**knobs)
    for hub in hubs:
        if hub.kind == "cluster":
            store.add_cluster(hub.id, logical_id=hub.logical_id, name=hub.name)
        else:
            store.add_edge(
                hub.id,
                logical_id=hub.logical_id,
                name=hub.name,
                cluster=CLUSTER.id if hub == MEMBER else None,
            )
    return store
