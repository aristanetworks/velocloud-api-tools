"""The save's calls for the role steps: the hub services, the role calls and the hub rows.

``hub_steps`` plans the steps from the module as read and as edited. The save
reads the hub services (``read_services``), sends each step not already in
place (``in_place``, ``send_step``) and writes the rows the steps made
(``hub_rows``). ``hubs_in_use`` finds, from the services read, the Edge hubs a
step removes whose service edge QoS rules use (``report_hubs_in_use`` says so).
"""

import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, cast

from pydantic import MISSING, ValidationError
from pyvelo.client.enums.enterprise import (
    EnterpriseServicesParamsWith,
    EnterpriseServiceType,
)
from pyvelo.client.exceptions import HubListError
from pyvelo.client.hub_services import BACKHAUL_REF, CLUSTER_HUB_REF, EDGE_HUB_REF
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.ref_owners import rows_named
from pyvelo.client.models.enterprise.service_data import EdgeRoles
from pyvelo.client.protocol import VcoClientProtocol
from pyvelo.client.schemas.edge import (
    DisableEdgeBackHaul,
    DisableEdgeHub,
    DisableEdgeToEdgeBridge,
    EnableEdgeBackHaul,
    EnableEdgeHub,
    EnableEdgeToEdgeBridge,
)
from pyvelo.client.schemas.enterprise import (
    DisableClusterBackHaul,
    DisableClusterHub,
    DisableClusterToEdgeBridge,
    EdgeHubClusterService,
    EdgeHubService,
    EnableClusterBackHaul,
    EnableClusterHub,
    EnableClusterToEdgeBridge,
    GetEnterpriseServices,
)
from pyvelo.core.base import is_dict
from pyvelo.core.identity import HubServiceNotFoundError

from pyvelo_intent.config.hub_steps import (
    Action,
    HubRef,
    HubStep,
    Role,
    hub_in_use_message,
    hub_lists_of,
)
from pyvelo_intent.config.validation_warning import ValidationWarning

_ROLE_FIELDS: Final[dict[Role, str]] = {
    "hub": "edge_hub",
    "bridge": "edge_to_edge_bridge",
    "backhaul": "back_haul_edge",
}
"""The attribute of ``EdgeRoles`` that holds each role."""


@dataclass(frozen=True)
class HubServices:
    """The hub services of one read of the enterprise: the Edge hubs (with the rows that point at them) and the clusters."""

    edge_hubs: tuple[EdgeHubService, ...] = ()
    clusters: tuple[EdgeHubClusterService, ...] = ()

    def edge_service(
        self,
        hub_logical_id: uuid.UUID,
        configuration_id: int,
        module_id: int,
        segment_object_id: int,
    ) -> EdgeHubService | None:
        """The service of the Edge with logical ID *hub_logical_id* for that profile, module and segment, or None.

        It is the service whose ``data.logical_id`` is the Edge's and which has
        a ``configuration`` row of that profile, module and segment. Raises
        ``HubListError`` when several do: a role call could act on any of
        them, and the VCO's own lookup takes one and only logs a warning.
        """
        found = [
            service
            for service in self.edge_hubs
            if service.data.logical_id == hub_logical_id
            and _has_configuration(
                service, configuration_id, module_id, segment_object_id
            )
        ]
        if len(found) > 1:
            ids = ", ".join(str(service.id_) for service in found)
            raise HubListError(
                f"The VCO holds {len(found)} Edge hub services (IDs {ids}) for "
                f"the Edge with logical ID {hub_logical_id} in segment object "
                f"{segment_object_id} of profile {configuration_id}, and a "
                "role call could act on any of them: the VCO takes one and "
                "only logs a warning. The save stops before it sends a role "
                "call for the hub, so that no role is set on a service the "
                "profile may not use. Remove the services the VCO holds twice "
                "(in the Orchestrator, the hub's configuration for the "
                "profile), so that one is left, then save again.",
                edges=[
                    (service.edge_id, service.name)
                    for service in found
                    if isinstance(service.edge_id, int)
                ],
            )
        return found[0] if found else None

    def cluster(self, logical_id: uuid.UUID) -> EdgeHubClusterService | None:
        """The hub cluster with logical ID *logical_id*, or None."""
        return next((c for c in self.clusters if c.logical_id == logical_id), None)

    @staticmethod
    def cluster_roles(
        cluster: EdgeHubClusterService, configuration_id: int, segment_id: int
    ) -> EdgeRoles | None:
        """The roles *cluster* has in segment *segment_id* of profile *configuration_id*, or None when its data holds none there.

        A cluster's ``data`` is by configuration ID and then segment ID, and
        a cluster that never was a hub of the profile has no entry for it.
        """
        held = (cluster.data.model_extra or {}).get(str(configuration_id))
        entry = held.get(str(segment_id)) if is_dict(held) else None
        roles = entry.get("roles") if is_dict(entry) else None
        try:
            return EdgeRoles.model_validate(roles) if is_dict(roles) else None
        except ValidationError:
            return None


def _has_configuration(
    service: EdgeHubService,
    configuration_id: int,
    module_id: int,
    segment_object_id: int,
) -> bool:
    """Whether a row of that profile, module and segment points at *service*."""
    held = service.configuration
    return isinstance(held, list) and any(
        c.id_ == configuration_id
        and c.module_id == module_id
        and c.segment_object_id == segment_object_id
        for c in held
    )


async def read_services(
    client: VcoClientProtocol, enterprise_id: int | None, *, qos_usage: bool
) -> HubServices:
    """Read the hub services of the enterprise: two requests, the Edge hubs and then the clusters.

    The Edge hubs come with the ``configuration`` rows that point at them,
    which say the profile, module and segment each is the service for, and
    with ``edgeQOSUsage`` when *qos_usage* (an Edge hub leaves a Branch-to-Hub
    list).
    """
    enterprise = enterprise_id if enterprise_id is not None else MISSING
    with_ = [EnterpriseServicesParamsWith.CONFIGURATION]
    if qos_usage:
        with_.append(EnterpriseServicesParamsWith.EDGE_QOS_USAGE)
    edge_hubs = await client.get_enterprise_services(
        GetEnterpriseServices(
            enterprise_id=enterprise, type_=EnterpriseServiceType.EDGE_HUB, with_=with_
        )
    )
    clusters = await client.get_enterprise_services(
        GetEnterpriseServices(
            enterprise_id=enterprise, type_=EnterpriseServiceType.EDGE_HUB_CLUSTER
        )
    )
    return HubServices(
        tuple(s for s in edge_hubs if isinstance(s, EdgeHubService)),
        tuple(s for s in clusters if isinstance(s, EdgeHubClusterService)),
    )


def in_place(
    step: HubStep,
    services: HubServices,
    *,
    configuration_id: int,
    module_id: int,
) -> bool:
    """Whether the VCO is as *step* would leave it, so the save does not send it.

    An enable is in place when the hub's service has the role in that segment.
    An Edge disable is in place when the Edge has no hub service there (the
    VCO answers ``null``), and a cluster disable when the cluster's role is
    off or its data holds no role for the profile and segment (the VCO fails
    that call).
    """
    field = _ROLE_FIELDS[step.role]
    if step.hub.kind == "edge":
        service = services.edge_service(
            step.hub.logical_id, configuration_id, module_id, step.segment_object_id
        )
        if step.action == "disable":
            return service is None
        return service is not None and getattr(service.data.roles, field) is True
    cluster = services.cluster(step.hub.logical_id)
    roles = (
        None
        if cluster is None
        else services.cluster_roles(cluster, configuration_id, step.segment_id)
    )
    has = roles is not None and getattr(roles, field) is True
    return not has if step.action == "disable" else has


type _RoleRequest = (
    EnableEdgeHub
    | EnableEdgeToEdgeBridge
    | EnableEdgeBackHaul
    | DisableEdgeHub
    | DisableEdgeToEdgeBridge
    | DisableEdgeBackHaul
    | EnableClusterHub
    | EnableClusterToEdgeBridge
    | EnableClusterBackHaul
    | DisableClusterHub
    | DisableClusterToEdgeBridge
    | DisableClusterBackHaul
)

_CALLS: Final[dict[tuple[Action, Role, str], tuple[type[_RoleRequest], str]]] = {
    ("enable", "hub", "edge"): (EnableEdgeHub, "enable_edge_hub"),
    ("enable", "bridge", "edge"): (
        EnableEdgeToEdgeBridge,
        "enable_edge_to_edge_bridge",
    ),
    ("enable", "backhaul", "edge"): (EnableEdgeBackHaul, "enable_edge_backhaul"),
    ("disable", "hub", "edge"): (DisableEdgeHub, "disable_edge_hub"),
    ("disable", "bridge", "edge"): (
        DisableEdgeToEdgeBridge,
        "disable_edge_to_edge_bridge",
    ),
    ("disable", "backhaul", "edge"): (DisableEdgeBackHaul, "disable_edge_backhaul"),
    ("enable", "hub", "cluster"): (EnableClusterHub, "enable_cluster_hub"),
    ("enable", "bridge", "cluster"): (
        EnableClusterToEdgeBridge,
        "enable_cluster_to_edge_bridge",
    ),
    ("enable", "backhaul", "cluster"): (
        EnableClusterBackHaul,
        "enable_cluster_backhaul",
    ),
    ("disable", "hub", "cluster"): (DisableClusterHub, "disable_cluster_hub"),
    ("disable", "bridge", "cluster"): (
        DisableClusterToEdgeBridge,
        "disable_cluster_to_edge_bridge",
    ),
    ("disable", "backhaul", "cluster"): (
        DisableClusterBackHaul,
        "disable_cluster_backhaul",
    ),
}
"""Each role call: its request and the method of the client that sends it, by action, role and kind of hub."""


def _target_id(
    step: HubStep, services: HubServices, *, configuration_id: int, module_id: int
) -> int:
    """The Edge ID or cluster ID the call of *step* takes: the step's for an enable, the one *services* give for a removal."""
    hub = step.hub
    if step.action == "enable":
        given = hub.edge_id if hub.kind == "edge" else hub.cluster_id
        if given is None:
            raise ValueError(
                f"the step '{step.render()}' has no {'Edge' if hub.kind == 'edge' else 'cluster'} "
                "ID to call the VCO with: plan it with a loaded hub resolver"
            )
        return given
    if hub.kind == "cluster":
        cluster = services.cluster(hub.logical_id)
        if cluster is None:
            raise _no_service(step, "cluster")
        return cluster.id_
    service = services.edge_service(
        hub.logical_id, configuration_id, module_id, step.segment_object_id
    )
    if service is None or not isinstance(service.edge_id, int):
        raise _no_service(step, "Edge hub service")
    return service.edge_id


def _no_service(step: HubStep, what: str) -> HubServiceNotFoundError:
    return HubServiceNotFoundError(
        hub=step.hub.name,
        segment=step.segment,
        path=f'deviceSettings: segments["{step.segment}"].vpn',
        message=(
            f"The VCO has no {what} for {step.hub.name!r} in segment "
            f"{step.segment!r} to call for the step '{step.render()}'. The save "
            "does not send a step whose roles are in place (`in_place`), so the "
            "VCO changed between the read and the call. Save again: it reads "
            "the services again."
        ),
    )


async def send_step(
    client: VcoClientProtocol,
    step: HubStep,
    services: HubServices,
    *,
    configuration_id: int,
    module_id: int,
    enterprise_id: int | None,
) -> None:
    """Make the one role call of *step*: a removal's Edge or cluster ID comes from *services*.

    An Edge disable the VCO answers with ``null`` counts as done. Any other
    error of the call propagates.
    """
    request_type, method = _CALLS[step.action, step.role, step.hub.kind]
    request = request_type(
        id_=_target_id(
            step, services, configuration_id=configuration_id, module_id=module_id
        ),
        configuration_id=configuration_id,
        module_id=module_id,
        segment_object_id=step.segment_object_id,
        enterprise_id=enterprise_id if enterprise_id is not None else MISSING,
    )
    send = cast(Callable[[_RoleRequest], Awaitable[object]], getattr(client, method))
    _ = await send(request)


def hubs_in_use(
    steps: Sequence[HubStep],
    services: HubServices,
    *,
    configuration_id: int,
    module_id: int,
) -> dict[HubStep, tuple[tuple[int, str], ...]]:
    """For each disable-hub step of an Edge hub, the ``(edgeId, name)`` of the edges whose QoS rules use the hub's service.

    The hub's service is the one for *configuration_id*, *module_id* and the
    step's segment (``HubServices.edge_service``), read with ``edgeQOSUsage``
    (``read_services(..., qos_usage=True)``). An edge counts once, for any
    number of rules, and only for rows whose ``ref`` is the name
    ``deviceSettings:backHaulEdge``. A step whose hub nothing uses, and one
    whose service the read has no usage for, is not in the result.
    """
    found: dict[HubStep, tuple[tuple[int, str], ...]] = {}
    for step in steps:
        if step.action != "disable" or step.role != "hub" or step.hub.kind != "edge":
            continue
        service = services.edge_service(
            step.hub.logical_id, configuration_id, module_id, step.segment_object_id
        )
        usage = service.edge_qos_usage if service is not None else MISSING
        if not isinstance(usage, list):
            continue
        edges = dict.fromkeys(
            (row.edge_id, row.name or "")
            for row in usage
            if row.ref == BACKHAUL_REF and isinstance(row.edge_id, int)
        )
        if edges:
            found[step] = tuple(edges)
    return found


def report_hubs_in_use(
    in_use: Mapping[HubStep, tuple[tuple[int, str], ...]], *, refuse: bool
) -> list[ValidationWarning]:
    """The ``hub_in_use`` warnings for *in_use*, one per hub and segment; with *refuse*, raise ``HubListError`` for all of them instead."""
    found = [
        (step, edges, hub_in_use_message(step.segment, step.hub.name, edges, refuse))
        for step, edges in in_use.items()
    ]
    if refuse and found:
        raise HubListError(
            "\n".join(message for _, _, message in found),
            segment=found[0][0].segment
            if len({step.segment for step, _, _ in found}) == 1
            else None,
            lists=("vpn.edgeToEdgeHub.vpnHubs",),
            hubs=tuple(dict.fromkeys(step.hub.name for step, _, _ in found)),
            edges=tuple(dict.fromkeys(e for _, edges, _ in found for e in edges)),
        )
    return [
        ValidationWarning(
            path=f'deviceSettings: segments["{step.segment}"].vpn',
            message=message,
            code="hub_in_use",
        )
        for step, _, message in found
    ]


def _hub_ref_name(hub: HubRef) -> str:
    return EDGE_HUB_REF if hub.kind == "edge" else CLUSTER_HUB_REF


def _holds_row(
    raw_refs: object, ref: str, object_id: int, segment_object_id: int
) -> bool:
    return any(
        row.get("enterpriseObjectId") == object_id
        and row.get("segmentObjectId") == segment_object_id
        for row in rows_named(raw_refs, ref)
    )


def _row_of_hub(
    raw_refs: object, step: HubStep, configuration_id: int
) -> dict[str, object] | None:
    """The row of the hub of *step* for its segment in *raw_refs*, as read, or None."""
    ref = _hub_ref_name(step.hub)
    for row in rows_named(raw_refs, ref):
        if row.get("configurationId") != configuration_id:
            continue
        if row.get("segmentObjectId") != step.segment_object_id:
            continue
        held = row.get("data")
        if step.hub.kind == "edge":
            logical_id = held.get("logicalId") if is_dict(held) else None
        else:
            logical_id = row.get("logicalId")
        if str(logical_id).lower() == str(step.hub.logical_id):
            return cast(dict[str, object], row)
    return None


def hub_rows(
    as_read_refs: object,
    steps: Sequence[HubStep],
    services: HubServices,
    *,
    configuration_id: int,
    module_id: int,
    now: DeviceSettingsData,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """The hub rows the steps add to and remove from the refs of the device settings module: ``(added, removed)``.

    *services* are read after the steps were sent, *as_read_refs* are the
    module's refs as read, and *now* the module as edited. A hub added to a
    Branch-to-Hub list has a row of five fields (``configurationId``,
    ``moduleId``, ``enterpriseObjectId`` which is its service's ID,
    ``ref`` and ``segmentObjectId``), unless the refs as read already hold one
    of the same ``(ref, enterpriseObjectId, segmentObjectId)``: the VCO's table
    has no unique constraint, so a second would be stored. A hub removed from
    it loses its row as read. A row of a hub that stays in the list is never
    removed. Raises ``HubServiceNotFoundError`` for a hub the VCO has no
    service for after its enable.
    """
    lists = hub_lists_of(now)
    added: list[dict[str, object]] = []
    removed: list[dict[str, object]] = []
    for step in steps:
        if step.role != "hub":
            continue
        ref = _hub_ref_name(step.hub)
        if step.action == "enable":
            if step.hub.kind == "edge":
                service = services.edge_service(
                    step.hub.logical_id,
                    configuration_id,
                    module_id,
                    step.segment_object_id,
                )
                object_id = None if service is None else service.id_
            else:
                cluster = services.cluster(step.hub.logical_id)
                object_id = None if cluster is None else cluster.id_
            if object_id is None:
                raise _no_service(step, "service")
            if _holds_row(as_read_refs, ref, object_id, step.segment_object_id):
                continue
            row: dict[str, object] = {
                "configurationId": configuration_id,
                "moduleId": module_id,
                "enterpriseObjectId": object_id,
                "ref": ref,
                "segmentObjectId": step.segment_object_id,
            }
            if row not in added:
                added.append(row)
            continue
        stays = any(
            h.logical_id == step.hub.logical_id
            for h in lists.get(step.segment_id, {}).get("hub", [])
        )
        gone = None if stays else _row_of_hub(as_read_refs, step, configuration_id)
        if gone is not None and gone not in removed:
            removed.append(gone)
    return added, removed
