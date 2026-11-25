"""States and pipelines over hubs: a profile or an edge whose device settings hold hub rows, and a store that agrees with them.

Test modules import this as they import ``profile_state``
(``from hub_state import ...``). The hubs, entries and rows come from
``hub_helpers``, the segments from ``segment_helpers``.
"""

import copy
import uuid
from collections.abc import Sequence
from typing import Any, Final, cast

from hub_helpers import CLUSTER, HUB_A, HUB_B, MEMBER, NOT_A_HUB, Hub
from profile_state import (
    module_store_pipeline,
    profile_state,
)
from pydantic import MISSING
from pyvelo.client.hub_services import CLUSTER_HUB_REF, EDGE_HUB_REF
from pyvelo.client.schemas.configuration import EdgeConfiguration
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.state import ConfigState
from pyvelo.core.base import is_dict, is_list
from pyvelo.core.identity import HubIdentity
from pyvelo.testing import ModuleStore, make_module_json
from pyvelo.testing.fixtures import make_hub_resolver
from segment_helpers import GLOBAL, GUEST, VOICE

ALL_HUBS: Final = (HUB_A, HUB_B, CLUSTER, MEMBER, NOT_A_HUB)
"""The hubs of a state and a store unless a test gives others: ``MEMBER`` is a member of ``CLUSTER``."""
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
    """Register a loaded ``HubResolver`` over *hubs* on *state*: a member of a cluster is no hub."""
    resolver = await make_hub_resolver(
        [_identity(h) for h in hubs if h != MEMBER],
        members=[(uuid.UUID(h.logical_id), h.name) for h in hubs if h == MEMBER],
    )
    state.resolvers.register(resolver)
    return state


async def hub_profile_state(
    ds: list[dict[str, Any]],
    qos: list[dict[str, Any]],
    firewall: list[dict[str, Any]],
    *,
    ds_refs: dict[str, Any] | MISSING,
    qos_refs: dict[str, Any] | MISSING = MISSING,
    hubs: Sequence[Hub] = ALL_HUBS,
) -> ConfigState:
    """A profile state of the three modules (``profile_state``), with a loaded ``HubResolver`` over *hubs*.

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


async def hub_edge_state(
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
    """An edge state of ``EDGE`` over a profile (``hub_profile_state``) whose device settings hold hub rows.

    The edge's own device settings and QoS modules (configuration
    ``EDGE_CONFIG``) have *edge_ds* and *edge_qos* as their segments, and no
    refs, but *edge_qos_refs* for QoS; the profile's refs are *profile_refs*
    and *profile_qos_refs*. The state's ``resolver_context`` names the Edge (``edge_id``), so
    ``hub_pipeline`` registers the stack for it and the store's QoS usage of a
    service finds it.
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
    """Hold the service each hub row of *state* points at, so the store and the state agree as read."""
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
    """A pipeline over a ``MockVcoClient`` for *state*, whose store holds *hubs* and the services the state's hub rows name.

    The store holds the segment objects of ``module_store_pipeline``, the Edges and clusters (``add_edge``, ``add_cluster``;
    ``MEMBER`` as a member of ``CLUSTER``) and, for each hub row of the
    profile's device settings, the service it points at, with the roles the row
    shows. An edge state's stack is registered for its Edge. *vpn_check* is the
    store's (the VCO's check of a device settings update against its hub rows).
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
    """The refs of a profile's device settings module for the segment rows and hub rows *rows*, as the VCO groups them.

    A row is held by its ``ref``: one row bare, several a list.
    """
    refs: dict[str, Any] = {}
    for row in rows:
        refs.setdefault(row["ref"], []).append(copy.deepcopy(row))
    return {name: held[0] if len(held) == 1 else held for name, held in refs.items()}
