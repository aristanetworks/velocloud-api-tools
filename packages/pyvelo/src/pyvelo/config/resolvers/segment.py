"""SegmentResolver — resolves segment names to SegmentIdentity via VCO API."""

from typing import override

from pyvelo.client.schemas.enterprise import GetEnterpriseNetworkSegments
from pyvelo.config.resolver import BaseResolver, ResolverContext
from pyvelo.core.identity import (
    SegmentId,
    SegmentIdentity,
    SegmentLogicalId,
    SegmentName,
    SegmentObjectId,
)

SegmentKey = SegmentName | SegmentId | SegmentObjectId | SegmentLogicalId


class SegmentResolver(BaseResolver[SegmentKey, SegmentIdentity]):
    """Resolves a segment identifier to a :class:`SegmentIdentity`.

    Accepts:
        - :class:`SegmentName`: human-readable segment name.
        - :class:`SegmentId`: numeric segment ID (the segment_id field).
        - :class:`SegmentObjectId`: numeric VCO object ID (the id_ field).
        - :class:`SegmentLogicalId`: UUID logical ID.

    Loads the full segment list via ``enterprise/getEnterpriseNetworkSegments``
    when ``load()`` is called, scoped to the enterprise from ResolverContext.
    """

    identity_type = SegmentIdentity

    @override
    async def resolve_key(self, key: SegmentKey) -> SegmentIdentity:
        match key:
            case SegmentName(value=name):
                return await self.resolve(name)
            case SegmentId(value=v):
                name = self._reverse["segment_id"][v]
                return self._by_name[name]
            case SegmentObjectId(value=v):
                name = self._reverse["object_id"][v]
                return self._by_name[name]
            case SegmentLogicalId(value=v):
                name = self._reverse["logical_id"][v]
                return self._by_name[name]
            case _:
                raise TypeError(f"Unsupported key type: {type(key).__name__}")

    @override
    async def load(self, context: ResolverContext) -> None:
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
                type_=seg.type_,
            )
            self._register(
                name,
                identity,
                segment_id=seg_id,
                object_id=obj_id,
                logical_id=logical_id,
            )
