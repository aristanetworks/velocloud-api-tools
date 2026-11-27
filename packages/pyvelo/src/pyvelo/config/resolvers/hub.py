"""HubResolver: resolves an Edge or a hub cluster of an enterprise.

It resolves one by name or logical ID.
"""

import uuid
from typing import ClassVar, Self, override

from pydantic import MISSING

from pyvelo.client.enums.enterprise import EnterpriseServiceType
from pyvelo.client.protocol import VcoClientProtocol
from pyvelo.client.schemas.enterprise import (
    EdgeHubClusterMemberService,
    EdgeHubClusterService,
    GetEnterpriseEdgeList,
    GetEnterpriseServices,
)
from pyvelo.config.resolver import BaseResolver, ResolverContext
from pyvelo.core.identity import (
    HubIdentity,
    HubLogicalId,
    HubName,
    IdentityKey,
    ResolutionError,
    resolution_error,
)


class HubResolver(BaseResolver[HubIdentity]):
    """Resolves an Edge or a hub cluster to a :class:`HubIdentity`.

    Accepts:
        - :class:`HubName`: the Edge's or the cluster's name.
        - :class:`HubLogicalId`: the Edge's or the cluster's logical ID.

    ``ResolverRegistry.lookup_by`` finds an identity by any of its fields,
    an ``edge_id`` or a ``cluster_id`` among them.

    Every Edge of an enterprise can be a hub, and so can a cluster, so the
    identities are all of them: an Edge that is no member of a cluster is an
    ``edge`` identity, a cluster a ``cluster`` identity. A member of a cluster
    is not a hub of its own (the cluster is), so it is no identity;
    ``is_cluster_member`` and ``member_name`` answer for the members.

    Nothing is found until ``load()`` has read the enterprise, scoped to the
    enterprise of the :class:`ResolverContext`; before that the lookups raise
    ``ResolverNotLoadedError``. A load makes three requests: the Edge list
    (``enterprise/getEnterpriseEdgeList``, read at 200 per page, until it is
    all read), then the hub clusters and the cluster members
    (``enterprise/getEnterpriseServices``, one request for each type). It is
    done once per state; a name looked up afterwards costs no request.

    An Edge and a cluster may share a name, so a name that several hubs hold
    is refused with a message that lists them and asks for the logical ID,
    which every hub has alone. ``BaseResolver`` keeps one identity per name,
    so this resolver also keeps every hub it loaded, and ``identities()``
    returns them all, shared names included.
    """

    identity_type: type[HubIdentity] = HubIdentity
    requires_load: ClassVar[bool] = True

    def __init__(self, client: VcoClientProtocol) -> None:
        """An unloaded resolver that reads the Edges and clusters through *client*."""
        super().__init__(client)
        self._hubs: list[HubIdentity] = []
        self._members: dict[uuid.UUID, str] = {}

    @override
    def lookup(self, name: str) -> HubIdentity:
        self._require_loaded()
        named = [h for h in self._hubs if h.name == name]
        if not named:
            named = [h for h in self._hubs if h.name.lower() == name.lower()]
        if len(named) == 1:
            return named[0]
        if not named:
            member = next(
                (
                    logical_id
                    for logical_id, member_name in self._members.items()
                    if member_name.lower() == name.lower()
                ),
                None,
            )
            if member is not None:
                raise ResolutionError(
                    name=name,
                    identity_type=HubIdentity,
                    candidates=[],
                    message=(
                        f"{name!r} is a member of a hub cluster ({member}), so it "
                        "cannot be a hub of its own: the VCO makes the cluster the "
                        "hub, not its members. Give the name or the logical ID of "
                        "the cluster instead."
                    ),
                )
            raise resolution_error(HubIdentity, name, self._by_name.keys())
        listed = ", ".join(f"a {_kind_label(h)} ({h.logical_id})" for h in named)
        raise ResolutionError(
            name=name,
            identity_type=HubIdentity,
            candidates=[str(h.logical_id) for h in named],
            message=(
                f"Several hubs are named {name!r}: {listed}. "
                "Give the logical ID of the one you mean instead of its name, "
                'as in {"_op": "add", "logical_id": "<logical ID>"}.'
            ),
        )

    @override
    def lookup_key(self, key: IdentityKey[HubIdentity]) -> HubIdentity:
        self._require_loaded()
        match key:
            case HubName(value=name):
                return self.lookup(name)
            case HubLogicalId(value=logical_id):
                for hub in self._hubs:
                    if hub.logical_id == logical_id:
                        return hub
                raise ResolutionError(
                    name=str(logical_id),
                    identity_type=HubIdentity,
                    candidates=[],
                    message=(
                        f"No Edge or hub cluster of the enterprise has the logical "
                        f"ID {logical_id}"
                        + (
                            ", which is that of a member of a hub cluster: give "
                            "the cluster's"
                            if logical_id in self._members
                            else ""
                        )
                    ),
                )
            case _:
                raise TypeError(f"Unsupported key type: {type(key).__name__}")

    @override
    async def _load(self, context: ResolverContext) -> None:
        """Read the hubs of the enterprise, and populate the lookup tables.

        It reads the Edges, the clusters and their members.
        """
        edge_request = GetEnterpriseEdgeList()
        cluster_request = GetEnterpriseServices(
            type_=EnterpriseServiceType.EDGE_HUB_CLUSTER
        )
        member_request = GetEnterpriseServices(
            type_=EnterpriseServiceType.EDGE_HUB_CLUSTER_MEMBER
        )
        if context.enterprise_id is not None:
            edge_request.enterprise_id = context.enterprise_id
            cluster_request.enterprise_id = context.enterprise_id
            member_request.enterprise_id = context.enterprise_id

        edges = await self._client.get_enterprise_edge_list(edge_request)
        clusters = await self._client.get_enterprise_services(cluster_request)
        members = await self._client.get_enterprise_services(member_request)

        member_edge_ids = {
            m.edge_id
            for m in members
            if isinstance(m, EdgeHubClusterMemberService)
            and m.edge_id is not MISSING
            and m.edge_id is not None
        }
        member_logical_ids = {
            m.data.logical_id: m.name
            for m in members
            if isinstance(m, EdgeHubClusterMemberService)
        }

        self._clear()
        for edge in edges:
            edge_id, logical_id, name = edge.id_, edge.logical_id, edge.name
            if edge_id is None or edge_id is MISSING:
                continue
            if logical_id is None or logical_id is MISSING:
                continue
            if name is None or name is MISSING:
                continue
            if edge_id in member_edge_ids or logical_id in member_logical_ids:
                self._members[logical_id] = member_logical_ids.get(logical_id, name)
                continue
            self._add(
                HubIdentity(
                    name=name,
                    kind="edge",
                    type_="edgeHub",
                    logical_id=logical_id,
                    edge_id=edge_id,
                    edge_logical_id=logical_id,
                    cluster_id=None,
                    cluster_logical_id=None,
                )
            )
        for cluster in clusters:
            if not isinstance(cluster, EdgeHubClusterService):
                continue
            self._add(
                HubIdentity(
                    name=cluster.name,
                    kind="cluster",
                    type_="edgeHubCluster",
                    logical_id=cluster.logical_id,
                    edge_id=None,
                    edge_logical_id=None,
                    cluster_id=cluster.id_,
                    cluster_logical_id=cluster.logical_id,
                )
            )

    @override
    def identities(self) -> list[HubIdentity]:
        """Every hub loaded so far, also those that share a name."""
        return list(self._hubs)

    def is_cluster_member(self, logical_id: uuid.UUID) -> bool:
        """True if the Edge with *logical_id* is a member of a hub cluster.

        Such an Edge is no hub of its own.
        """
        self._require_loaded()
        return logical_id in self._members

    def member_name(self, logical_id: uuid.UUID) -> str | None:
        """The name of the cluster member Edge with *logical_id*.

        None if it is no member.
        """
        self._require_loaded()
        return self._members.get(logical_id)

    @override
    def unloaded(self) -> Self:
        twin = super().unloaded()
        twin._hubs = []
        twin._members = {}
        return twin

    @override
    def _clear(self) -> None:
        super()._clear()
        self._hubs.clear()
        self._members.clear()

    def _add(self, hub: HubIdentity) -> None:
        self._hubs.append(hub)
        reverse_keys: dict[str, int | str | uuid.UUID] = {"logical_id": hub.logical_id}
        if hub.edge_id is not None:
            reverse_keys["edge_id"] = hub.edge_id
        if hub.cluster_id is not None:
            reverse_keys["cluster_id"] = hub.cluster_id
        self._register(hub.name, hub, **reverse_keys)


def _kind_label(hub: HubIdentity) -> str:
    return "hub cluster" if hub.kind == "cluster" else "Edge"
