from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from pyvelo.client.operations._tagging import ADMIN, domain_tag
from pyvelo.client.schemas.enterprise_export import (
    ExportMetadataResult,
    GetExportMetadata,
    GetImportResult,
    ImportResultResult,
)
from pyvelo.client.schemas.enterprise_proxy import (
    EnterpriseProxyCapabilitiesResult,
    EnterpriseProxyCloneableEnterpriseItem,
    EnterpriseProxyConfigurationItem,
    EnterpriseProxyEdgeInventoryItem,
    EnterpriseProxyEdgeItem,
    EnterpriseProxyEnterpriseItem,
    EnterpriseProxyEnterprisesWithPropertyItem,
    EnterpriseProxyGatewayItem,
    EnterpriseProxyGatewayPoolItem,
    EnterpriseProxyItem,
    EnterpriseProxyOperatorProfileItem,
    EnterpriseProxyPropertyResult,
    EnterpriseProxyRateLimitsResult,
    EnterpriseProxyResult,
    EnterpriseProxyServiceItem,
    EnterpriseProxyUserItem,
    EnterpriseProxyUserResult,
    GetEnterpriseProxies,
    GetEnterpriseProxy,
    GetEnterpriseProxyCapabilities,
    GetEnterpriseProxyCloneableEnterprises,
    GetEnterpriseProxyConfigurations,
    GetEnterpriseProxyEdgeInventory,
    GetEnterpriseProxyEdges,
    GetEnterpriseProxyEnterprises,
    GetEnterpriseProxyEnterprisesWithProperty,
    GetEnterpriseProxyGatewayPools,
    GetEnterpriseProxyGateways,
    GetEnterpriseProxyOperatorProfiles,
    GetEnterpriseProxyProperty,
    GetEnterpriseProxyRateLimits,
    GetEnterpriseProxyServices,
    GetEnterpriseProxyUser,
    GetEnterpriseProxyUsers,
)

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class EnterpriseProxyMixin(Protocol):
    """Operations for enterpriseProxy/, enterpriseExport/, and enterpriseImport/ API endpoints."""

    # --- enterpriseProxy/ endpoints ---

    @domain_tag(ADMIN)
    async def get_enterprise_proxy(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseProxy,
    ) -> EnterpriseProxyResult:
        """Get details for a specific partner (MSP/enterprise proxy).

        Retrieves identity and contact data for one partner/MSP. An MSP session can infer its own
        proxy; an operator selects a proxy by integer ID or logical UUID. The optional ``with``
        values include gateway pool details or IDs.

        Returns:
            Enterprise proxy object with name, domain, contact info, and configuration settings
        """
        return await self.portal(
            "enterpriseProxy/getEnterpriseProxy",
            request,
            EnterpriseProxyResult,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_proxies(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseProxies,
    ) -> Sequence[EnterpriseProxyItem]:
        """Get all partners (MSPs/enterprise proxies) on the VCO.

        Returns all enterprise proxy (partner/MSP) organizations registered on the orchestrator.
        Each partner can manage multiple customer enterprises and has its own set of admin users,
        gateway pools, and operator profiles. Useful for getting an overview of all partners on
        the VCO, typically invoked by operator-level administrators.

        Returns:
            Array of enterprise proxy objects with name, domain, and configuration details
        """
        return await self.portal_list(
            "enterpriseProxy/getEnterpriseProxies",
            request,
            EnterpriseProxyItem,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_proxy_edges(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseProxyEdges,
    ) -> Sequence[EnterpriseProxyEdgeItem]:
        """Get all edges across all enterprises managed by a partner (MSP).

        Returns all edge appliances belonging to all customer enterprises managed by the specified
        partner. This provides a fleet-wide view of all edges under the partner's management scope,
        regardless of which enterprise they belong to. Each edge includes status, model, software
        version, and enterprise association. Useful for partner administrators who need to monitor
        or report on the entire managed edge population.

        Returns:
            Array of edge objects across all managed enterprises with edge status, model,
            software version, and enterprise association
        """
        return await self.portal_list(
            "enterpriseProxy/getEnterpriseProxyEdges",
            request,
            EnterpriseProxyEdgeItem,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_proxy_enterprises(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseProxyEnterprises,
    ) -> Sequence[EnterpriseProxyEnterpriseItem]:
        """Get all customer enterprises managed by a partner (MSP).

        Returns the partner's customer enterprises. An MSP session can infer its own proxy; an
        operator supplies the target proxy ID. Optional related data includes full Edge records,
        Edge counts, configuration-update data, or service licenses. Prefer Edge counts unless
        full Edge records are required.

        Returns:
            Array of enterprise objects managed by the partner, with name, ID, edge count,
            and configuration summary
        """
        return await self.portal_list(
            "enterpriseProxy/getEnterpriseProxyEnterprises",
            request,
            EnterpriseProxyEnterpriseItem,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_proxy_gateway_pools(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseProxyGatewayPools,
    ) -> Sequence[EnterpriseProxyGatewayPoolItem]:
        """Get all gateway pools associated with a partner (MSP).

        Returns all gateway pools assigned to the specified partner. Gateway pools are logical
        groupings of VeloCloud gateways that can be assigned to customer enterprises for traffic
        routing. Each pool optionally includes its member gateways and the enterprises that use
        it. Partners use gateway pools to control which gateways are available to their managed
        enterprises. Useful for reviewing gateway infrastructure and pool-to-enterprise mappings.

        Returns:
            Array of gateway pool objects with pool name, member gateways, and associated
            enterprise assignments
        """
        return await self.portal_list(
            "enterpriseProxy/getEnterpriseProxyGatewayPools",
            request,
            EnterpriseProxyGatewayPoolItem,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_proxy_gateways(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseProxyGateways,
    ) -> Sequence[EnterpriseProxyGatewayItem]:
        """Get all gateways associated with a partner (MSP).

        Returns all VeloCloud gateways that are associated with the specified partner. Gateways
        are cloud or on-premise infrastructure nodes that provide traffic routing, optimization,
        and connectivity services for SD-WAN edges. This returns the partner's view of available
        gateway infrastructure. Useful for reviewing the gateway resources available to a partner
        and their operational status.

        Returns:
            Array of gateway objects with gateway name, IP, state, location, and pool membership
        """
        return await self.portal_list(
            "enterpriseProxy/getEnterpriseProxyGateways",
            request,
            EnterpriseProxyGatewayItem,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_proxy_operator_profiles(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseProxyOperatorProfiles,
    ) -> Sequence[EnterpriseProxyOperatorProfileItem]:
        """Get all operator profiles (software images) assigned to a partner (MSP).

        Returns all operator profiles that the network operator has assigned to the specified
        partner. Operator profiles define the base-layer software image and default configuration
        for edges. Partners can then assign these operator profiles to their managed enterprises.
        Useful for reviewing which software images are available to a partner and which can be
        assigned to customer enterprises.

        Returns:
            Array of operator profile objects with profile name, software version, and
            configuration details
        """
        return await self.portal_list(
            "enterpriseProxy/getEnterpriseProxyOperatorProfiles",
            request,
            EnterpriseProxyOperatorProfileItem,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_proxy_property(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseProxyProperty,
    ) -> EnterpriseProxyPropertyResult:
        """Get a specific property for a partner (MSP) by name.

        Retrieves a single enterprise proxy property identified by name or object ID. Enterprise
        proxy properties are key-value pairs that store partner-level configuration metadata,
        feature flags, and operational settings. Useful for querying specific partner configuration
        values without fetching the entire proxy object.

        Returns:
            Property object with name, value, dataType, and metadata, or None if not found
        """
        return await self.portal(
            "enterpriseProxy/getEnterpriseProxyProperty",
            request,
            EnterpriseProxyPropertyResult,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_proxy_user(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseProxyUser,
    ) -> EnterpriseProxyUserResult:
        """Get a specific partner admin user by ID or username.

        Retrieves the specified enterprise proxy (partner/MSP) admin user. Each partner admin
        user has access to manage the partner's customer enterprises based on their assigned
        role. The user object includes username, email, role, login status, and authentication
        settings. Useful for auditing specific partner admin accounts and verifying user
        permissions.

        Returns:
            Partner admin user object with username, email, role, isActive, lastLogin, and
            authentication configuration
        """
        return await self.portal(
            "enterpriseProxy/getEnterpriseProxyUser",
            request,
            EnterpriseProxyUserResult,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_proxy_users(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseProxyUsers,
    ) -> Sequence[EnterpriseProxyUserItem]:
        """Get all admin users for a partner (MSP).

        Returns all proxy admin users associated with the specified partner. Partner admin users
        have management access to the partner's customer enterprises based on their assigned
        roles. Useful for auditing partner admin access, reviewing user roles, and managing
        the partner's administrative user base.

        Returns:
            Array of partner admin user objects with username, email, role, isActive,
            and lastLogin
        """
        return await self.portal_list(
            "enterpriseProxy/getEnterpriseProxyUsers",
            request,
            EnterpriseProxyUserItem,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_proxy_configurations(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseProxyConfigurations,
    ) -> Sequence[EnterpriseProxyConfigurationItem]:
        """Get all configuration profiles managed by a partner (MSP).

        Returns all configuration profiles associated with the specified partner. These are
        partner-level configuration templates that can serve as the basis for customer enterprise
        profiles. Partners can create and manage configuration profiles that are then assigned
        to their managed enterprises. Useful for reviewing the partner's configuration template
        library and understanding available profile options.

        Returns:
            Array of configuration profile objects with name, description, and module summary
        """
        return await self.portal_list(
            "enterpriseProxy/getEnterpriseProxyConfigurations",
            request,
            EnterpriseProxyConfigurationItem,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_proxy_edge_inventory(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseProxyEdgeInventory,
    ) -> Sequence[EnterpriseProxyEdgeInventoryItem]:
        """Get all partner enterprises and their associated edge inventory.

        Returns all customer enterprises managed by the specified partner along with their
        complete edge inventory. This provides a comprehensive view of all edge appliances
        across all managed enterprises, including edge status, model, serial number, and
        activation state. Useful for partner administrators who need a consolidated inventory
        report across their entire managed customer base.

        Returns:
            Array of enterprise objects, each containing their edge inventory with edge
            status, model, serial number, and activation details
        """
        return await self.portal_list(
            "enterpriseProxy/getEnterpriseProxyEdgeInventory",
            request,
            EnterpriseProxyEdgeInventoryItem,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_proxy_rate_limits(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseProxyRateLimits,
    ) -> EnterpriseProxyRateLimitsResult:
        """Get API rate limits for a partner (MSP).

        Returns the API rate limit configuration for the specified enterprise proxy (partner).
        Rate limits include concurrency limits (maximum simultaneous API requests) and
        leaky-bucket limits (maximum requests per time window) that prevent the partner from
        monopolizing VCO API resources. Useful for understanding API usage constraints and
        diagnosing rate-limiting issues at the partner level.

        Returns:
            Rate limit configuration including concurrency limits, burst limits, and
            penalty duration settings
        """
        return await self.portal(
            "enterpriseProxy/getEnterpriseProxyRateLimits",
            request,
            EnterpriseProxyRateLimitsResult,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_proxy_services(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseProxyServices,
    ) -> Sequence[EnterpriseProxyServiceItem]:
        """Get all network services available to a partner (MSP).

        Returns all network services associated with the specified partner, including Non-SD-WAN
        Destinations, cloud security services, and other third-party service integrations that
        the partner can make available to managed enterprises. Useful for reviewing the partner's
        service catalog and understanding which network services can be configured for customer
        enterprises.

        Returns:
            Array of network service objects with service type, configuration, and availability
        """
        return await self.portal_list(
            "enterpriseProxy/getEnterpriseProxyServices",
            request,
            EnterpriseProxyServiceItem,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_proxy_capabilities(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseProxyCapabilities,
    ) -> EnterpriseProxyCapabilitiesResult:
        """Get all feature capabilities enabled or disabled for a partner (MSP).

        Returns the full set of partner capabilities (feature flags) such as role customization,
        enterprise provisioning, and other advanced features currently enabled or disabled for
        the specified partner. These capabilities are managed by the network operator and control
        which features the partner can access and delegate to managed enterprises. Useful for
        verifying partner feature entitlements and understanding available functionality.

        Returns:
            Object mapping capability names to their enabled/disabled status
        """
        return await self.portal(
            "enterpriseProxy/getEnterpriseProxyCapabilities",
            request,
            EnterpriseProxyCapabilitiesResult,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_proxy_cloneable_enterprises(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseProxyCloneableEnterprises,
    ) -> Sequence[EnterpriseProxyCloneableEnterpriseItem]:
        """Get enterprises that can serve as templates for cloning under a partner (MSP).

        Returns all cloneable enterprises within the specified partner scope, including delegation
        information. Cloning an enterprise creates a new enterprise with the same configuration
        profiles, services, and settings as the source enterprise, which accelerates onboarding
        of new customers with similar configurations. Useful for identifying available enterprise
        templates when provisioning new customers.

        Returns:
            Array of cloneable enterprise objects with enterprise details and delegation information
        """
        return await self.portal_list(
            "enterpriseProxy/getEnterpriseProxyCloneableEnterprises",
            request,
            EnterpriseProxyCloneableEnterpriseItem,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_proxy_enterprises_with_property(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseProxyEnterprisesWithProperty,
    ) -> Sequence[EnterpriseProxyEnterprisesWithPropertyItem]:
        """Get all MSP-managed enterprises that have a specific property.

        Searches across all enterprises managed by the specified partner to find those that have
        a particular enterprise property set. Enterprise properties are key-value pairs that
        store configuration metadata and feature flags. Useful for partner administrators who
        need to identify which of their managed enterprises have specific features enabled or
        particular configuration settings applied.

        Returns:
            Array of enterprise objects that have the specified property set
        """
        return await self.portal_list(
            "enterpriseProxy/getEnterpriseProxyEnterprisesWithProperty",
            request,
            EnterpriseProxyEnterprisesWithPropertyItem,
        )

    # --- enterpriseExport/ endpoints ---

    async def get_export_metadata(
        self: VcoClientBaseProtocol,
        request: GetExportMetadata,
    ) -> ExportMetadataResult:
        """Get job metadata for an enterprise configuration export package.

        Fetches the job metadata for a configuration export operation, including the secretKey,
        fileSize, and HMAC of the export package. Enterprise exports allow migration of a
        complete enterprise configuration (profiles, edges, services, etc.) from one VCO to
        another. This method retrieves metadata about an export that has been previously
        initiated. Useful for tracking export job status and obtaining information needed
        to download the export package.

        Returns:
            Export metadata including secretKey, fileSize, HMAC, and job status
        """
        return await self.portal(
            "enterpriseExport/getExportMetadata",
            request,
            ExportMetadataResult,
        )

    # --- enterpriseImport/ endpoints ---

    async def get_import_result(
        self: VcoClientBaseProtocol,
        request: GetImportResult,
    ) -> ImportResultResult:
        """Get the results of an enterprise configuration import operation.

        Fetches the details of a newly created enterprise resulting from a configuration import.
        Enterprise imports allow migration of a complete enterprise configuration from one VCO
        to another by importing a previously exported configuration package. This method returns
        the outcome of the import including the newly created enterprise details, any mapping
        changes, and import validation results. Useful for verifying that an enterprise migration
        completed successfully.

        Returns:
            Import result with newly created enterprise details, entity mappings, and
            validation status
        """
        return await self.portal(
            "enterpriseImport/getImportResult",
            request,
            ImportResultResult,
        )
