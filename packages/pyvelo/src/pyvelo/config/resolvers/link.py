"""LinkResolver — resolves link identifiers to LinkIdentity via VCO API."""

from typing import override

from pydantic import MISSING

from pyvelo.client.enums.edge import GetEdgeWith
from pyvelo.client.schemas.edge import GetEdge
from pyvelo.config.resolver import BaseResolver, ResolverContext
from pyvelo.core.identity import (
    LinkId,
    LinkIdentity,
    LinkInternalId,
    LinkLogicalId,
    LinkName,
)

LinkKey = LinkName | LinkId | LinkLogicalId | LinkInternalId


class LinkResolver(BaseResolver[LinkKey, LinkIdentity]):
    """Resolves a link identifier to a :class:`LinkIdentity`.

    Accepts:
        - :class:`LinkName`: human-readable link name (case-insensitive, fuzzy).
        - :class:`LinkId`: numeric VCO link ID.
        - :class:`LinkLogicalId`: string logical ID.
        - :class:`LinkInternalId`: UUID internal ID (used by path-stats APIs).

    Loads links for a single edge via ``edge/getEdge`` with
    ``recentLinks`` when ``load()`` is called, scoped to the edge from
    ResolverContext.
    """

    identity_type = LinkIdentity

    @override
    async def resolve_key(self, key: LinkKey) -> LinkIdentity:
        match key:
            case LinkName(value=name):
                return await self.resolve(name)
            case LinkId(value=v):
                name = self._reverse["id"][v]
                return self._by_name[name]
            case LinkLogicalId(value=v):
                name = self._reverse["logical_id"][v]
                return self._by_name[name]
            case LinkInternalId(value=v):
                name = self._reverse["internal_id"][v]
                return self._by_name[name]
            case _:
                raise TypeError(f"Unsupported key type: {type(key).__name__}")

    @override
    async def load(self, context: ResolverContext) -> None:
        """Fetch links for an edge and populate lookup tables."""
        if context.edge_id is None:
            raise ValueError("LinkResolver requires edge_id in ResolverContext")

        request = GetEdge(
            id_=context.edge_id,
            with_=[GetEdgeWith.RECENT_LINKS],
        )
        if context.enterprise_id is not None:
            request.enterprise_id = context.enterprise_id

        edge = await self._client.get_edge(request)

        self._clear()

        recent_links = edge.recent_links
        if not recent_links or recent_links is MISSING:
            return
        for link in recent_links:
            name = (
                link.display_name
                if link.display_name and link.display_name is not MISSING
                else None
            )
            if not name:
                name = link.name if link.name and link.name is not MISSING else None
            if not name or link.id_ is None or link.id_ is MISSING:
                continue
            logical_id = link.logical_id if link.logical_id is not MISSING else None
            internal_id = link.internal_id if link.internal_id is not MISSING else None
            if logical_id is None or internal_id is None:
                continue
            identity = LinkIdentity(
                name=name,
                id=link.id_,
                logical_id=logical_id,
                internal_id=internal_id,
                edge_id=context.edge_id,
            )
            self._register(
                name,
                identity,
                id=link.id_,
                logical_id=logical_id,
                internal_id=internal_id,
            )
