"""Generic VCO API execution mixin.

A typed-bypass escape hatch: ``execute_vco_api`` calls any portal method by
path with an arbitrary body. Use the typed operation mixins where possible;
this exists for endpoints not covered by a schema.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from pyvelo.client.schemas.generic import GenericRequest, GenericResult

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class GenericMixin(Protocol):
    """Generic VCO API execution for arbitrary endpoints."""

    async def execute_vco_api(
        self: VcoClientBaseProtocol, api_path: str, body: dict[str, Any]
    ) -> GenericResult:
        """Execute a generic VCO API call for any operation not covered by specific tools.

        Common API paths include:
        - /edge/getEdge - Get edge details
        - /edge/getEdgeConfigurationStack - Get edge configuration
        - /enterprise/getEnterpriseEdges - List edges
        - /enterprise/getEnterpriseUsers - List users
        - /metrics/getEdgeLinkMetrics - Get link metrics
        - /event/getEnterpriseEvents - Get events
        - /firewall/getEnterpriseFirewallLogs - Get firewall logs

        Returns:
            API response
        """
        method_name = api_path.lstrip("/")
        request = GenericRequest.model_validate(body)
        return await self.portal(method_name, request, GenericResult)
