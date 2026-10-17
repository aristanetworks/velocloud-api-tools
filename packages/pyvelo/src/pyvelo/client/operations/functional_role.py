from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pyvelo.client.schemas.functional_role import (
    FunctionalRoleResult,
    GetFunctionalRoleByEnterprise,
    GetFunctionalRoleByEnterpriseProxy,
    GetFunctionalRoleByOperator,
)

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class FunctionalRoleMixin(Protocol):
    async def get_functional_role_by_enterprise(
        self: VcoClientBaseProtocol, request: GetFunctionalRoleByEnterprise
    ) -> FunctionalRoleResult:
        """Get all functional roles defined for an enterprise.

        Returns all functional roles defined with the specified enterprise context. Functional
        roles represent predefined permission sets that map to specific operational functions
        (e.g., network monitoring, configuration management, security administration). Unlike
        composite roles which are custom-defined, functional roles provide a standardized set
        of permission groupings. Useful for understanding the available role assignments for
        enterprise users and reviewing RBAC options.

        Returns:
            Array of functional role objects with role name, description, user type,
            and associated privilege set
        """
        return await self.portal(
            "functionalRole/getFunctionalRoleByEnterprise",
            request,
            FunctionalRoleResult,
        )

    async def get_functional_role_by_enterprise_proxy(
        self: VcoClientBaseProtocol, request: GetFunctionalRoleByEnterpriseProxy
    ) -> FunctionalRoleResult:
        """Get all functional roles defined for a partner (MSP).

        Returns all functional roles available within the specified partner context. These roles
        define standardized permission sets for partner admin users who manage customer
        enterprises. Useful for understanding the available role assignments for partner users
        and reviewing RBAC options at the MSP level.

        Returns:
            Array of functional role objects with role name, description, user type,
            and associated privilege set
        """
        return await self.portal(
            "functionalRole/getFunctionalRoleByEnterpriseProxy",
            request,
            FunctionalRoleResult,
        )

    async def get_functional_role_by_operator(
        self: VcoClientBaseProtocol, request: GetFunctionalRoleByOperator
    ) -> FunctionalRoleResult:
        """Get all functional roles defined for an operator (network).

        Returns all functional roles available within the specified operator/network context.
        Operator-level functional roles define standardized permission sets for VCO operator
        admin users who manage the entire orchestrator platform, including partners, enterprises,
        and gateways. Useful for understanding the available role assignments for operator
        users and reviewing RBAC options at the platform level.

        Returns:
            Array of functional role objects with role name, description, user type,
            and associated privilege set
        """
        return await self.portal(
            "functionalRole/getFunctionalRoleByOperator",
            request,
            FunctionalRoleResult,
        )
