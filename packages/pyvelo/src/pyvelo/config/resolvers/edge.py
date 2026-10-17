"""EdgeResolver — resolves edge names to EdgeIdentity via VCO API."""

from typing import override

from pydantic_core import MISSING

from pyvelo.client.enums.edge import device_family_for_model
from pyvelo.client.schemas.enterprise import GetEnterpriseEdges
from pyvelo.config.resolver import BaseResolver, ResolverContext
from pyvelo.core.identity import (
    DeviceFamily,
    EdgeId,
    EdgeIdentity,
    EdgeLogicalId,
    EdgeName,
)

EdgeKey = EdgeName | EdgeId | EdgeLogicalId


class EdgeResolver(BaseResolver[EdgeKey, EdgeIdentity]):
    """Resolves an edge identifier to an :class:`EdgeIdentity`.

    Accepts:
        - :class:`EdgeName`: human-readable edge name (case-insensitive, fuzzy).
        - :class:`EdgeId`: numeric VCO edge ID.
        - :class:`EdgeLogicalId`: UUID logical ID.

    Loads the full edge list via ``enterprise/getEnterpriseEdges`` when
    ``load()`` is called, scoped to the enterprise from ResolverContext.
    """

    identity_type = EdgeIdentity

    @override
    async def resolve_key(self, key: EdgeKey) -> EdgeIdentity:
        match key:
            case EdgeName(value=name):
                return await self.resolve(name)
            case EdgeId(value=v):
                name = self._reverse["id"][v]
                return self._by_name[name]
            case EdgeLogicalId(value=v):
                name = self._reverse["logical_id"][v]
                return self._by_name[name]
            case _:
                raise TypeError(f"Unsupported key type: {type(key).__name__}")

    @override
    async def load(self, context: ResolverContext) -> None:
        """Fetch all edges for the enterprise and populate lookup tables."""
        request = GetEnterpriseEdges()
        if context.enterprise_id is not None:
            request.enterprise_id = context.enterprise_id

        edges = await self._client.get_enterprise_edges(request)

        self._clear()

        for edge in edges:
            name = edge.name
            if name is None or name is MISSING:
                continue
            edge_id = edge.id_
            logical_id = edge.logical_id
            if (
                edge_id is None
                or edge_id is MISSING
                or logical_id is None
                or logical_id is MISSING
            ):
                continue
            raw_model = getattr(edge, "model_number", None)
            model_number = raw_model if raw_model is not MISSING else None

            device_family = (
                DeviceFamily(device_family_for_model(model_number))
                if model_number is not None
                else None
            )
            identity = EdgeIdentity(
                name=name,
                id=edge_id,
                logical_id=logical_id,
                model_number=model_number,
                device_family=device_family,
            )
            self._register(name, identity, id=edge_id, logical_id=logical_id)
