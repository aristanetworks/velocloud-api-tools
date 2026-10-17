"""Dynamic VCO entity discovery for integration tests.

Resolves enterprise, edge, link, segment, gateway, and SD-WAN peer IDs
by querying a real VCO at test session startup.  All IDs are discovered
dynamically — nothing is hardcoded.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic_core import MISSING

if TYPE_CHECKING:
    from pyvelo.client import VcoClient

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LinkInfo:
    id: int
    edge_id: int
    logical_id: str
    interface: str
    internal_id: str
    display_name: str


@dataclass(frozen=True)
class EdgeInfo:
    id: int
    logical_id: str
    name: str
    edge_state: str
    ha_state: str | None
    site_name: str | None
    links: list[LinkInfo]


@dataclass(frozen=True)
class GatewayInfo:
    id: int
    logical_id: str
    name: str


@dataclass(frozen=True)
class SegmentInfo:
    segment_id: int
    logical_id: str
    name: str


@dataclass(frozen=True)
class SdwanPeerInfo:
    edge_id: int
    peer_logical_id: str
    peer_type: str
    src_link_internal_id: str = ""
    dest_link_internal_id: str = ""


@dataclass(frozen=True)
class VcoTestContext:
    enterprise_id: int
    edges: list[EdgeInfo]
    gateways: list[GatewayInfo]
    segments: list[SegmentInfo]
    sdwan_gateway_peer: SdwanPeerInfo | None = None
    sdwan_edge_peer: SdwanPeerInfo | None = None

    @property
    def first_edge(self) -> EdgeInfo:
        return self.edges[0]

    @property
    def first_link(self) -> LinkInfo:
        return self.first_edge.links[0]

    @property
    def first_gateway(self) -> GatewayInfo:
        return self.gateways[0]


def _enum_str(val: Any) -> str | None:
    if val is None or val is MISSING:
        return None
    return val.value if hasattr(val, "value") else str(val)


async def _resolve_sdwan_peers(
    client: VcoClient, edge_id: int, enterprise_id: int
) -> tuple[SdwanPeerInfo | None, SdwanPeerInfo | None]:
    from pyvelo.client.enums.edge import SdwanPeerType
    from pyvelo.client.enums.metrics import SdwanPeerPathMetric
    from pyvelo.client.schemas.edge import GetEdgeSdwanPeers
    from pyvelo.client.schemas.metrics import GetEdgeSdwanPeerPathMetrics
    from pyvelo.core import ApiInterval

    now_ms = int(time.time() * 1000)
    interval = ApiInterval.model_validate(
        {"start": now_ms - 24 * 86_400_000, "end": now_ms}
    )

    try:
        peers = await client.get_edge_sdwan_peers(
            GetEdgeSdwanPeers(
                edge_id=edge_id, enterprise_id=enterprise_id, interval=interval
            )
        )
    except Exception:
        log.debug("SD-WAN peer discovery failed", exc_info=True)
        return None, None

    async def _resolve_paths(peer_logical_id: str) -> tuple[str, str]:
        try:
            path_items = await client.get_edge_sdwan_peer_path_metrics(
                GetEdgeSdwanPeerPathMetrics(
                    edge_id=edge_id,
                    enterprise_id=enterprise_id,
                    peer_logical_id=UUID(peer_logical_id),
                    interval=interval,
                    metrics=[SdwanPeerPathMetric.BYTES_RX],
                )
            )
            if path_items:
                src = path_items[0].source.link_logical_id
                dst = path_items[0].destination.link_logical_id
                return (
                    str(src) if src and src is not MISSING else "",
                    str(dst) if dst and dst is not MISSING else "",
                )
        except Exception:
            log.debug(
                "Path resolution failed for peer %s", peer_logical_id, exc_info=True
            )
        return ("", "")

    gw_peer: SdwanPeerInfo | None = None
    edge_peer: SdwanPeerInfo | None = None
    for peer in peers:
        peer_logical_id = str(peer.device_logical_id)
        peer_type = str(peer.peer_type)
        is_gateway = peer.peer_type == SdwanPeerType.GATEWAY
        if is_gateway and gw_peer is None:
            src, dst = await _resolve_paths(peer_logical_id)
            gw_peer = SdwanPeerInfo(
                edge_id=edge_id,
                peer_logical_id=peer_logical_id,
                peer_type=peer_type,
                src_link_internal_id=src,
                dest_link_internal_id=dst,
            )
        elif not is_gateway and edge_peer is None:
            src, dst = await _resolve_paths(peer_logical_id)
            edge_peer = SdwanPeerInfo(
                edge_id=edge_id,
                peer_logical_id=peer_logical_id,
                peer_type=peer_type,
                src_link_internal_id=src,
                dest_link_internal_id=dst,
            )
        if gw_peer and edge_peer:
            break

    return gw_peer, edge_peer


async def discover(client: VcoClient, enterprise_id: int) -> VcoTestContext:
    """Query a real VCO and build a VcoTestContext with all discovered entities."""
    from pyvelo.client.enums.edge import EdgeState, GetEdgesWith
    from pyvelo.client.schemas.enterprise import (
        GetEnterpriseEdges,
        GetEnterpriseNetworkSegments,
    )
    from pyvelo.client.schemas.network import GetNetworkGateways

    # Edges with site + links
    raw_edges = await client.get_enterprise_edges(
        GetEnterpriseEdges(
            enterprise_id=enterprise_id,
            with_=[GetEdgesWith.SITE, GetEdgesWith.LINKS],
        )
    )

    edges: list[EdgeInfo] = []
    for e in raw_edges:
        edge_id = e.id_ if (e.id_ is not None and e.id_ is not MISSING) else 0

        links: list[LinkInfo] = []
        if e.links and e.links is not MISSING:
            for lnk in e.links:
                lnk_id = (
                    lnk.id_ if (lnk.id_ is not None and lnk.id_ is not MISSING) else 0
                )
                links.append(
                    LinkInfo(
                        id=lnk_id,
                        edge_id=edge_id,
                        logical_id=(
                            str(lnk.logical_id)
                            if (lnk.logical_id and lnk.logical_id is not MISSING)
                            else ""
                        ),
                        interface=(
                            lnk.interface
                            if (lnk.interface and lnk.interface is not MISSING)
                            else ""
                        ),
                        internal_id=(
                            str(lnk.internal_id)
                            if (lnk.internal_id and lnk.internal_id is not MISSING)
                            else ""
                        ),
                        display_name=(
                            (lnk.display_name or "")
                            if (lnk.display_name is not MISSING)
                            else ""
                        ),
                    )
                )

        edges.append(
            EdgeInfo(
                id=edge_id,
                logical_id=(
                    str(e.logical_id)
                    if (e.logical_id and e.logical_id is not MISSING)
                    else ""
                ),
                name=e.name if (e.name and e.name is not MISSING) else "",
                edge_state=_enum_str(e.edge_state) or "UNKNOWN",
                ha_state=_enum_str(e.ha_state),
                site_name=(
                    e.site.name
                    if (
                        e.site is not None
                        and e.site is not MISSING
                        and e.site.name is not MISSING
                        and e.site.name
                    )
                    else None
                ),
                links=links,
            )
        )

    # Gateways
    gateways: list[GatewayInfo] = []
    try:
        raw_gateways = await client.get_network_gateways(GetNetworkGateways())
        for gw in raw_gateways:
            gw_id = gw.id_ if (gw.id_ is not None and gw.id_ is not MISSING) else 0
            gateways.append(
                GatewayInfo(
                    id=gw_id,
                    logical_id=(
                        str(gw.logical_id)
                        if (gw.logical_id and gw.logical_id is not MISSING)
                        else ""
                    ),
                    name=gw.name if (gw.name and gw.name is not MISSING) else "",
                )
            )
    except Exception:
        log.debug("Gateway discovery failed", exc_info=True)

    # Segments
    segments: list[SegmentInfo] = []
    try:
        raw_segments = await client.get_enterprise_network_segments(
            GetEnterpriseNetworkSegments(enterprise_id=enterprise_id)
        )
        for s in raw_segments:
            if s.data is not None:
                segments.append(
                    SegmentInfo(
                        segment_id=s.data.segment_id,
                        logical_id=str(s.logical_id),
                        name=s.name,
                    )
                )
    except Exception:
        log.debug("Segment discovery failed", exc_info=True)

    # SD-WAN peers (from first connected edge)
    connected = [e for e in edges if e.edge_state == EdgeState.CONNECTED.value]
    gw_peer: SdwanPeerInfo | None = None
    edge_peer: SdwanPeerInfo | None = None
    if connected:
        gw_peer, edge_peer = await _resolve_sdwan_peers(
            client, connected[0].id, enterprise_id
        )

    log.info(
        "Discovered %d edges (%d connected), %d gateways, %d segments",
        len(edges),
        len(connected),
        len(gateways),
        len(segments),
    )

    return VcoTestContext(
        enterprise_id=enterprise_id,
        edges=edges,
        gateways=gateways,
        segments=segments,
        sdwan_gateway_peer=gw_peer,
        sdwan_edge_peer=edge_peer,
    )
