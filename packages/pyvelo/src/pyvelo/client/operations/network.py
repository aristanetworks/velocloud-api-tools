from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from pyvelo.client.operations._tagging import SYSTEM, domain_tag
from pyvelo.client.schemas.network import (
    GetEligibleReplacementGateways,
    GetEligibleReplacementGatewaysResultItem,
    GetNetworkApiTokens,
    GetNetworkApiTokensResultItem,
    GetNetworkCloneableEnterprises,
    GetNetworkCloneableEnterprisesResultItem,
    GetNetworkConfigurations,
    GetNetworkConfigurationsResultItem,
    GetNetworkEnterprises,
    GetNetworkEnterprisesResultItem,
    GetNetworkGatewayPools,
    GetNetworkGatewayPoolsResultItem,
    GetNetworkGateways,
    GetNetworkGatewaysResultItem,
    GetNetworkOperatorUsers,
    GetNetworkOperatorUsersResultItem,
)
from pyvelo.client.schemas.operator_user import (
    GetOperatorUser,
    GetOperatorUserApiTokens,
    GetOperatorUsersStatus,
    OperatorUserApiTokenResult,
    OperatorUserResult,
    OperatorUserStatusResult,
)

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class NetworkMixin(Protocol):
    """Operations for network/ and operatorUser/ API endpoints."""

    # --- network/ endpoints ---

    @domain_tag(SYSTEM)
    async def get_network_enterprises(
        self: VcoClientBaseProtocol, request: GetNetworkEnterprises
    ) -> Sequence[GetNetworkEnterprisesResultItem]:
        """List all enterprises in the network (operator-level view)."""
        return await self.portal_list(
            "network/getNetworkEnterprises",
            request,
            GetNetworkEnterprisesResultItem,
        )

    @domain_tag(SYSTEM)
    async def get_network_api_tokens(
        self: VcoClientBaseProtocol, request: GetNetworkApiTokens
    ) -> Sequence[GetNetworkApiTokensResultItem]:
        """Get all API tokens provisioned at the network (operator) level.

        Returns all API tokens created within the specified network/operator context. These are
        operator-level tokens that provide programmatic access to VCO API methods available to
        operator administrators. Each token record includes name, creation date, expiration, and
        associated user. Useful for auditing operator-level API access and managing token lifecycle
        for automation systems.

        Returns:
            Array of API token objects with name, creation date, expiration, associated user,
            and status
        """
        return await self.portal_list(
            "network/getApiTokens",
            request,
            GetNetworkApiTokensResultItem,
        )

    @domain_tag(SYSTEM)
    async def get_eligible_replacement_gateways(
        self: VcoClientBaseProtocol, request: GetEligibleReplacementGateways
    ) -> Sequence[GetEligibleReplacementGatewaysResultItem]:
        """Get gateways eligible to replace a specific gateway based on roles and pool assignments.

        Returns a list of gateways that are similar to the specified gateway with respect to
        roles and gateway pool assignments, making them suitable replacements. This is used
        during gateway decommissioning, maintenance, or capacity rebalancing to identify
        which other gateways can take over the workload. Useful for planning gateway replacements
        and ensuring continuity of service during infrastructure changes.

        Returns:
            Array of eligible replacement gateway objects with matching roles, pool memberships,
            and capacity information
        """
        return await self.portal_list(
            "network/getEligibleReplacementGateways",
            request,
            GetEligibleReplacementGatewaysResultItem,
        )

    @domain_tag(SYSTEM)
    async def get_network_cloneable_enterprises(
        self: VcoClientBaseProtocol, request: GetNetworkCloneableEnterprises
    ) -> Sequence[GetNetworkCloneableEnterprisesResultItem]:
        """Get all enterprises on the network that can serve as templates for cloning.

        Returns all cloneable enterprises on the specified network, including delegation
        information. Cloning an enterprise creates a new enterprise with identical configuration
        profiles, services, and settings, which accelerates customer onboarding. This
        operator-level method shows all enterprises across the network that are eligible to
        be used as clone sources. Useful for identifying enterprise templates when provisioning
        new customers at the operator level.

        Returns:
            Array of cloneable enterprise objects with enterprise details and delegation information
        """
        return await self.portal_list(
            "network/getNetworkCloneableEnterprises",
            request,
            GetNetworkCloneableEnterprisesResultItem,
        )

    @domain_tag(SYSTEM)
    async def get_network_configurations(
        self: VcoClientBaseProtocol, request: GetNetworkConfigurations
    ) -> Sequence[GetNetworkConfigurationsResultItem]:
        """Get all operator configuration profiles (software images) on the network.

        Returns all operator configuration profiles associated with the network. Operator profiles
        define the base-layer software image, default device settings, and feature availability
        for edges. These profiles are assigned to enterprises and serve as the foundation of the
        configuration inheritance hierarchy. This method does not return templates. Optionally
        includes refs (associations with network services). Useful for reviewing available
        software images and operator-level configuration options.

        Returns:
            Array of operator configuration profile objects with name, software version,
            and optionally refs (network service associations)
        """
        return await self.portal_list(
            "network/getNetworkConfigurations",
            request,
            GetNetworkConfigurationsResultItem,
        )

    @domain_tag(SYSTEM)
    async def get_network_gateway_pools(
        self: VcoClientBaseProtocol, request: GetNetworkGatewayPools
    ) -> Sequence[GetNetworkGatewayPoolsResultItem]:
        """Get all gateway pools on the network.

        Returns all gateway pools associated with the specified network. Gateway pools are
        logical groupings of VeloCloud gateways that enable geographic or functional partitioning
        of gateway resources. Pools can optionally include their member gateways and the
        enterprises assigned to each pool. Partners and enterprises are assigned to specific
        gateway pools to control which gateways handle their traffic. Useful for understanding
        the gateway infrastructure topology and pool-to-enterprise/partner mappings.

        Returns:
            Array of gateway pool objects with pool name, member gateways, and enterprise
            assignments
        """
        return await self.portal_list(
            "network/getNetworkGatewayPools",
            request,
            GetNetworkGatewayPoolsResultItem,
        )

    @domain_tag(SYSTEM)
    async def get_network_gateways(
        self: VcoClientBaseProtocol, request: GetNetworkGateways
    ) -> Sequence[GetNetworkGatewaysResultItem]:
        """Get all gateways on the network.

        Returns all VeloCloud gateways associated with the specified network. Gateways are
        infrastructure nodes deployed in cloud PoPs or on-premise locations that provide
        traffic routing, multi-path optimization, and connectivity to Non-VeloCloud Sites for
        SD-WAN edges. Each gateway includes its operational state, IP addresses, software
        version, and pool membership. Useful for monitoring the complete gateway infrastructure,
        checking gateway health, and planning capacity.

        Returns:
            Array of gateway objects with name, IP addresses, state, software version,
            location, and pool membership
        """
        return await self.portal_list(
            "network/getNetworkGateways",
            request,
            GetNetworkGatewaysResultItem,
        )

    @domain_tag(SYSTEM)
    async def get_network_operator_users(
        self: VcoClientBaseProtocol, request: GetNetworkOperatorUsers
    ) -> Sequence[GetNetworkOperatorUsersResultItem]:
        """Get all operator admin users on the network.

        Returns all operator-level administrator users associated with the specified network.
        Operator users have the highest level of access on the VCO and can manage all aspects
        of the platform including gateways, partners, enterprises, system properties, and
        software images. Each user includes username, email, role, and login status. Useful
        for auditing operator-level access and managing platform administrator accounts.

        Returns:
            Array of operator user objects with username, email, role, isActive, and lastLogin
        """
        return await self.portal_list(
            "network/getNetworkOperatorUsers",
            request,
            GetNetworkOperatorUsersResultItem,
        )

    # --- operatorUser/ endpoints ---

    @domain_tag(SYSTEM)
    async def get_operator_user(
        self: VcoClientBaseProtocol, request: GetOperatorUser
    ) -> OperatorUserResult:
        """Get a specific operator admin user by ID or username.

        Retrieves the specified operator user object. Operator users are the highest-privileged
        administrators on the VCO platform with access to manage gateways, partners, enterprises,
        system properties, and platform-wide settings. The user object includes username, email,
        role, login status, and authentication configuration. Useful for inspecting specific
        operator admin accounts and verifying user permissions.

        Returns:
            Operator user object with username, email, role, roleId, isActive, lastLogin,
            and authentication configuration
        """
        return await self.portal(
            "operatorUser/getOperatorUser", request, OperatorUserResult
        )

    @domain_tag(SYSTEM)
    async def get_operator_user_api_tokens(
        self: VcoClientBaseProtocol, request: GetOperatorUserApiTokens
    ) -> Sequence[OperatorUserApiTokenResult]:
        """Get all API tokens owned by a specific operator user.

        Returns all API tokens allocated to the specified operator user. Operator API tokens
        provide programmatic access to the VCO API with operator-level privileges, enabling
        automation of platform management tasks. Each token record includes creation date,
        expiration, and status. Useful for auditing operator-level API access and managing
        token lifecycle for automation accounts.

        Returns:
            Array of API token objects with name, creation date, expiration, and status
        """
        return await self.portal_list(
            "operatorUser/getApiTokens", request, OperatorUserApiTokenResult
        )

    @domain_tag(SYSTEM)
    async def get_operator_users_status(
        self: VcoClientBaseProtocol, request: GetOperatorUsersStatus
    ) -> OperatorUserStatusResult:
        """Get the login and account status for multiple operator users.

        Returns status information for the specified operator users in a single batch request.
        Status includes whether the user account is active, locked, or disabled, as well as
        last login time and session state. Useful for bulk auditing of operator admin account
        status and identifying inactive or locked accounts at the platform level.

        Returns:
            Status information for each specified operator user, including active/locked state,
            last login time, and session details
        """
        return await self.portal(
            "operatorUser/getOperatorUsersStatus",
            request,
            OperatorUserStatusResult,
        )
