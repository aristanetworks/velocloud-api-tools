"""``ModuleStore``: Edges, hub clusters, the twelve hub role calls and the hub list check.

Each test runs on a store with the profile of ``segment_helpers`` (configuration
``CONFIG``, one device settings segment, ``GLOBAL``), the Edges of ``hub_helpers``
and an Edge configuration (``EDGE_CONFIG``) of ``NOT_A_HUB`` with a QoS module.
"""

import copy
from typing import Any, cast

import hub_helpers
import pytest
import segment_helpers
from hub_helpers import CLUSTER, HUB_A, HUB_B, MEMBER, NOT_A_HUB, Hub
from pydantic import TypeAdapter
from pyvelo.client.schemas.enterprise import (
    AnyEnterpriseService,
    EdgeHubClusterMemberService,
    EdgeHubClusterService,
    EdgeHubService,
    EnterpriseServiceBase,
    GetEnterpriseEdgeList,
)
from pyvelo.testing import make_module_json
from pyvelo.testing.module_store import ModuleStore, RpcError
from refs_helpers import CONFIG, DS_ID
from segment_helpers import GLOBAL

from ._type_support import MockVcoClientFactory

EDGE_CONFIG = 200
EDGE_QOS_ID = 21
SEGMENT_OBJECT = GLOBAL[3]
HUB_REF = "deviceSettings:vpn:edgeHub"
CLUSTER_REF = "deviceSettings:vpn:edgeHubCluster"
BACKHAUL_REF = "deviceSettings:backHaulEdge"
GET_SERVICES = "enterprise/getEnterpriseServices"
GET_EDGES = "enterprise/getEnterpriseEdgeList"
UPDATE = "configuration/updateConfigurationModule"

ENABLE_EDGE = {
    "hub": "edge/enableEdgeForEdgeHub",
    "bridge": "edge/enableEdgeForEdgeToEdgeBridge",
    "backhaul": "edge/enableEdgeForBackHaul",
}
DISABLE_EDGE = {
    "hub": "edge/disableEdgeForEdgeHub",
    "bridge": "edge/disableEdgeForEdgeToEdgeBridge",
    "backhaul": "edge/disableEdgeForBackHaul",
}
ENABLE_CLUSTER = {
    "hub": "enterprise/enableClusterForEdgeHub",
    "bridge": "enterprise/enableClusterForEdgeToEdgeBridge",
    "backhaul": "enterprise/enableClusterForBackHaul",
}
DISABLE_CLUSTER = {
    "hub": "enterprise/disableClusterForEdgeHub",
    "bridge": "enterprise/disableClusterForEdgeToEdgeBridge",
    "backhaul": "enterprise/disableClusterForBackHaul",
}
ROLE_KEY = {"hub": "edgeHub", "bridge": "edgeToEdgeBridge", "backhaul": "backHaulEdge"}


def make_store(**knobs: Any) -> ModuleStore:
    store = hub_helpers.hub_store(HUB_A, HUB_B, CLUSTER, MEMBER, NOT_A_HUB, **knobs)
    store.add_object(
        SEGMENT_OBJECT,
        logical_id=GLOBAL[2],
        name=GLOBAL[1],
        type_="REGULAR",
        object_="NETWORK_SEGMENT",
        data={"segmentId": GLOBAL[0]},
    )
    ds, qos, firewall = segment_helpers.profile_rows(
        [hub_helpers.ds_with_vpn(GLOBAL)],
        [segment_helpers.qos_entry(GLOBAL)],
        [segment_helpers.firewall_entry(GLOBAL)],
        ds_refs={},
    )
    store.add_configuration(
        {"id": CONFIG, "name": "Profile", "modules": [ds, qos, firewall]}
    )
    store.add_configuration(
        {
            "id": EDGE_CONFIG,
            "name": "Edge Specific Profile",
            "modules": [
                make_module_json(
                    "QOS",
                    {"segments": []},
                    module_id=EDGE_QOS_ID,
                    configuration_id=EDGE_CONFIG,
                )
            ],
        }
    )
    store.add_stack(EDGE_CONFIG, CONFIG, edge_id=NOT_A_HUB.id)
    return store


def params(target: int) -> dict[str, Any]:
    return {
        "id": target,
        "configurationId": CONFIG,
        "moduleId": DS_ID,
        "segmentObjectId": SEGMENT_OBJECT,
    }


def call(store: ModuleStore, method: str, target: int) -> Any:
    return store.handle(method, params(target))


def services(store: ModuleStore, **extra: Any) -> list[dict[str, Any]]:
    return cast(
        list[dict[str, Any]],
        store.handle(GET_SERVICES, {"enterpriseId": 7, **extra}),
    )


def refs(store: ModuleStore, module_id: int = DS_ID) -> dict[str, Any]:
    return cast(dict[str, Any], store.rows[module_id].get("refs", {}))


def rows_of(store: ModuleStore, name: str, module_id: int = DS_ID) -> list[Any]:
    held = refs(store, module_id).get(name)
    if held is None:
        return []
    return held if isinstance(held, list) else [held]


def edge_service_of(store: ModuleStore, hub: Hub) -> dict[str, Any]:
    (found,) = [s for s in services(store, type="edgeHub") if s["edgeId"] == hub.id]
    return found


def add_backhaul_row(store: ModuleStore, service_id: int) -> None:
    """A ``deviceSettings:backHaulEdge`` row of the Edge configuration's QoS module that points at *service_id*."""
    service = store.objects[service_id]
    store.rows[EDGE_QOS_ID]["refs"] = {
        BACKHAUL_REF: {
            "id": 7000 + service_id,
            "configurationId": EDGE_CONFIG,
            "moduleId": EDGE_QOS_ID,
            "enterpriseObjectId": service_id,
            "ref": BACKHAUL_REF,
            "segmentObjectId": SEGMENT_OBJECT,
            "logicalId": service["logicalId"],
            "name": service["name"],
            "type": service["type"],
            "object": service["object"],
            "data": copy.deepcopy(service["data"]),
        }
    }


def roles_of(service: dict[str, Any]) -> dict[str, bool]:
    return cast(dict[str, bool], service["data"]["roles"])


def error_codes(error: RpcError) -> list[str]:
    data = cast(dict[str, Any], error.data)
    return [e.get("code") for e in data["error"]]


# -- Edges and clusters -----------------------------------------------------


def test_add_edge_with_a_cluster_adds_a_member_service():
    store = make_store()
    (member,) = services(store, type="edgeHubClusterMember")
    assert member["edgeId"] == MEMBER.id
    assert member["data"] == {"logicalId": MEMBER.logical_id}
    assert services(store, type="edgeHub") == []


def test_add_cluster_holds_a_cluster_object_with_no_data_and_version_1():
    store = make_store()
    cluster = store.objects[CLUSTER.id]
    assert (cluster["type"], cluster["data"], cluster["version"]) == (
        "edgeHubCluster",
        {},
        1,
    )
    assert cluster["logicalId"] == CLUSTER.logical_id


async def test_the_edge_list_is_one_page_of_the_edges(
    mock_vco_client: MockVcoClientFactory,
):
    store = make_store()
    client = await mock_vco_client(store)
    edges = await client.get_enterprise_edge_list(GetEnterpriseEdgeList())
    assert {(e.id_, e.name, str(e.logical_id)) for e in edges} == {
        (h.id, h.name, h.logical_id) for h in (HUB_A, HUB_B, MEMBER, NOT_A_HUB)
    }
    assert len(store.requests) == 1


# -- The services read ------------------------------------------------------


def test_the_services_read_filters_by_type_and_parses_as_any_service():
    store = make_store()
    everything = services(store)
    assert len(everything) == 2
    parsed = TypeAdapter(list[AnyEnterpriseService]).validate_python(
        [
            {**s, "created": "2024-01-01T00:00:00.000Z"}
            for s in services(store)
            if s["type"] == "edgeHubCluster"
        ]
    )
    assert isinstance(parsed[0], EdgeHubClusterService)
    (member,) = TypeAdapter(list[AnyEnterpriseService]).validate_python(
        services(store, type="edgeHubClusterMember")
    )
    assert isinstance(member, EdgeHubClusterMemberService)
    assert [s["type"] for s in services(store, type="edgeHubCluster")] == [
        "edgeHubCluster"
    ]


def test_the_services_read_answers_configuration_and_qos_usage_only_with_configuration():
    store = make_store()
    _ = call(store, ENABLE_EDGE["hub"], NOT_A_HUB.id)
    service = edge_service_of(store, NOT_A_HUB)
    add_backhaul_row(store, service["id"])

    plain = services(store, type="edgeHub")
    only_usage = services(store, type="edgeHub", **{"with": ["edgeQOSUsage"]})
    for answer in (plain, only_usage):
        assert "configuration" not in answer[0]
        assert "edgeQOSUsage" not in answer[0]

    both = services(
        store, type="edgeHub", **{"with": ["configuration", "edgeQOSUsage"]}
    )
    assert both[0]["configuration"] == [
        {
            "enterpriseObjectId": service["id"],
            "id": CONFIG,
            "name": "Profile",
            "moduleId": DS_ID,
            "segmentObjectId": SEGMENT_OBJECT,
        },
        {
            "enterpriseObjectId": service["id"],
            "id": EDGE_CONFIG,
            "name": "Edge Specific Profile",
            "moduleId": EDGE_QOS_ID,
            "segmentObjectId": SEGMENT_OBJECT,
        },
    ]
    assert both[0]["edgeQOSUsage"] == [
        {
            "enterpriseObjectId": service["id"],
            "segmentObjectId": SEGMENT_OBJECT,
            "ref": BACKHAUL_REF,
            "edgeId": NOT_A_HUB.id,
            "name": NOT_A_HUB.name,
            "logicalId": NOT_A_HUB.logical_id,
            "profileId": EDGE_CONFIG,
        }
    ]
    only_configuration = services(store, type="edgeHub", **{"with": ["configuration"]})
    assert "edgeQOSUsage" not in only_configuration[0]
    (parsed,) = TypeAdapter(list[AnyEnterpriseService]).validate_python(both)
    assert isinstance(parsed, EdgeHubService)


# -- Edge role calls --------------------------------------------------------


def test_an_edge_enable_twice_makes_one_service_and_one_row():
    store = make_store()
    first = call(store, ENABLE_EDGE["hub"], HUB_A.id)
    second = call(store, ENABLE_EDGE["hub"], HUB_A.id)
    assert first["id"] == second["id"]
    assert len(services(store, type="edgeHub")) == 1
    (row,) = rows_of(store, HUB_REF)
    assert row["enterpriseObjectId"] == first["id"]
    assert row["logicalId"] == first["logicalId"]
    assert row["ref"] == HUB_REF
    assert row["segmentObjectId"] == SEGMENT_OBJECT
    assert row["data"] == {
        "logicalId": HUB_A.logical_id,
        "roles": {"edgeHub": True, "edgeToEdgeBridge": False, "backHaulEdge": False},
    }
    parsed = EnterpriseServiceBase.model_validate(
        {**first, "created": "2024-01-01T00:00:00.000Z"}
    )
    assert parsed.edge_id == HUB_A.id


@pytest.mark.parametrize("role", ["bridge", "backhaul"])
def test_an_edge_bridge_or_backhaul_enable_sets_the_role_and_edge_hub(role: str):
    store = make_store()
    answer = call(store, ENABLE_EDGE[role], HUB_A.id)
    assert roles_of(answer) == {
        "edgeHub": True,
        "edgeToEdgeBridge": role == "bridge",
        "backHaulEdge": role == "backhaul",
    }
    (row,) = rows_of(store, HUB_REF)
    assert row["data"]["roles"] == roles_of(answer)


def test_an_edge_enable_leaves_the_module_data_and_version():
    store = make_store()
    before = copy.deepcopy(store.rows[DS_ID])
    _ = call(store, ENABLE_EDGE["hub"], HUB_A.id)
    _ = call(store, ENABLE_EDGE["backhaul"], HUB_A.id)
    assert store.rows[DS_ID]["data"] == before["data"]
    assert store.rows[DS_ID]["version"] == before["version"]


def test_an_edge_enable_for_a_cluster_member_is_refused():
    store = make_store()
    with pytest.raises(RpcError) as refused:
        _ = call(store, ENABLE_EDGE["hub"], MEMBER.id)
    assert refused.value.code == -32003
    assert refused.value.message == "validationError"
    assert services(store, type="edgeHub") == []
    assert rows_of(store, HUB_REF) == []


def test_an_edge_hub_disable_deletes_the_service_and_every_row_pointing_at_it():
    store = make_store()
    service = call(store, ENABLE_EDGE["hub"], HUB_A.id)
    other = call(store, ENABLE_EDGE["hub"], HUB_B.id)
    add_backhaul_row(store, service["id"])
    assert call(store, DISABLE_EDGE["hub"], HUB_A.id) == {"rows": 1}
    assert service["id"] not in store.objects
    assert [r["enterpriseObjectId"] for r in rows_of(store, HUB_REF)] == [other["id"]]
    assert rows_of(store, BACKHAUL_REF, EDGE_QOS_ID) == []
    assert "refs" not in store.rows[EDGE_QOS_ID]


@pytest.mark.parametrize("role", ["hub", "bridge", "backhaul"])
def test_an_edge_disable_with_no_service_answers_null(role: str):
    store = make_store()
    assert call(store, DISABLE_EDGE[role], HUB_A.id) is None
    assert services(store, type="edgeHub") == []


@pytest.mark.parametrize("role", ["bridge", "backhaul"])
def test_an_edge_bridge_or_backhaul_disable_clears_its_role_and_answers_the_service(
    role: str,
):
    store = make_store()
    _ = call(store, ENABLE_EDGE["bridge"], HUB_A.id)
    _ = call(store, ENABLE_EDGE["backhaul"], HUB_A.id)
    answer = call(store, DISABLE_EDGE[role], HUB_A.id)
    expected = {"edgeHub": True, "edgeToEdgeBridge": True, "backHaulEdge": True}
    expected[ROLE_KEY[role]] = False
    assert roles_of(answer) == expected
    assert answer["id"] == edge_service_of(store, HUB_A)["id"]
    (row,) = rows_of(store, HUB_REF)
    assert row["data"]["roles"] == expected


# -- Cluster role calls -----------------------------------------------------


def cluster(store: ModuleStore) -> dict[str, Any]:
    return store.objects[CLUSTER.id]


def test_a_cluster_enable_sets_the_segment_roles_the_row_and_bumps_the_version():
    store = make_store()
    answer = call(store, ENABLE_CLUSTER["hub"], CLUSTER.id)
    _ = call(store, ENABLE_CLUSTER["hub"], CLUSTER.id)
    segment = str(GLOBAL[0])
    roles = {"edgeHub": True, "edgeToEdgeBridge": False, "backHaulEdge": False}
    assert cluster(store)["data"] == {str(CONFIG): {segment: {"roles": roles}}}
    assert cluster(store)["version"] == 3
    assert answer["version"] == "2"
    (row,) = rows_of(store, CLUSTER_REF)
    assert row["enterpriseObjectId"] == CLUSTER.id
    assert row["logicalId"] == CLUSTER.logical_id
    assert row["segmentObjectId"] == SEGMENT_OBJECT
    assert row["data"] == {
        str(CONFIG): {segment: {"roles": roles}}
    }  # the cluster's own data
    parsed = EdgeHubClusterService.model_validate(
        {**answer, "created": "2024-01-01T00:00:00.000Z"}
    )
    assert parsed.name == CLUSTER.name


@pytest.mark.parametrize("role", ["bridge", "backhaul"])
def test_a_cluster_bridge_or_backhaul_enable_and_disable_set_and_clear_its_flag(
    role: str,
):
    store = make_store()
    _ = call(store, ENABLE_CLUSTER[role], CLUSTER.id)
    held = cluster(store)["data"][str(CONFIG)][str(GLOBAL[0])]["roles"]
    assert held == {
        "edgeHub": True,
        "edgeToEdgeBridge": role == "bridge",
        "backHaulEdge": role == "backhaul",
    }
    _ = call(store, DISABLE_CLUSTER[role], CLUSTER.id)
    held = cluster(store)["data"][str(CONFIG)][str(GLOBAL[0])]["roles"]
    assert held == {"edgeHub": True, "edgeToEdgeBridge": False, "backHaulEdge": False}
    (row,) = rows_of(store, CLUSTER_REF)
    assert row["data"][str(CONFIG)][str(GLOBAL[0])]["roles"] == held


def test_a_cluster_hub_disable_clears_the_segment_roles_and_leaves_the_row():
    store = make_store()
    _ = call(store, ENABLE_CLUSTER["bridge"], CLUSTER.id)
    answer = call(store, DISABLE_CLUSTER["hub"], CLUSTER.id)
    roles = cluster(store)["data"][str(CONFIG)][str(GLOBAL[0])]["roles"]
    assert roles == {"edgeHub": False, "edgeToEdgeBridge": False, "backHaulEdge": False}
    assert len(rows_of(store, CLUSTER_REF)) == 1
    assert answer["id"] == CLUSTER.id


def test_a_cluster_hub_disable_with_no_profile_data_fails_and_changes_nothing():
    store = make_store()
    before = copy.deepcopy(cluster(store))
    with pytest.raises(RpcError) as failed:
        _ = call(store, DISABLE_CLUSTER["hub"], CLUSTER.id)
    assert failed.value.code == -32603
    assert failed.value.message == "Cannot convert undefined or null to object"
    assert cluster(store) == before
    assert rows_of(store, CLUSTER_REF) == []


@pytest.mark.parametrize(
    "method",
    [
        *ENABLE_CLUSTER.values(),
        *DISABLE_CLUSTER.values(),
    ],
)
def test_every_cluster_call_bumps_the_version(method: str):
    store = make_store()
    _ = call(store, ENABLE_CLUSTER["hub"], CLUSTER.id)
    before = cluster(store)["version"]
    _ = call(store, method, CLUSTER.id)
    assert cluster(store)["version"] == before + 1


# -- fail_calls -------------------------------------------------------------


def test_fail_calls_fails_the_named_call_once():
    store = make_store()
    store.fail_calls.add(ENABLE_EDGE["backhaul"])
    with pytest.raises(RpcError) as failed:
        _ = call(store, ENABLE_EDGE["backhaul"], HUB_A.id)
    assert (failed.value.code, failed.value.message) == (-32603, "simulated failure")
    assert services(store, type="edgeHub") == []
    assert store.fail_calls == set()
    assert roles_of(call(store, ENABLE_EDGE["backhaul"], HUB_A.id))["backHaulEdge"]


# -- the hub list check and the cleanup of an update -----------------------------


def update_ds(
    store: ModuleStore, entry: dict[str, Any], sent_refs: dict[str, Any]
) -> object:
    data = copy.deepcopy(store.rows[DS_ID]["data"])
    data["segments"] = [entry]
    return store.handle(
        UPDATE,
        {"id": DS_ID, "_update": {"data": data, "refs": sent_refs}},
    )


def hub_ref(store: ModuleStore, hub: Hub) -> dict[str, Any]:
    """The refs holding the row for *hub*, whose service the store holds (made with an enable if an Edge)."""
    if hub.kind == "edge":
        service = call(store, ENABLE_EDGE["hub"], hub.id)
        row = hub_helpers.hub_row(
            hub,
            service_id=service["id"],
            service_logical_id=service["logicalId"],
            segment=GLOBAL,
        )
        return {HUB_REF: row}
    row = hub_helpers.hub_row(
        hub, service_id=hub.id, service_logical_id=hub.logical_id, segment=GLOBAL
    )
    return {CLUSTER_REF: row}


def merge(*sent: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for one in sent:
        for name, row in one.items():
            merged.setdefault(name, []).append(row)
    return merged


def refused_codes(
    store: ModuleStore, entry: dict[str, Any], sent_refs: dict[str, Any]
) -> list[str]:
    version = store.rows[DS_ID]["version"]
    with pytest.raises(RpcError) as refused:
        _ = update_ds(store, entry, sent_refs)
    assert refused.value.code == -32003
    assert refused.value.message == "validationError"
    assert store.rows[DS_ID]["version"] == version
    return error_codes(refused.value)


def test_vpn_check_accepts_a_matching_edge_and_cluster_list():
    store = make_store(vpn_check=True)
    sent = merge(hub_ref(store, HUB_A), hub_ref(store, CLUSTER))
    entry = hub_helpers.ds_with_vpn(GLOBAL, hubs=(HUB_A, CLUSTER))
    answer = update_ds(store, entry, sent)
    assert answer == {"rows": 1}
    assert len(rows_of(store, HUB_REF)) == 1
    assert len(rows_of(store, CLUSTER_REF)) == 1


@pytest.mark.parametrize(
    ("listed", "held"), [((HUB_A,), (HUB_A, HUB_B)), ((HUB_A, HUB_B), (HUB_A,))]
)
def test_vpn_check_refuses_a_list_and_rows_that_differ_either_way(
    listed: tuple[Hub, ...], held: tuple[Hub, ...]
):
    store = make_store(vpn_check=True)
    sent = merge(*(hub_ref(store, h) for h in held))
    entry = hub_helpers.ds_with_vpn(GLOBAL, hubs=listed)
    assert refused_codes(store, entry, sent) == ["DS00000020"]


def test_vpn_check_refuses_an_enabled_hub_list_with_no_hub_row():
    store = make_store(vpn_check=True)
    entry = hub_helpers.ds_with_vpn(GLOBAL, hub_list_enabled=True)
    assert refused_codes(store, entry, {}) == ["DS00000010"]


def test_vpn_check_refuses_a_disabled_hub_list_with_an_edge_hub_row():
    store = make_store(vpn_check=True)
    sent = hub_ref(store, HUB_A)
    entry = hub_helpers.ds_with_vpn(GLOBAL, hubs=(HUB_A,), hub_list_enabled=False)
    assert refused_codes(store, entry, sent) == ["DS00000011"]


def test_vpn_check_is_off_by_default():
    store = make_store()
    entry = hub_helpers.ds_with_vpn(GLOBAL, hub_list_enabled=True)
    assert update_ds(store, entry, {}) == {"rows": 1}


def test_vpn_check_is_a_public_attribute_set_after_construction():
    store = make_store()
    assert store.vpn_check is False
    store.vpn_check = True
    entry = hub_helpers.ds_with_vpn(GLOBAL, hub_list_enabled=True)
    assert refused_codes(store, entry, {}) == ["DS00000010"]


def test_an_update_that_drops_a_live_edge_hub_row_deletes_the_service():
    store = make_store()
    service = call(store, ENABLE_EDGE["hub"], HUB_A.id)
    kept = call(store, ENABLE_EDGE["hub"], HUB_B.id)
    add_backhaul_row(store, service["id"])
    sent = {HUB_REF: copy.deepcopy(rows_of(store, HUB_REF)[1])}
    assert sent[HUB_REF]["enterpriseObjectId"] == kept["id"]
    _ = update_ds(store, hub_helpers.ds_with_vpn(GLOBAL), sent)
    assert service["id"] not in store.objects
    assert kept["id"] in store.objects
    assert "refs" not in store.rows[EDGE_QOS_ID]


def test_an_update_that_keeps_the_row_keeps_the_service():
    store = make_store()
    service = call(store, ENABLE_EDGE["hub"], HUB_A.id)
    sent = {HUB_REF: copy.deepcopy(rows_of(store, HUB_REF)[0])}
    _ = update_ds(store, hub_helpers.ds_with_vpn(GLOBAL), sent)
    assert service["id"] in store.objects
