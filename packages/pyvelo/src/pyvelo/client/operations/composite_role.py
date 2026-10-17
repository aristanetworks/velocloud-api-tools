from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pyvelo.client.schemas.composite_role import (
    CompositeRoleResult,
    GetCompositeRoleByEnterprise,
    GetCompositeRoleByEnterpriseProxy,
    GetCompositeRoleByOperator,
)

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class CompositeRoleMixin(Protocol):
    async def get_composite_role_by_enterprise(
        self: VcoClientBaseProtocol, request: GetCompositeRoleByEnterprise
    ) -> CompositeRoleResult:
        """Get all composite roles defined for an enterprise.

        Returns all composite roles defined with the specified enterprise context. Composite
        roles are custom role definitions that combine multiple fine-grained privileges into
        a single assignable role. They extend the built-in role system (Admin, Read-Only, etc.)
        with enterprise-specific role definitions that can precisely control access to features
        like edge management, configuration, monitoring, and alerting. Useful for auditing
        custom role definitions and understanding the enterprise's RBAC configuration.

        Returns:
            Array of composite role objects with role name, description, user type,
            and associated privileges
        """
        return await self.portal(
            "compositeRole/getCompositeRoleByEnterprise",
            request,
            CompositeRoleResult,
        )

    async def get_composite_role_by_enterprise_proxy(
        self: VcoClientBaseProtocol, request: GetCompositeRoleByEnterpriseProxy
    ) -> CompositeRoleResult:
        """Get all composite roles defined for a partner (MSP).

        Returns all composite roles defined within the specified partner context. Composite
        roles for partners control what partner admin users can do across their managed
        enterprises, such as provisioning new customers, managing edge configurations, or
        viewing monitoring data. Useful for auditing partner-level RBAC configuration and
        understanding custom role definitions.

        Returns:
            Array of composite role objects with role name, description, user type,
            and associated privileges
        """
        return await self.portal(
            "compositeRole/getCompositeRoleByEnterpriseProxy",
            request,
            CompositeRoleResult,
        )

    async def get_composite_role_by_operator(
        self: VcoClientBaseProtocol, request: GetCompositeRoleByOperator
    ) -> CompositeRoleResult:
        """Get all composite roles defined for an operator (network).

        Returns all composite roles defined within the specified operator/network context.
        Operator-level composite roles control what operator admin users can do across the
        entire VCO, including managing partners, enterprises, gateways, and system-level
        settings. Useful for auditing operator-level RBAC configuration and understanding
        custom administrative role definitions.

        Returns:
            Array of composite role objects with role name, description, user type,
            and associated privileges
        """
        return await self.portal(
            "compositeRole/getCompositeRoleByOperator",
            request,
            CompositeRoleResult,
        )
