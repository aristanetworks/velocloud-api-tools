from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from pyvelo.client.operations._tagging import CONFIG, EDGE, SYSTEM, domain_tag
from pyvelo.client.schemas.gateway import (
    GetGatewayEdgeAssignments,
    GetGatewayEdgeAssignmentsResultItem,
    GetGatewayProperties,
    GetGatewayPropertiesResultItem,
)

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class GatewayMixin(Protocol):
    """Operations for gateway/ and gatewayProperty/ API endpoints."""

    @domain_tag(EDGE, CONFIG)
    async def get_gateway_edge_assignments(
        self: VcoClientBaseProtocol, request: GetGatewayEdgeAssignments
    ) -> Sequence[GetGatewayEdgeAssignmentsResultItem]:
        """Get all edge assignments for a specific VeloCloud gateway.

        Returns the list of edges that are assigned to the specified gateway, including their
        assignment type (primary, secondary, super). In the VeloCloud architecture, each edge
        is assigned to one or more gateways that provide cloud routing, traffic optimization,
        and Non-VeloCloud Site connectivity. This method shows which edges depend on a particular
        gateway. Useful for understanding gateway load, planning gateway maintenance windows,
        and assessing the impact of a gateway outage.

        Returns:
            Array of edge assignment objects with edge identity, enterprise, assignment type
            (primary/secondary/super), and segment information
        """
        return await self.portal_list(
            "gateway/getGatewayEdgeAssignments",
            request,
            GetGatewayEdgeAssignmentsResultItem,
        )

    @domain_tag(SYSTEM)
    async def get_gateway_properties(
        self: VcoClientBaseProtocol, request: GetGatewayProperties
    ) -> Sequence[GetGatewayPropertiesResultItem]:
        """Get all properties for a specific VeloCloud gateway.

        Returns the set of key-value properties configured for the specified gateway. Gateway
        properties store operational settings, feature flags, and configuration metadata for
        the gateway node. Properties can be queried by gatewayId, name, or value. Useful for
        inspecting gateway-specific settings and understanding operational parameters.

        Returns:
            Array of gateway property objects with name, value, dataType, and metadata
        """
        return await self.portal_list(
            "gatewayProperty/getGatewayProperties",
            request,
            GetGatewayPropertiesResultItem,
        )
