from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from pyvelo.client.enums import UserType
from pyvelo.client.exceptions import VcoError, VcoUserTypeUnknownError
from pyvelo.client.operations._tagging import (
    ADMIN,
    COMMON,
    CONFIG,
    EDGE,
    EVENTS,
    PM_HEALTH,
    PM_QOE,
    SECURITY,
    domain_tag,
)
from pyvelo.client.schemas.configuration import (
    EnterpriseConfigurationProfile,
)
from pyvelo.client.schemas.enterprise import (
    AnyEnterpriseService,
    AnyObjectGroup,
    AuthMigrationStatusResult,
    CustomApp,
    DeleteEnterpriseNetworkSegment,
    DeleteEnterpriseService,
    DeleteObjectGroup,
    DisableClusterBackHaul,
    DisableClusterHub,
    DisableClusterToEdgeBridge,
    EnableClusterBackHaul,
    EnableClusterHub,
    EnableClusterToEdgeBridge,
    EnterpriseAddressResult,
    EnterpriseAlertConfigurationResult,
    EnterpriseAllAlertsRecipientsResult,
    EnterpriseApiTokenResult,
    EnterpriseCapabilitiesResult,
    EnterpriseDefaultOperatorConfigurationResult,
    EnterpriseDistributedCostCalculationResult,
    EnterpriseGatewayHandoffResult,
    EnterpriseMaximumSegmentsResult,
    EnterpriseOperatorConfigurationResult,
    EnterprisePropertyResult,
    EnterpriseRateLimitsResult,
    EnterpriseResult,
    EnterpriseRouteConfigurationResult,
    EnterpriseRouteTableResult,
    EnterpriseSecurityObjectResult,
    EnterpriseServiceBase,
    EnterprisesMissingGatewayResult,
    EnterpriseSpecificOperatorConfigurationsResult,
    EnterprisesWithPropertyResult,
    EnterpriseUseNsdPolicyResult,
    EnterpriseUser,
    GetAuthMigrationStatus,
    GetEnterprise,
    GetEnterpriseAddresses,
    GetEnterpriseAlertConfigurations,
    GetEnterpriseAllAlertsRecipients,
    GetEnterpriseApiTokens,
    GetEnterpriseCapabilities,
    GetEnterpriseCustomApps,
    GetEnterpriseDefaultOperatorConfiguration,
    GetEnterpriseDistributedCostCalculation,
    GetEnterpriseEdgeList,
    GetEnterpriseEdgeListItem,
    GetEnterpriseEdges,
    GetEnterpriseEdgesItem,
    GetEnterpriseGatewayHandoff,
    GetEnterpriseMaximumSegments,
    GetEnterpriseNetworkSegments,
    GetEnterpriseNsdConfigurationResult,
    GetEnterpriseOperatorConfiguration,
    GetEnterprisePrivateNetworks,
    GetEnterpriseProperty,
    GetEnterpriseRateLimits,
    GetEnterpriseRouteConfiguration,
    GetEnterpriseRouteTable,
    GetEnterpriseSecurityObjects,
    GetEnterpriseServices,
    GetEnterprisesMissingGateway,
    GetEnterpriseSpecificOperatorConfigurations,
    GetEnterprisesWithProperty,
    GetEnterpriseUseNsdPolicy,
    GetEnterpriseUsers,
    GetNsdConfiguration,
    GetObjectGroups,
    InsertEnterpriseNetworkSegment,
    InsertEnterpriseService,
    InsertObjectGroup,
    NetworkSegment,
    PrivateNetwork,
    UpdateObjectGroup,
)
from pyvelo.client.schemas.enterprise_proxy import (
    EnterpriseProxyResult,
    GetEnterpriseProxy,
)
from pyvelo.client.schemas.enterprise_user import (
    EnterpriseUserApiTokenItem,
    EnterpriseUserResult,
    EnterpriseUsersStatusResult,
    GetEnterpriseUser,
    GetEnterpriseUserApiTokens,
    GetEnterpriseUsersStatus,
)
from pyvelo.client.schemas.event import (
    EnterpriseAlertItem,
    GetEnterpriseAlerts,
)
from pyvelo.client.schemas.network import (
    GetNetworkEnterprises,
    GetNetworkEnterprisesResultItem,
)
from pyvelo.client.schemas.profiles import GetEnterpriseConfigurations
from pyvelo.core import InsertionConfirmation, RowsModifiedConfirmation

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class EnterpriseMixin(Protocol):
    """Operations for enterprise/ and enterpriseUser/ API endpoints."""

    # --- enterprise/ endpoints ---

    @domain_tag(EDGE, PM_QOE, PM_HEALTH, CONFIG, SECURITY)
    async def get_enterprise_edges(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseEdges,
    ) -> Sequence[GetEnterpriseEdgesItem]:
        """List all Edge appliances in an enterprise with optional detail includes.

        Retrieves the full inventory of Edge CPE devices belonging to the enterprise.
        Each Edge record contains identification, activation state, software version,
        HA configuration, and operational state. Use the ``with_`` parameter on the
        request to include related data such as site info, WAN links, licenses, or
        the assigned configuration profile. Useful for fleet inventory, compliance
        auditing, and building Edge selection lists for downstream operations.

        Returns:
            Array of Edge objects with requested detail includes
        """
        return await self.portal_paginated_list(
            "enterprise/getEnterpriseEdges",
            request,
            GetEnterpriseEdgesItem,
        )

    @domain_tag(EDGE, PM_QOE, PM_HEALTH, CONFIG, SECURITY)
    async def get_enterprise_edge_list(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseEdgeList,
    ) -> Sequence[GetEnterpriseEdgeListItem]:
        """List Edge appliances using the UI-oriented edge-list endpoint.

        This endpoint supports a broader ``with_`` enum than
        ``enterprise/getEnterpriseEdges`` and returns pruned edge objects intended
        for inventory-style lists.
        """
        return await self.portal_paginated_list(
            "enterprise/getEnterpriseEdgeList",
            request,
            GetEnterpriseEdgeListItem,
        )

    @domain_tag(CONFIG)
    async def get_enterprise_configurations(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseConfigurations,
    ) -> Sequence[EnterpriseConfigurationProfile]:
        """List all configuration profiles in an enterprise."""
        return await self.portal_list(
            "enterprise/getEnterpriseConfigurations",
            request,
            EnterpriseConfigurationProfile,
        )

    @domain_tag(CONFIG)
    async def get_enterprise_services(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseServices,
    ) -> Sequence[AnyEnterpriseService]:
        """List enterprise network services such as cloud security, DNS, and data centers."""
        return await self.portal_list(
            "enterprise/getEnterpriseServices",
            request,
            AnyEnterpriseService,  # pyright: ignore[reportArgumentType]
        )

    @domain_tag(CONFIG)
    async def insert_enterprise_service(
        self: VcoClientBaseProtocol, request: InsertEnterpriseService
    ) -> InsertionConfirmation:
        """Create a new enterprise service (DNS, authentication, syslog, netflow, cloud security, etc.)."""
        return await self.portal(
            "enterprise/insertEnterpriseService",
            request,
            InsertionConfirmation,
        )

    async def delete_enterprise_service(
        self: VcoClientBaseProtocol, request: DeleteEnterpriseService
    ) -> RowsModifiedConfirmation:
        """Delete an enterprise service by ID or logical ID."""
        return await self.portal(
            "enterprise/deleteEnterpriseService",
            request,
            RowsModifiedConfirmation,
        )

    @domain_tag(CONFIG)
    async def get_nsd_configuration(
        self: VcoClientBaseProtocol, request: GetNsdConfiguration
    ) -> GetEnterpriseNsdConfigurationResult:
        """Retrieve the NSD BGP configurations for an enterprise."""
        return await self.portal(
            "enterprise/getNSDConfiguration",
            request,
            GetEnterpriseNsdConfigurationResult,
        )

    @domain_tag(CONFIG)
    async def get_enterprise_private_networks(
        self: VcoClientBaseProtocol,
        request: GetEnterprisePrivateNetworks,
    ) -> Sequence[PrivateNetwork]:
        """List private network names in an enterprise. These objects are referenced within WAN configuration modules."""
        return await self.portal_list(
            "enterprise/getEnterprisePrivateNetworks",
            request,
            PrivateNetwork,
        )

    @domain_tag(CONFIG)
    async def get_object_groups(
        self: VcoClientBaseProtocol,
        request: GetObjectGroups,
    ) -> Sequence[AnyObjectGroup]:
        """List object groups (address groups, port groups) used in firewall and business policies."""
        return await self.portal_list(
            "enterprise/getObjectGroups",
            request,
            AnyObjectGroup,  # pyright: ignore[reportArgumentType]
        )

    async def insert_object_group(
        self: VcoClientBaseProtocol, request: InsertObjectGroup
    ) -> InsertionConfirmation:
        """Create a new object group (address group or port group) for use in firewall and business policies."""
        return await self.portal(
            "enterprise/insertObjectGroup",
            request,
            InsertionConfirmation,
        )

    async def update_object_group(
        self: VcoClientBaseProtocol, request: UpdateObjectGroup
    ) -> RowsModifiedConfirmation:
        """Update an existing object group's name, description, or data entries."""
        return await self.portal(
            "enterprise/updateObjectGroup",
            request,
            RowsModifiedConfirmation,
        )

    async def delete_object_group(
        self: VcoClientBaseProtocol, request: DeleteObjectGroup
    ) -> RowsModifiedConfirmation:
        """Delete an object group. Fails if the object group is still referenced by profiles or edges."""
        return await self.portal(
            "enterprise/deleteObjectGroup",
            request,
            RowsModifiedConfirmation,
        )

    @domain_tag(CONFIG)
    async def get_enterprise_network_segments(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseNetworkSegments,
    ) -> Sequence[NetworkSegment]:
        """List network segments configured in an enterprise.

        Retrieves the set of network segments (VRFs) defined for the enterprise. In the
        VeloCloud SD-WAN architecture, segments provide network-level isolation across
        the overlay, allowing distinct routing tables, firewall policies, and business
        policies per segment. The default segment (segment 0) always exists; additional
        segments are created for multi-tenant or regulatory isolation use cases. Useful
        for understanding the enterprise's segmentation topology and for building
        segment-aware configuration or monitoring queries.

        Returns:
            Array of network segment definitions including segment ID, name, and type
        """
        return await self.portal_list(
            "enterprise/getEnterpriseNetworkSegments",
            request,
            NetworkSegment,
        )

    async def insert_enterprise_network_segment(
        self: VcoClientBaseProtocol, request: InsertEnterpriseNetworkSegment
    ) -> NetworkSegment:
        """Create a new network segment (VRF) in the enterprise."""
        return await self.portal(
            "enterprise/insertEnterpriseNetworkSegment",
            request,
            NetworkSegment,
        )

    async def delete_enterprise_network_segment(
        self: VcoClientBaseProtocol, request: DeleteEnterpriseNetworkSegment
    ) -> RowsModifiedConfirmation:
        """Delete a network segment from the enterprise."""
        return await self.portal(
            "enterprise/deleteEnterpriseNetworkSegment",
            request,
            RowsModifiedConfirmation,
        )

    @domain_tag(CONFIG)
    async def get_enterprise_custom_apps(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseCustomApps,
    ) -> Sequence[CustomApp]:
        """List custom application definitions for an enterprise.

        Retrieves user-defined application definitions that extend the VeloCloud DPI
        engine's built-in application classification. Custom apps are defined by IP/port
        patterns, domain names, or other L3-L7 criteria and allow enterprises to create
        business policy and QoS rules targeting internal or unrecognized traffic. Useful
        for auditing custom app coverage and checking for conflicts with built-in app
        definitions.

        Returns:
            Array of custom application definitions with match criteria and status
        """
        return await self.portal_list(
            "enterprise/getEnterpriseCustomApps",
            request,
            CustomApp,
        )

    @domain_tag(ADMIN, COMMON)
    async def get_enterprise(
        self: VcoClientBaseProtocol,
        request: GetEnterprise,
    ) -> EnterpriseResult:
        """Get enterprise (customer) details from VeloCloud Orchestrator.

        Retrieves core identity, account, service, gateway-pool, and operator-profile data for
        one customer enterprise. The target may be selected by integer ID or logical UUID; an
        enterprise-scoped session can infer its own enterprise. The optional ``with`` value
        ``enterpriseProxy`` includes the associated partner/MSP. Use an Edge-list operation to
        enumerate the enterprise's Edges.

        Returns:
            Enterprise object with its IDs, name, account and service status, gateway pool,
            operator profile, and optional enterpriseProxy relationship
        """
        return await self.portal(
            "enterprise/getEnterprise",
            request,
            EnterpriseResult,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_users(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseUsers,
    ) -> Sequence[EnterpriseUser]:
        """Get all admin users for an enterprise.

        Returns all enterprise administrator users for the specified enterprise. Each user object
        includes identity, role, active/locked/native status, last login, user type, and access
        level. The request ``id`` and ``enterpriseId`` fields both identify the target
        enterprise; this endpoint does not select an individual user. This operation is useful
        for read-only access audits; it does not create or modify users or API tokens.

        Returns:
            Array of enterprise user objects with identity, role, status, lastLogin, userType,
            and accessLevel fields
        """
        return await self.portal_list(
            "enterprise/getEnterpriseUsers",
            request,
            EnterpriseUser,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_gateway_handoff(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseGatewayHandoff,
    ) -> EnterpriseGatewayHandoffResult:
        """Get the gateway handoff configuration for an enterprise.

        Returns the gateway handoff configuration for the specified enterprise, which defines how
        traffic is handed off from VeloCloud gateways to the enterprise's data center or cloud
        infrastructure. This includes VLAN tagging, QoS mapping, NAT settings, and per-segment
        handoff rules. In the SD-WAN architecture, gateways act as traffic aggregation points,
        and the handoff configuration determines how gateway-routed traffic reaches its final
        destination. Useful for troubleshooting gateway-to-datacenter connectivity and verifying
        handoff policies.

        Returns:
            Gateway handoff configuration including VLAN, QoS, NAT, and per-segment handoff rules
        """
        return await self.portal(
            "enterprise/getEnterpriseGatewayHandoff",
            request,
            EnterpriseGatewayHandoffResult,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_capabilities(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseCapabilities,
    ) -> EnterpriseCapabilitiesResult:
        """Get all feature capabilities enabled or disabled for an enterprise.

        Returns the full set of enterprise capabilities (feature flags) such as BGP, COS mapping,
        PKI, stateful firewall, enhanced firewall, segmentation, and other advanced features.
        Each capability indicates whether the feature is currently enabled or disabled for the
        enterprise. These capabilities are typically managed by the operator or partner and control
        which features are available to the enterprise admin. Useful for verifying feature
        entitlements and understanding available functionality.

        Returns:
            Object mapping capability names to their enabled/disabled status (e.g., enableBGP,
            enablePKI, enableStatefulFirewall, enableSegmentation)
        """
        return await self.portal(
            "enterprise/getEnterpriseCapabilities",
            request,
            EnterpriseCapabilitiesResult,
        )

    @domain_tag(CONFIG)
    async def get_enterprise_addresses(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseAddresses,
    ) -> Sequence[EnterpriseAddressResult]:
        """Get public IP addresses for management and control entities of an enterprise.

        Returns all public IP address information for the Orchestrator(s), Gateway(s), and
        datacenter(s) associated with the specified enterprise. These are the IP addresses used
        for management plane and control plane communication in the SD-WAN overlay. Useful for
        firewall whitelisting, verifying control plane connectivity, and understanding the
        network infrastructure serving an enterprise.

        Returns:
            Array of IP address records for Orchestrators, Gateways, and datacenters
            associated with the enterprise
        """
        return await self.portal_list(
            "enterprise/getEnterpriseAddresses",
            request,
            EnterpriseAddressResult,
        )

    @domain_tag(EVENTS)
    async def get_enterprise_all_alerts_recipients(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseAllAlertsRecipients,
    ) -> EnterpriseAllAlertsRecipientsResult:
        """Get all recipients configured to receive all alerts for an enterprise.

        Returns the list of email addresses and webhook endpoints configured to receive all
        alert notifications for the specified enterprise. These are global alert recipients
        that receive every triggered alert regardless of alert type. Useful for auditing alert
        notification configuration and verifying that the correct personnel are receiving
        enterprise-wide alerts.

        Returns:
            List of alert recipient configurations including email addresses and webhook URLs
        """
        return await self.portal(
            "enterprise/getEnterpriseAllAlertsRecipients",
            request,
            EnterpriseAllAlertsRecipientsResult,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_rate_limits(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseRateLimits,
    ) -> EnterpriseRateLimitsResult:
        """Get API rate limit settings for an enterprise.

        Returns the rate limit configuration for the specified enterprise, including concurrency
        limits (maximum simultaneous API requests) and leaky-bucket limits (maximum requests per
        time window). These limits prevent any single enterprise from monopolizing VCO API
        resources. When limits are exceeded, the API returns HTTP 429 responses. Useful for
        understanding API usage constraints and diagnosing rate-limiting issues.

        Returns:
            Rate limit configuration including concurrency limits, burst limits, and
            penalty duration settings
        """
        return await self.portal(
            "enterprise/getEnterpriseRateLimits",
            request,
            EnterpriseRateLimitsResult,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_property(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseProperty,
    ) -> EnterprisePropertyResult:
        """Get a specific enterprise property by name.

        Retrieves a single enterprise property identified by name. Enterprise properties are
        key-value pairs that store enterprise-level settings, feature flags, and configuration
        metadata. Properties control various behaviors such as edge software management policies,
        analytics settings, and operational preferences. Useful for querying specific configuration
        values without fetching the entire enterprise object.

        Returns:
            Property object with name, value, dataType, and metadata
        """
        return await self.portal(
            "enterprise/getEnterpriseProperty",
            request,
            EnterprisePropertyResult,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_default_operator_configuration(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseDefaultOperatorConfiguration,
    ) -> EnterpriseDefaultOperatorConfigurationResult:
        """Get the default operator configuration (software image/profile) assigned to an enterprise.

        Returns the default operator profile that serves as the base configuration layer for the
        enterprise. Operator profiles (also called Software Images) are managed by the network
        operator and define default settings for device configuration, control plane behavior,
        and available features. The default operator profile is inherited by all enterprise
        configuration profiles unless overridden. Useful for understanding the baseline
        configuration an enterprise inherits from the operator.

        Returns:
            Operator configuration profile object with modules, version, and settings
        """
        return await self.portal(
            "enterprise/getEnterpriseDefaultOperatorConfiguration",
            request,
            EnterpriseDefaultOperatorConfigurationResult,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_distributed_cost_calculation(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseDistributedCostCalculation,
    ) -> EnterpriseDistributedCostCalculationResult:
        """Get the distributed cost calculation property for an enterprise.

        Returns whether distributed cost calculation is enabled for the specified enterprise.
        Distributed cost calculation controls how routing cost metrics are computed across the
        SD-WAN overlay, affecting path selection and traffic steering decisions. When enabled,
        edges participate in distributed route cost computation rather than relying solely on
        centralized orchestrator-based calculations. Useful for understanding routing behavior
        and verifying advanced routing configuration.

        Returns:
            Distributed cost calculation property with enabled/disabled status
        """
        return await self.portal(
            "enterprise/getEnterpriseDistributedCostCalculation",
            request,
            EnterpriseDistributedCostCalculationResult,
        )

    @domain_tag(CONFIG)
    async def get_enterprise_maximum_segments(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseMaximumSegments,
    ) -> EnterpriseMaximumSegmentsResult:
        """Get the maximum number of network segments allowed for an enterprise.

        Returns the maximum segments limit configured for the specified enterprise. Network
        segments in VeloCloud SD-WAN provide isolated routing domains (similar to VRFs) that
        enable traffic separation across the overlay. The maximum segments setting caps how many
        segments an enterprise can define, which is constrained by operator policy and licensing.
        Useful for capacity planning and understanding segmentation limits.

        Returns:
            Maximum segments value for the enterprise
        """
        return await self.portal(
            "enterprise/getEnterpriseMaximumSegments",
            request,
            EnterpriseMaximumSegmentsResult,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_operator_configuration(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseOperatorConfiguration,
    ) -> EnterpriseOperatorConfigurationResult:
        """Get the operator profile assigned to an enterprise.

        Fetches the operator profile (Software Image) currently assigned to the target enterprise,
        with optional module details. The operator profile defines the base-layer configuration
        that all enterprise profiles inherit from, including device settings defaults, control
        plane behavior, and feature availability. Operator profiles are managed by the network
        operator and determine the software image and default policies for edges. Useful for
        understanding the foundation configuration layer and its module settings.

        Returns:
            Operator profile object with profile metadata, software version, and
            configuration modules
        """
        return await self.portal(
            "enterprise/getEnterpriseOperatorConfiguration",
            request,
            EnterpriseOperatorConfigurationResult,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_specific_operator_configurations(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseSpecificOperatorConfigurations,
    ) -> Sequence[EnterpriseSpecificOperatorConfigurationsResult]:
        """Get all operator configurations specifically assigned to an enterprise.

        Returns the set of operator configurations (Software Images) that have been explicitly
        assigned to the enterprise by the operator or partner. Unlike the default operator
        configuration, these are additional operator profiles that the enterprise can select
        from when configuring edge profiles. Useful for understanding which software images
        and operator-level configurations are available to the enterprise.

        Returns:
            Array of operator configuration profiles assigned to the enterprise
        """
        return await self.portal_list(
            "enterprise/getEnterpriseSpecificOperatorConfigurations",
            request,
            EnterpriseSpecificOperatorConfigurationsResult,
        )

    @domain_tag(ADMIN)
    async def get_enterprises_missing_gateway(
        self: VcoClientBaseProtocol,
        request: GetEnterprisesMissingGateway,
    ) -> Sequence[EnterprisesMissingGatewayResult]:
        """Get the list of enterprises that are missing required gateway type assignments.

        Returns enterprises that do not have a complete gateway assignment, meaning they lack
        the required gateway types (e.g., primary, secondary, or super gateways) needed for
        proper SD-WAN operation. Edges require gateway assignments for cloud-routed traffic,
        multi-path optimization, and Non-VeloCloud Site connectivity. Useful for identifying
        enterprises with incomplete deployments that may experience connectivity issues.

        Returns:
            Array of enterprise objects that have missing gateway type assignments
        """
        return await self.portal_list(
            "enterprise/getEnterprisesMissingGateway",
            request,
            EnterprisesMissingGatewayResult,
        )

    @domain_tag(ADMIN)
    async def get_enterprises_with_property(
        self: VcoClientBaseProtocol,
        request: GetEnterprisesWithProperty,
    ) -> Sequence[EnterprisesWithPropertyResult]:
        """Get all enterprises that have a specific property name and value.

        Searches across all enterprises to find those that have a particular enterprise property
        set with the specified value. Enterprise properties are key-value pairs that store
        configuration metadata, feature flags, and operational settings. Useful for identifying
        which enterprises have specific features enabled, finding enterprises with particular
        configuration settings, or auditing property usage across the VCO.

        Returns:
            Array of enterprise objects that have the specified property and value
        """
        return await self.portal_list(
            "enterprise/getEnterprisesWithProperty",
            request,
            EnterprisesWithPropertyResult,
        )

    @domain_tag(ADMIN)
    async def get_auth_migration_status(
        self: VcoClientBaseProtocol,
        request: GetAuthMigrationStatus,
    ) -> AuthMigrationStatusResult:
        """Get the authentication migration status for one or more enterprises.

        Returns the status of authentication system migration (e.g., from local authentication
        to SSO/SAML or from legacy to modern auth) for the specified enterprises. This is used
        during orchestrator-wide authentication upgrades to track which enterprises have been
        migrated, are in progress, or still need migration. Useful for planning and monitoring
        authentication infrastructure transitions.

        Returns:
            Migration status for each specified enterprise, including migration state
            and completion details
        """
        return await self.portal(
            "enterprise/getAuthMigrationStatus",
            request,
            AuthMigrationStatusResult,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_api_tokens(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseApiTokens,
    ) -> Sequence[EnterpriseApiTokenResult]:
        """Get all API tokens provisioned for an enterprise.

        Returns all API tokens that have been created within the specified enterprise context.
        API tokens provide programmatic access to the VCO API and are typically longer-lived
        than session cookies. Each token record includes the token name, creation date, expiration,
        associated user, and current status. Note that the actual token value is only available
        at creation time. Useful for auditing API access, managing token lifecycle, and reviewing
        programmatic access to the enterprise.

        Returns:
            Array of API token objects with name, creation date, expiration, associated user,
            and status
        """
        return await self.portal_list(
            "enterprise/getApiTokens",
            request,
            EnterpriseApiTokenResult,
        )

    @domain_tag(SECURITY, CONFIG)
    async def get_security_objects(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseSecurityObjects,
    ) -> Sequence[EnterpriseSecurityObjectResult]:
        """Get all security objects defined for an enterprise.

        Returns all security objects configured for the specified enterprise. Security objects
        include address groups (collections of IP addresses/subnets), service groups (collections
        of ports/protocols), and FQDN groups that can be referenced in firewall rules, business
        policies, and other security configurations. Useful for reviewing the reusable security
        building blocks available for policy configuration and auditing security object definitions.

        Returns:
            Array of security objects including address groups, service groups, and FQDN groups
            with their member definitions
        """
        return await self.portal_list(
            "enterprise/getSecurityObjects",
            request,
            EnterpriseSecurityObjectResult,
        )

    @domain_tag(EVENTS)
    async def get_enterprise_alert_configurations(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseAlertConfigurations,
    ) -> Sequence[EnterpriseAlertConfigurationResult]:
        """Get all alert configurations for an enterprise.

        Returns all alert configuration rules for the specified enterprise. Alert configurations
        define which events trigger alert notifications, the severity levels, notification
        recipients (email, webhook), and any filtering criteria (e.g., specific edges or links).
        These configurations control the alerting behavior across the enterprise's SD-WAN
        deployment. Useful for auditing alert policies, understanding notification routing,
        and troubleshooting missing or excessive alerts.

        Returns:
            Array of alert configuration objects with alert type, enabled status, severity,
            recipients, and filtering criteria
        """
        return await self.portal_list(
            "enterprise/getEnterpriseAlertConfigurations",
            request,
            EnterpriseAlertConfigurationResult,
        )

    @domain_tag(PM_HEALTH)
    async def get_enterprise_route_table(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseRouteTable,
    ) -> EnterpriseRouteTableResult:
        """Get the composite enterprise route table.

        Returns the full enterprise route table, optionally scoped by profile(s). The returned
        routes include static routes, directly connected routes, and learned routes (from BGP,
        OSPF, or other dynamic routing protocols). Each route entry includes the destination
        prefix, next hop, metric, route type, advertising edge, and segment association.
        Useful for troubleshooting routing issues, verifying route advertisement, and
        understanding the overall routing topology across the enterprise's SD-WAN overlay.

        Returns:
            Route table containing static, connected, and learned routes with prefix,
            next hop, metric, type, and segment information
        """
        return await self.portal(
            "enterprise/getEnterpriseRouteTable",
            request,
            EnterpriseRouteTableResult,
        )

    @domain_tag(CONFIG)
    async def get_enterprise_route_configuration(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseRouteConfiguration,
    ) -> EnterpriseRouteConfigurationResult:
        """Get enterprise global routing preferences.

        Returns all global routing preferences for the specified enterprise, including enterprise
        route advertisement policy, routing preferences (overlay vs underlay), OSPF settings,
        and BGP advertisement policy. These preferences govern how routes are distributed across
        the SD-WAN overlay, which routes are advertised to BGP/OSPF peers, and how route
        preferences are calculated. Useful for understanding and troubleshooting routing behavior
        across the enterprise deployment.

        Returns:
            Global routing preferences including route advertisement, OSPF, BGP advertisement
            policy, and overlay/underlay routing preferences
        """
        return await self.portal(
            "enterprise/getEnterpriseRouteConfiguration",
            request,
            EnterpriseRouteConfigurationResult,
        )

    @domain_tag(ADMIN, CONFIG)
    async def get_enterprise_use_nsd_policy(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseUseNsdPolicy,
    ) -> EnterpriseUseNsdPolicyResult:
        """Get the "Use NSD Policy" property for an enterprise.

        Returns whether Non-SD-WAN Destination (NSD) services can be considered in overlay flow
        control (OFC) routing decisions for the specified enterprise. When enabled, traffic
        steering policies can route traffic through NSD services (e.g., cloud security providers
        or third-party data centers) as part of the SD-WAN routing decision. Useful for
        understanding whether NSD-based routing is active and for troubleshooting traffic
        steering to third-party services.

        Returns:
            NSD policy property indicating whether NSD routing is enabled or disabled
        """
        return await self.portal(
            "enterprise/getEnterpriseUseNSDPolicy",
            request,
            EnterpriseUseNsdPolicyResult,
        )

    @domain_tag(EVENTS)
    async def get_enterprise_alerts(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseAlerts,
    ) -> Sequence[EnterpriseAlertItem]:
        """Get past triggered alerts for an enterprise.

        Returns alerts that have been triggered for the specified enterprise, such as edge
        offline alerts, link down alerts, HA failover alerts, and other configured alert types.
        Each alert includes the trigger time, alert type, severity, affected entity (edge, link),
        and current state. Alerts are generated when enterprise-configured alert rules match
        detected events. Useful for monitoring network health, investigating incidents, and
        reviewing historical alert activity.

        Returns:
            Array of triggered alert objects with alertType, triggerTime, severity, affected
            entity details, and alert state
        """
        return await self.portal_list(
            "enterprise/getEnterpriseAlerts",
            request,
            EnterpriseAlertItem,
        )

    # --- enterpriseUser/ endpoints ---

    @domain_tag(ADMIN)
    async def get_enterprise_user(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseUser,
    ) -> EnterpriseUserResult:
        """Get a specific enterprise admin user by ID or username.

        Retrieves the specified enterprise user object including username, email, role, login
        status, last login time, two-factor authentication settings, and other account attributes.
        Enterprise users are administrator accounts that can manage the enterprise's SD-WAN
        configuration, monitor edges, and access reports. Useful for retrieving specific user
        account details and verifying user permissions.

        Returns:
            Enterprise user object with username, email, role, roleId, isActive, lastLogin,
            and authentication configuration
        """
        return await self.portal(
            "enterpriseUser/getEnterpriseUser",
            request,
            EnterpriseUserResult,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_user_api_tokens(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseUserApiTokens,
    ) -> Sequence[EnterpriseUserApiTokenItem]:
        """Get all API tokens owned by a specific enterprise user.

        Returns all API tokens allocated to the specified enterprise user. API tokens provide
        programmatic access to the VCO API with the same permissions as the owning user. Each
        token record includes creation date, expiration, and status. Useful for auditing which
        API tokens a user has created and managing token lifecycle for automation accounts.

        Returns:
            Array of API token objects with name, creation date, expiration, and status
        """
        return await self.portal_list(
            "enterpriseUser/getApiTokens",
            request,
            EnterpriseUserApiTokenItem,
        )

    @domain_tag(ADMIN)
    async def get_enterprise_users_status(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseUsersStatus,
    ) -> EnterpriseUsersStatusResult:
        """Get the login and account status for multiple enterprise users.

        Returns status information for the specified enterprise users in a single batch request.
        Status includes whether the user account is active, locked, or disabled, as well as
        last login time and session state. Useful for bulk auditing of user account status
        across an enterprise and identifying inactive or locked accounts.

        Returns:
            Status information for each specified user, including active/locked state,
            last login time, and session details
        """
        return await self.portal(
            "enterpriseUser/getEnterpriseUsersStatus",
            request,
            EnterpriseUsersStatusResult,
        )

    async def determine_user_type(self: VcoClientBaseProtocol) -> UserType:
        """Determine whether the authenticated token belongs to an enterprise, MSP, or operator user.

        Probes three endpoints in order with empty requests (the VCO infers identity
        from the token context). Short-circuits on the first successful response.

        Returns:
            UserType.ENTERPRISE, UserType.MSP, or UserType.OPERATOR

        Raises:
            VcoError: If all three probes fail (token may be invalid or expired).
        """
        try:
            await self.portal_list(
                "network/getNetworkEnterprises",
                GetNetworkEnterprises(),
                GetNetworkEnterprisesResultItem,
            )
            return UserType.OPERATOR
        except VcoError:
            pass

        try:
            await self.portal(
                "enterpriseProxy/getEnterpriseProxy",
                GetEnterpriseProxy(),
                EnterpriseProxyResult,
            )
            return UserType.MSP
        except VcoError:
            pass

        try:
            await self.portal(
                "enterprise/getEnterprise",
                GetEnterprise(),
                EnterpriseResult,
            )
            return UserType.ENTERPRISE
        except VcoError:
            pass

        raise VcoUserTypeUnknownError()

    # --- Cluster Hub Enable/Disable ---

    async def enable_cluster_hub(
        self: VcoClientBaseProtocol, request: EnableClusterHub
    ) -> EnterpriseServiceBase:
        """Designate a cluster as a VPN hub on a profile segment."""
        return await self.portal(
            "enterprise/enableClusterForEdgeHub", request, EnterpriseServiceBase
        )

    async def enable_cluster_to_edge_bridge(
        self: VcoClientBaseProtocol, request: EnableClusterToEdgeBridge
    ) -> EnterpriseServiceBase:
        """Designate a cluster hub as a site-to-site bridge on a profile segment."""
        return await self.portal(
            "enterprise/enableClusterForEdgeToEdgeBridge",
            request,
            EnterpriseServiceBase,
        )

    async def enable_cluster_backhaul(
        self: VcoClientBaseProtocol, request: EnableClusterBackHaul
    ) -> EnterpriseServiceBase:
        """Designate a cluster hub for backhaul on a profile segment."""
        return await self.portal(
            "enterprise/enableClusterForBackHaul", request, EnterpriseServiceBase
        )

    async def disable_cluster_hub(
        self: VcoClientBaseProtocol, request: DisableClusterHub
    ) -> EnterpriseServiceBase:
        """Remove VPN hub designation from a cluster on a profile segment."""
        return await self.portal(
            "enterprise/disableClusterForEdgeHub", request, EnterpriseServiceBase
        )

    async def disable_cluster_to_edge_bridge(
        self: VcoClientBaseProtocol, request: DisableClusterToEdgeBridge
    ) -> EnterpriseServiceBase:
        """Remove site-to-site bridge designation from a cluster on a profile segment."""
        return await self.portal(
            "enterprise/disableClusterForEdgeToEdgeBridge",
            request,
            EnterpriseServiceBase,
        )

    async def disable_cluster_backhaul(
        self: VcoClientBaseProtocol, request: DisableClusterBackHaul
    ) -> EnterpriseServiceBase:
        """Remove backhaul designation from a cluster on a profile segment."""
        return await self.portal(
            "enterprise/disableClusterForBackHaul", request, EnterpriseServiceBase
        )
