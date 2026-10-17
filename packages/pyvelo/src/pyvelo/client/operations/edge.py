from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from pydantic import RootModel

from pyvelo.client.exceptions import VcoOperationError
from pyvelo.client.operations._tagging import CONFIG, EDGE, PM_QOE, domain_tag
from pyvelo.client.schemas.configuration import (
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
)
from pyvelo.client.schemas.edge import (
    ClientVisibilityModeResult,
    CreateEdge,
    CreateEdgeResult,
    DeleteEdge,
    DisableEdgeBackHaul,
    DisableEdgeHub,
    DisableEdgeToEdgeBridge,
    EdgeCertificate,
    EdgeConfigurationModulesResult,
    EdgeGatewayAssignmentResult,
    EdgeSdwanPeerEntry,
    EnableEdgeBackHaul,
    EnableEdgeHub,
    EnableEdgeToEdgeBridge,
    GetClientVisibilityMode,
    GetEdge,
    GetEdgeCertificates,
    GetEdgeConfigurationModules,
    GetEdgeConfigurationStack,
    GetEdgeGatewayAssignmentsView,
    GetEdgeResult,
    GetEdgeSdwanPeers,
    GetHaInterfaces,
    GetInterconnectGateways,
    GetSecureDeviceSecretsOption,
    HaInterfaceItem,
    InterconnectGatewayItem,
    RequestEdgeHaFailover,
    SecureDeviceSecretsOptionResult,
)
from pyvelo.client.schemas.enterprise import EnterpriseServiceBase
from pyvelo.core import RowsModifiedConfirmation

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol

GetEdgeConfigurationStackResult = tuple[
    EdgeConfiguration, EnterpriseConfigurationProfile
]

GetEdgeConfigurationStackResultModel = RootModel[GetEdgeConfigurationStackResult]


class EdgeMixin(Protocol):
    """Operations for edge/ API endpoints."""

    @domain_tag(EDGE)
    async def create_edge(
        self: VcoClientBaseProtocol, request: CreateEdge
    ) -> CreateEdgeResult:
        """Provision a new edge and return its ID and activation key."""
        return await self.portal("edge/edgeProvision", request, CreateEdgeResult)

    @domain_tag(EDGE)
    async def delete_edge(self: VcoClientBaseProtocol, request: DeleteEdge) -> None:
        """Delete an edge from the enterprise."""
        result = await self.portal("edge/deleteEdge", request, RowsModifiedConfirmation)
        if result.error:
            raise VcoOperationError("delete edge", result.error)

    @domain_tag(EDGE, CONFIG)
    async def get_edge_configuration_stack(
        self: VcoClientBaseProtocol, request: GetEdgeConfigurationStack
    ) -> GetEdgeConfigurationStackResult:
        """
        Get the full configuration stack for an edge.

        Returns the complete configuration profile (including all modules) of the specified
        Edge. The stack includes edge-specific overrides and the assigned enterprise profile.
        Each layer contains modules such as deviceSettings,
        QOS, firewall, and controlPlane. Useful for understanding the effective configuration
        an Edge receives and for diagnosing configuration conflicts across layers.

        Returns:
            Array of configuration profiles in stack order (edge-specific, enterprise profile),
            each containing their associated configuration modules
        """
        result = await self.portal(
            "edge/getEdgeConfigurationStack",
            request,
            GetEdgeConfigurationStackResultModel,
        )
        return result.root

    @domain_tag(EDGE, PM_QOE)
    async def get_edge_sdwan_peers(
        self: VcoClientBaseProtocol, request: GetEdgeSdwanPeers
    ) -> Sequence[EdgeSdwanPeerEntry]:
        """
        Get SD-WAN peers that have established overlay paths with an edge.

        Returns the list of SD-WAN peers (other edges and gateways) that have active VCMP
        (VeloCloud Multi-Path Protocol) tunnels with the specified edge. Optionally supports
        filtering and time interval parameters. Useful for understanding the overlay mesh topology,
        verifying peer-to-peer connectivity, and troubleshooting path establishment issues in the
        SD-WAN fabric.

        Returns:
            Array of SD-WAN peer objects, each containing peer edge/gateway identity, path count,
            path state, and associated link information
        """
        return await self.portal_list(
            "edge/getEdgeSDWANPeers", request, EdgeSdwanPeerEntry
        )

    @domain_tag(EDGE)
    async def get_edge(self: VcoClientBaseProtocol, request: GetEdge) -> GetEdgeResult:
        """Get comprehensive details for a specific SD-WAN edge.

        Retrieves the specified Edge with optional related entities. Supports queries by Edge ID
        and can include site location data, WAN link details, configuration profiles, and recent
        link activity. The edge object contains core attributes such as name, model, software version,
        activation state, HA mode, serial number, and current service state (CONNECTED, DEGRADED, etc.).
        This is the primary method for inspecting a single edge's full state.

        Returns:
            Edge object with core attributes (name, model, softwareVersion, edgeState, serviceState,
            haState, serialNumber, etc.) plus optionally resolved site, links, and configuration data
        """
        return await self.portal("edge/getEdge", request, GetEdgeResult)

    @domain_tag(EDGE, CONFIG)
    async def get_edge_gateway_assignment_view(
        self: VcoClientBaseProtocol, request: GetEdgeGatewayAssignmentsView
    ) -> EdgeGatewayAssignmentResult:
        """Get the gateway assignments view for an edge.

        Returns the complete gateway assignment information for the specified edge, including
        primary, secondary, and super gateway assignments across all segments. In the VeloCloud
        SD-WAN architecture, edges connect to gateways for cloud-based traffic routing, multi-path
        optimization, and access to Non-VeloCloud Sites (NVS). This view shows which gateways are
        assigned to the edge and their roles. Useful for understanding an edge's gateway topology,
        troubleshooting gateway connectivity, and verifying gateway redundancy.

        Returns:
            Gateway assignment details including primary, secondary, and super gateway assignments
            with gateway names, IPs, roles, and per-segment assignment information
        """
        return await self.portal(
            "edge/getEdgeGatewayAssignmentsView",
            request,
            EdgeGatewayAssignmentResult,
        )

    @domain_tag(EDGE, CONFIG)
    async def get_edge_configuration_modules(
        self: VcoClientBaseProtocol, request: GetEdgeConfigurationModules
    ) -> EdgeConfigurationModulesResult:
        """Get the effective (composite) configuration modules for an edge.

        Returns the reconciled configuration modules for the specified Edge in precisely the same
        format that those modules are dispatched to the Edge. This method merges elements from the
        various profile-level and edge-level modules that comprise an Edge's configuration stack.
        For example, the effective set of firewall rules is determined by concatenating rules from
        the profile-level and edge-level firewall modules. Useful for understanding exactly what
        configuration an Edge is running, without needing to manually merge the stack layers.

        Returns:
            Composite configuration modules reflecting the effective merged configuration,
            including deviceSettings, QOS, firewall, controlPlane, and other module types
        """
        return await self.portal(
            "edge/getEdgeConfigurationModules",
            request,
            EdgeConfigurationModulesResult,
        )

    @domain_tag(EDGE)
    async def get_client_visibility_mode(
        self: VcoClientBaseProtocol, request: GetClientVisibilityMode
    ) -> ClientVisibilityModeResult:
        """Get the client visibility mode setting for an edge.

        Retrieves the current client visibility mode for the specified edge, which controls how
        the edge identifies and reports on individual client devices behind it. The mode determines
        whether traffic statistics and flow data are aggregated or broken down by individual client
        IP/MAC address. Useful for understanding the granularity of per-client monitoring and
        troubleshooting flow visibility settings.

        Returns:
            Client visibility mode setting (e.g., IP_ADDRESS, MAC_ADDRESS, or NONE)
        """
        return await self.portal(
            "edge/getClientVisibilityMode",
            request,
            ClientVisibilityModeResult,
        )

    @domain_tag(EDGE)
    async def get_ha_interfaces(
        self: VcoClientBaseProtocol, request: GetHaInterfaces
    ) -> Sequence[HaInterfaceItem]:
        """Get High Availability (HA) status and interface details for an edge.

        Retrieves the HA state for the specified edge, including information about both the
        active and standby appliances in an HA pair. Returns interface-level details such as
        IP addresses, MAC addresses, and link status for each HA member. Useful for monitoring
        HA failover readiness, verifying HA pairing, and troubleshooting HA synchronization issues.

        Returns:
            HA interface details including active/standby status, IP addresses, MAC addresses,
            and link state for each HA member
        """
        return await self.portal_list(
            "edge/getHaInterfaces",
            request,
            HaInterfaceItem,
        )

    @domain_tag(EDGE)
    async def request_edge_ha_failover(
        self: VcoClientBaseProtocol, request: RequestEdgeHaFailover
    ) -> None:
        """Trigger an HA failover on an edge, promoting the standby to active.

        Instructs the VCO to initiate an HA failover for the specified edge. The current
        active appliance transitions to standby and the standby appliance becomes active.
        The edge must have HA configured and a healthy standby peer. This is a destructive
        operation that causes a brief traffic interruption during the switchover.

        Raises:
            VcoOperationError: If the failover request fails (e.g. no HA peer, edge offline).
        """
        result = await self.portal(
            "edge/edgeRequestEdgeHaFailover",
            request,
            RowsModifiedConfirmation,
        )
        if result.error:
            raise VcoOperationError("request edge HA failover", result.error)

    @domain_tag(EDGE)
    async def get_interconnect_gateways(
        self: VcoClientBaseProtocol, request: GetInterconnectGateways
    ) -> Sequence[InterconnectGatewayItem]:
        """Get interconnect gateways available to an enterprise.

        Returns the list of gateways that support interconnect functionality for the specified
        enterprise. Interconnect gateways enable connectivity between different VeloCloud
        network segments or between VeloCloud and external networks via gateway-based
        interconnection points. Useful for understanding which gateways support multi-segment
        or cross-network routing and for planning interconnect topology.

        Returns:
            Array of interconnect gateway objects with gateway identity, location, capabilities,
            and interconnect configuration details
        """
        return await self.portal_list(
            "edge/getInterconnectGateways",
            request,
            InterconnectGatewayItem,
        )

    @domain_tag(EDGE)
    async def get_secure_device_secrets_option(
        self: VcoClientBaseProtocol, request: GetSecureDeviceSecretsOption
    ) -> Sequence[SecureDeviceSecretsOptionResult]:
        """Get the secure device secrets option status for an edge.

        Retrieves whether the secure device secrets feature is enabled or disabled for the
        specified edge. When enabled, sensitive configuration data such as PSK keys, RADIUS
        secrets, and other credentials are encrypted and stored securely on the edge appliance
        using a TPM (Trusted Platform Module) or software-based encryption. Useful for auditing
        security posture and compliance across the SD-WAN deployment.

        Returns:
            Secure device secrets configuration including enabled/disabled status
        """
        return await self.portal_list(
            "edge/getSecureDeviceSecretsOption",
            request,
            SecureDeviceSecretsOptionResult,
        )

    @domain_tag(EDGE)
    async def get_edge_certificates(
        self: VcoClientBaseProtocol, request: GetEdgeCertificates
    ) -> Sequence[EdgeCertificate]:
        """Get PKI certificates for a SINGLE specific edge.

        Returns the list of PKI certificates associated with one edge. Each
        certificate includes validity dates (validFrom, validTo), subject key ID, serial
        number, and fingerprints. Use validTo to identify certificates approaching
        expiration.

        IMPORTANT — when to use:
        - Use ONLY when you already know the specific edge_id and only need
          that one edge's certificates.
        - For ANY question that spans multiple edges or "all edges" (e.g.
          "edges with certificates expiring in N days", auditing the whole
          enterprise, finding renewals due) DO NOT fan out one call per edge.
          Use `get_enterprise_edges(with=["certificates"])` instead — it
          returns every edge plus its certificates in a single paginated
          response and is dramatically faster and more reliable.

        Returns:
            Array of edge certificate objects with validity period, key identifiers,
            serial number, and fingerprint information
        """
        return await self.portal_list(
            "edge/getEdgeCertificates", request, EdgeCertificate
        )

    # --- VPN Hub Enable/Disable ---

    async def enable_edge_hub(
        self: VcoClientBaseProtocol, request: EnableEdgeHub
    ) -> EnterpriseServiceBase:
        """Designate an edge as a VPN hub on a profile segment."""
        return await self.portal(
            "edge/enableEdgeForEdgeHub", request, EnterpriseServiceBase
        )

    async def enable_edge_to_edge_bridge(
        self: VcoClientBaseProtocol, request: EnableEdgeToEdgeBridge
    ) -> EnterpriseServiceBase:
        """Designate an edge hub as a site-to-site bridge on a profile segment."""
        return await self.portal(
            "edge/enableEdgeForEdgeToEdgeBridge", request, EnterpriseServiceBase
        )

    async def enable_edge_backhaul(
        self: VcoClientBaseProtocol, request: EnableEdgeBackHaul
    ) -> EnterpriseServiceBase:
        """Designate an edge hub for backhaul on a profile segment."""
        return await self.portal(
            "edge/enableEdgeForBackHaul", request, EnterpriseServiceBase
        )

    async def disable_edge_hub(
        self: VcoClientBaseProtocol, request: DisableEdgeHub
    ) -> None:
        """Remove VPN hub designation from an edge on a profile segment."""
        await self.portal(
            "edge/disableEdgeForEdgeHub", request, RowsModifiedConfirmation
        )

    async def disable_edge_to_edge_bridge(
        self: VcoClientBaseProtocol, request: DisableEdgeToEdgeBridge
    ) -> EnterpriseServiceBase:
        """Remove site-to-site bridge designation from an edge on a profile segment."""
        return await self.portal(
            "edge/disableEdgeForEdgeToEdgeBridge", request, EnterpriseServiceBase
        )

    async def disable_edge_backhaul(
        self: VcoClientBaseProtocol, request: DisableEdgeBackHaul
    ) -> EnterpriseServiceBase:
        """Remove backhaul designation from an edge on a profile segment."""
        return await self.portal(
            "edge/disableEdgeForBackHaul", request, EnterpriseServiceBase
        )
