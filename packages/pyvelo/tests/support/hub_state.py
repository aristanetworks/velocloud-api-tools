"""States and pipelines over hubs.

A profile or an edge whose device settings hold hub rows, and a store that agrees with
them.

Test modules import this as they import ``profile_state``
(``from hub_state import ...``). The hubs, entries and rows come from
``hub_helpers``, the segments from ``segment_helpers``.
"""

import copy
import uuid
from collections.abc import Sequence
from typing import Any, Final, cast

from hub_helpers import (
    CLUSTER,
    HUB_A,
    HUB_B,
    MEMBER,
    NOT_A_HUB,
    Hub,
    backhaul_entry,
    backhaul_row,
    backhaul_rule,
    ds_with_vpn,
    hub_row,
)
from profile_state import (
    module_store_pipeline,
    profile_state,
)
from pydantic import MISSING
from pyvelo.client.hub_services import BACKHAUL_REF, CLUSTER_HUB_REF, EDGE_HUB_REF
from pyvelo.client.models.configuration.profile_segments import segment_template
from pyvelo.client.schemas.configuration import EdgeConfiguration
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.state import ConfigState
from pyvelo.core import delete
from pyvelo.core.base import is_dict, is_list
from pyvelo.core.identity import HubIdentity
from pyvelo.testing import ModuleStore, make_module_json
from pyvelo.testing.fixtures import make_hub_resolver
from refs_helpers import QOS_ID, segment_row
from segment_helpers import (
    GLOBAL,
    GUEST,
    VOICE,
    ds_entry,
    firewall_entry,
    qos_entry,
)

ALL_HUBS: Final = (HUB_A, HUB_B, CLUSTER, MEMBER, NOT_A_HUB)
"""The hubs of a state and a store unless a test gives others: ``MEMBER`` is a member of
``CLUSTER``.
"""
EDGE_CONFIG: Final = 50
EDGE_DS_ID: Final = 51
EDGE_QOS_ID: Final = 52
EDGE: Final = NOT_A_HUB
"""The Edge an edge state is of."""


def _identity(hub: Hub) -> HubIdentity:
    logical_id = uuid.UUID(hub.logical_id)
    if hub.kind == "cluster":
        return HubIdentity(
            hub.name,
            "cluster",
            "edgeHubCluster",
            logical_id,
            None,
            None,
            hub.id,
            logical_id,
        )
    return HubIdentity(
        hub.name, "edge", "edgeHub", logical_id, hub.id, logical_id, None, None
    )


async def _with_hubs(state: ConfigState, hubs: Sequence[Hub]) -> ConfigState:
    """Register a loaded ``HubResolver`` over *hubs* on *state*.

    A member of a cluster is no hub.
    """
    resolver = await make_hub_resolver(
        [_identity(h) for h in hubs if h != MEMBER],
        members=[(uuid.UUID(h.logical_id), h.name) for h in hubs if h == MEMBER],
    )
    state.resolvers.register(resolver)
    return state


async def hub_profile_state(  # noqa: PLR0913 (existing: split it when it next changes)
    ds: list[dict[str, Any]],
    qos: list[dict[str, Any]],
    firewall: list[dict[str, Any]],
    *,
    ds_refs: dict[str, Any] | MISSING,
    qos_refs: dict[str, Any] | MISSING = MISSING,
    hubs: Sequence[Hub] = ALL_HUBS,
) -> ConfigState:
    """A profile state of the three modules (``profile_state``).

    It has a loaded ``HubResolver`` over *hubs*.

    *ds_refs* are the refs of the device settings module: the segment rows
    and the hub rows the test needs (``hub_helpers.hub_row``); *qos_refs* those
    of the QoS module (the backhaul rows).
    """
    state = await profile_state(
        ds,
        qos,
        firewall,
        segments=(GLOBAL, GUEST, VOICE),
        ds_refs=ds_refs,
        qos_refs=qos_refs,
    )
    return await _with_hubs(state, hubs)


async def hub_edge_state(  # noqa: PLR0913 (existing: split it when it next changes)
    edge_ds: list[dict[str, Any]],
    edge_qos: list[dict[str, Any]],
    *,
    profile_ds: list[dict[str, Any]],
    profile_qos: list[dict[str, Any]],
    profile_refs: dict[str, Any] | MISSING,
    profile_qos_refs: dict[str, Any] | MISSING = MISSING,
    edge_qos_refs: dict[str, Any] | MISSING = MISSING,
    hubs: Sequence[Hub] = ALL_HUBS,
) -> ConfigState:
    """An edge state of ``EDGE`` over a profile (``hub_profile_state``).

    The profile's device settings hold hub rows.

    The edge's own device settings and QoS modules (configuration ``EDGE_CONFIG``) have
    *edge_ds* and *edge_qos* as their segments, and no refs, but *edge_qos_refs* for
    QoS; the profile's refs are *profile_refs* and *profile_qos_refs*. The state's
    ``resolver_context`` names the Edge (``edge_id``), so ``hub_pipeline`` registers the
    stack for it and the store's QoS usage of a service finds it.
    """
    profile = await hub_profile_state(
        profile_ds,
        profile_qos,
        [],
        ds_refs=profile_refs,
        qos_refs=profile_qos_refs,
        hubs=hubs,
    )
    edge = EdgeConfiguration.model_validate(
        {
            "name": "Edge Specific Profile",
            "id": EDGE_CONFIG,
            "modules": [
                make_module_json(
                    "deviceSettings",
                    {
                        "lan": {"networks": [], "interfaces": []},
                        "segments": copy.deepcopy(edge_ds),
                    },
                    module_id=EDGE_DS_ID,
                    configuration_id=EDGE_CONFIG,
                    refs={},
                ),
                make_module_json(
                    "QOS",
                    {"segments": copy.deepcopy(edge_qos)},
                    module_id=EDGE_QOS_ID,
                    configuration_id=EDGE_CONFIG,
                    refs=copy.deepcopy(edge_qos_refs),
                ),
            ],
        }
    )
    state = ConfigState(
        edge_config=edge,
        profile_config=profile.profile_config,
        target_type="edge",
        resolver_context=ResolverContext(edge_id=EDGE.id),
        resolvers=profile.resolvers,
    )
    state.take_snapshot()
    return state


def _hub_rows(state: ConfigState) -> list[tuple[str, dict[str, Any]]]:
    """Each hub row of the profile's device settings module, with its ref name."""
    modules = state.profile_config.modules
    found: list[tuple[str, dict[str, Any]]] = []
    for module in [] if modules is MISSING else modules:
        refs: object = module.raw_refs
        if module.name != "deviceSettings" or not is_dict(refs):
            continue
        for name in (EDGE_HUB_REF, CLUSTER_HUB_REF):
            held = refs.get(name)
            rows = held if is_list(held) else [held]
            found.extend(
                (name, cast("dict[str, Any]", row)) for row in rows if is_dict(row)
            )
    return found


def _seed_services(store: ModuleStore, state: ConfigState) -> None:
    """Hold the service each hub row of *state* points at.

    Then the store and the state agree as read.
    """
    for name, row in _hub_rows(state):
        object_id: int = row["enterpriseObjectId"]
        if name == CLUSTER_HUB_REF:
            held = store.objects[object_id]["data"]
            for configuration, by_segment in row["data"].items():
                held.setdefault(configuration, {}).update(copy.deepcopy(by_segment))
            continue
        store.add_object(
            object_id,
            logical_id=row["logicalId"],
            name=row["name"],
            type_="edgeHub",
            data={
                "logicalId": row["data"]["logicalId"],
                "roles": copy.deepcopy(row["data"]["roles"]),
            },
        )
        edge = next(
            e
            for e in store.edges.values()
            if e["logicalId"] == row["data"]["logicalId"]
        )
        store.objects[object_id].update(
            edgeId=edge["id"],
            configurationId=row["configurationId"],
            moduleId=row["moduleId"],
            segmentObjectId=row["segmentObjectId"],
        )


def hub_pipeline(
    state: ConfigState,
    *,
    vpn_check: bool = False,
    hubs: Sequence[Hub] = ALL_HUBS,
) -> tuple[ConfigPipeline, ModuleStore]:
    """A pipeline over a ``MockVcoClient`` for *state*.

    Its store holds *hubs* and the services the state's hub rows name.

    The store holds the segment objects of ``module_store_pipeline``, the Edges and
    clusters (``add_edge``, ``add_cluster``; ``MEMBER`` as a member of ``CLUSTER``) and,
    for each hub row of the profile's device settings, the service it points at, with
    the roles the row shows. An edge state's stack is registered for its Edge.
    *vpn_check* is the store's (the VCO's check of a device settings update against its
    hub rows).
    """
    pipeline, store = module_store_pipeline(state)
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
    _seed_services(store, state)
    if state.edge_config is not None:
        edge_id = state.resolver_context.edge_id
        profile_id = state.profile_config.id_
        edge_config_id = state.edge_config.id_
        if (
            edge_id is not None
            and isinstance(profile_id, int)
            and isinstance(edge_config_id, int)
        ):
            store.add_stack(edge_config_id, profile_id, edge_id=edge_id)
    store.vpn_check = vpn_check
    return pipeline, store


def profile_hub_rows(*rows: dict[str, Any]) -> dict[str, Any]:
    """The refs of a profile's device settings module for the rows *rows*.

    The rows are segment rows and hub rows, grouped as the VCO groups them.

    A row is held by its ``ref``: one row bare, several a list.
    """
    refs: dict[str, Any] = {}
    for row in rows:
        refs.setdefault(row["ref"], []).append(copy.deepcopy(row))
    return {name: held[0] if len(held) == 1 else held for name, held in refs.items()}


GUEST_SERVICES: Final = {
    HUB_A: (601, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    HUB_B: (602, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
}
"""The service in the Guest segment of each hub of ``guest_backhaul_state``.

Its ID and its logical ID.
"""


def hub_backhaul_rule(
    name: str, *hubs: Hub, route_action: str = "edge2CloudRouteAction"
) -> dict[str, Any]:
    """A QoS rule named *name* whose *route_action* backhauls to *hubs*.

    Its ``routePolicy`` is ``"backhaul"`` and its ``routeCfg.type`` ``"edge"``.
    """
    rule = segment_template("QOS")["rules"][0]
    rule["name"] = name
    rule["ruleLogicalId"] = str(uuid.uuid5(uuid.NAMESPACE_URL, name))
    action = rule["action"][route_action]
    action["routePolicy"] = "backhaul"
    action["routeCfg"] = {
        "type": "edge",
        "backhaulEdges": [backhaul_entry(h, GUEST_SERVICES[h][1]) for h in hubs],
    }
    return rule


def named_hubs(rules: list[dict[str, Any]]) -> list[Hub]:
    """The hubs the rules' route actions name, each once."""
    names = {
        entry["name"]
        for rule in rules
        for action in rule["action"].values()
        if isinstance(action, dict)
        for entry in action.get("routeCfg", {}).get("backhaulEdges", [])
    }
    return [hub for hub in GUEST_SERVICES if hub.name in names]


async def guest_backhaul_state(*rules: dict[str, Any]) -> ConfigState:
    """A profile whose Guest segment has Hub-A and Hub-B as hubs and backhaul hubs.

    Guest's QoS rules are *rules*.
    """
    refs = profile_hub_rows(
        segment_row(GLOBAL[0], GLOBAL[3], GLOBAL[2]),
        segment_row(GUEST[0], GUEST[3], GUEST[2]),
        *(
            hub_row(
                hub,
                service_id=service_id,
                service_logical_id=service,
                segment=GUEST,
                roles=("hub", "backhaul"),
            )
            for hub, (service_id, service) in GUEST_SERVICES.items()
        ),
    )
    rows = [
        backhaul_row(
            hub,
            service_id=GUEST_SERVICES[hub][0],
            service_logical_id=GUEST_SERVICES[hub][1],
            segment=GUEST,
            module_id=QOS_ID,
        )
        for hub in named_hubs(list(rules))
    ]
    return await hub_profile_state(
        [
            ds_entry(GLOBAL),
            ds_with_vpn(GUEST, hubs=(HUB_A, HUB_B), backhaul=(HUB_A, HUB_B)),
        ],
        [qos_entry(GLOBAL), qos_entry(GUEST, rules=list(rules))],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
        ds_refs=refs,
        qos_refs=(
            {BACKHAUL_REF: rows[0] if len(rows) == 1 else rows} if rows else MISSING
        ),
    )


# --- Hub-A taken out of Guest's hub lists and out of the rule Web ------------

DS = "deviceSettings"
QOS = "QOS"
SERVICE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SERVICE_ID = 601
GONE = {HUB_A.name: delete()}
FROM_BOTH_LISTS: dict[str, Any] = {
    "segments": {
        "Guest": {
            "vpn": {
                "backHaulEdges": GONE,
                "edgeToEdgeHub": {"enabled": False, "vpnHubs": GONE},
            }
        }
    }
}
RULE_ENTRY = {"Hub-A": {"_op": "delete"}}
OUT_OF_THE_RULE = {
    "segments": {
        "Guest": {
            "rules": {
                "Web": {
                    "action": {
                        "edge2EdgeRouteAction": {
                            "routeCfg": {"backhaulEdges": RULE_ENTRY}
                        }
                    }
                }
            }
        }
    }
}


async def _hub_a_in_guest(global_entry: dict[str, Any]) -> ConfigState:
    """``rule_uses_hub``, with *global_entry* as the device settings of Global."""
    refs = profile_hub_rows(
        segment_row(GLOBAL[0], GLOBAL[3], GLOBAL[2]),
        segment_row(GUEST[0], GUEST[3], GUEST[2]),
        hub_row(
            HUB_A,
            service_id=SERVICE_ID,
            service_logical_id=SERVICE,
            segment=GUEST,
            roles=("hub", "backhaul"),
        ),
    )
    backhaul = backhaul_row(
        HUB_A,
        service_id=SERVICE_ID,
        service_logical_id=SERVICE,
        segment=GUEST,
        module_id=QOS_ID,
    )
    rule = backhaul_rule("Web", backhaul_entry(HUB_A, SERVICE))
    return await hub_profile_state(
        [global_entry, ds_with_vpn(GUEST, hubs=(HUB_A,), backhaul=(HUB_A,))],
        [qos_entry(GLOBAL), qos_entry(GUEST, rules=[rule])],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
        ds_refs=refs,
        qos_refs={BACKHAUL_REF: backhaul},
    )


async def rule_uses_hub() -> ConfigState:
    """Guest has Hub-A as hub and backhaul hub, and its rule ``Web`` backhauls to it."""
    return await _hub_a_in_guest(ds_entry(GLOBAL))


# --- A BGP neighbour with a password, in the Global Segment of that state ----

OLD_PASSWORD, NEW_PASSWORD = "OLD-md5-made-up", "NEW-md5-made-up"
"""Two made-up MD5 passwords: the values of ``OLD`` and ``NEW`` in ``test_secrets``."""
NEIGHBOR_IP = "192.0.2.1"


def bgp_with_a_password(password: str) -> dict[str, Any]:
    """The ``bgp`` of a segment: one neighbour, ``NEIGHBOR_IP``, with *password*.

    The one ``test_secrets`` gives its device settings; the password is the
    neighbour's last key.
    """
    return {
        "enabled": True,
        "override": True,
        "ASN": "65000",
        "neighbors": [
            {
                "neighborIp": NEIGHBOR_IP,
                "neighborAS": "65001",
                "enableMd5": True,
                "id": "00000000-0000-0000-0000-0000000000aa",
                "md5Password": password,
            }
        ],
    }


async def state_with_a_bgp_password(password: str) -> ConfigState:
    """``rule_uses_hub`` whose Global Segment has a BGP neighbour with *password*.

    A profile state: the neighbour is ``NEIGHBOR_IP`` of the device settings'
    ``segments["Global Segment"].bgp``, and its ``md5_password`` is a secret.
    """
    entry = {**ds_entry(GLOBAL), "bgp": bgp_with_a_password(password)}
    return await _hub_a_in_guest(entry)


NEW_PASSWORD_PATCH: dict[str, Any] = {
    "segments": {
        "Global Segment": {
            "bgp": {"neighbors": {NEIGHBOR_IP: {"md5_password": NEW_PASSWORD}}}
        }
    }
}
"""A device settings patch: the neighbour, by ``neighbor_ip``, gets ``NEW_PASSWORD``."""


async def edge_over_a_hub_profile() -> ConfigState:
    """An edge with a QoS rule and device settings of its own, over a hub profile."""
    refs = profile_hub_rows(
        segment_row(GLOBAL[0], GLOBAL[3], GLOBAL[2]),
        segment_row(GUEST[0], GUEST[3], GUEST[2]),
        hub_row(
            HUB_A,
            service_id=SERVICE_ID,
            service_logical_id=SERVICE,
            segment=GUEST,
            roles=("hub", "backhaul"),
        ),
    )
    return await hub_edge_state(
        [ds_entry(GLOBAL), ds_entry(GUEST)],
        [qos_entry(GLOBAL), qos_entry(GUEST, rules=[backhaul_rule("Web")])],
        profile_ds=[
            ds_entry(GLOBAL),
            ds_with_vpn(GUEST, hubs=(HUB_A,), backhaul=(HUB_A,)),
        ],
        profile_qos=[qos_entry(GLOBAL), qos_entry(GUEST)],
        profile_refs=refs,
    )


async def edit(state: ConfigState, module: str, patch: dict[str, Any]) -> None:
    _ = await ConfigPipeline(client=None).apply(state, module=module, patch=patch)


PORT = {"segments": {"Guest": {"rules": {"Web": {"match": {"dport_low": 443}}}}}}


async def edited() -> ConfigState:
    """``rule_uses_hub`` with an edit in each of its three modules.

    Hub-A is out of Guest's hub lists and out of the rule ``Web``, whose
    destination port is 443, and the firewall logs.
    """
    state = await rule_uses_hub()
    await edit(state, "deviceSettings", FROM_BOTH_LISTS)
    await edit(state, "QOS", OUT_OF_THE_RULE)
    await edit(state, "firewall", {"firewall_logging_enabled": True})
    await edit(state, "QOS", PORT)
    return state


async def removal_and_its_rule() -> tuple[ConfigPipeline, ModuleStore, ConfigState]:
    state = await rule_uses_hub()
    await edit(state, DS, FROM_BOTH_LISTS)
    await edit(state, QOS, OUT_OF_THE_RULE)
    pipeline, store = hub_pipeline(state, vpn_check=True)
    return pipeline, store, state
