"""SegmentResolver — resolves segment names to SegmentIdentity via VCO API."""

from typing import override

from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.schemas.enterprise import GetEnterpriseNetworkSegments
from pyvelo.config.resolver import BaseResolver, ResolverContext
from pyvelo.core.identity import (
    IdentityKey,
    SegmentId,
    SegmentIdentity,
    SegmentLogicalId,
    SegmentName,
    SegmentObjectId,
)


class SegmentResolver(BaseResolver[SegmentIdentity]):
    """Resolves a segment identifier to a :class:`SegmentIdentity`.

    Accepts:
        - :class:`SegmentName`: human-readable segment name.
        - :class:`SegmentId`: numeric segment ID (the segment_id field).
        - :class:`SegmentObjectId`: numeric VCO object ID (the id_ field).
        - :class:`SegmentLogicalId`: UUID logical ID.

    Loads the full segment list via ``enterprise/getEnterpriseNetworkSegments``
    when ``load()`` is called, scoped to the enterprise from ResolverContext.
    """

    identity_type: type[SegmentIdentity] = SegmentIdentity

    @override
    def lookup_key(self, key: IdentityKey[SegmentIdentity]) -> SegmentIdentity:
        match key:
            case SegmentName(value=name):
                return self.lookup(name)
            case SegmentId(value=v):
                return self._by_key("segment_id", v)
            case SegmentObjectId(value=v):
                return self._by_key("object_id", v)
            case SegmentLogicalId(value=v):
                return self._by_key("logical_id", v)
            case _:
                raise TypeError(f"Unsupported key type: {type(key).__name__}")

    @override
    async def _load(self, context: ResolverContext) -> None:
        """Fetch all segments for the enterprise and populate lookup tables."""
        request = GetEnterpriseNetworkSegments()
        if context.enterprise_id is not None:
            request.enterprise_id = context.enterprise_id

        segments = await self._client.get_enterprise_network_segments(request)

        self._clear()

        for seg in segments:
            name = seg.name
            seg_id = seg.data.segment_id
            obj_id = seg.id_
            logical_id = seg.logical_id

            identity = SegmentIdentity(
                name=name,
                segment_id=seg_id,
                object_id=obj_id,
                logical_id=logical_id,
                type_=NetworkSegmentType(seg.type_),
            )
            self._register(
                name,
                identity,
                segment_id=seg_id,
                object_id=obj_id,
                logical_id=logical_id,
            )
