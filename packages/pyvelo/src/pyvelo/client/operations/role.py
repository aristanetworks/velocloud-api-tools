from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from pyvelo.client.schemas.role import (
    EligiblePrivilegeResult,
    GetEligiblePrivilegesForCustomization,
    GetRoleCustomizationPrivileges,
    GetRoleCustomizations,
    GetUserTypeRoles,
    RoleCustomizationPrivilegeResult,
    RoleCustomizationResult,
    RoleResult,
)

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class RoleMixin(Protocol):
    async def get_user_type_roles(
        self: VcoClientBaseProtocol, request: GetUserTypeRoles
    ) -> Sequence[RoleResult]:
        """Get all defined roles for a specific user type.

        Returns all roles defined for the specified user type (ENTERPRISE, OPERATOR, or MSP).
        Roles define the set of permissions available to users of that type. Built-in roles
        include Admin, Read-Only, and various specialized roles. Each role specifies which API
        methods and UI features the user can access. Useful for understanding the RBAC model,
        listing available roles when creating or updating users, and auditing role definitions.

        Returns:
            Array of role objects with role name, ID, description, and privilege set
            for the specified user type
        """
        return await self.portal_list("role/getUserTypeRoles", request, RoleResult)

    async def get_role_customizations(
        self: VcoClientBaseProtocol, request: GetRoleCustomizations
    ) -> RoleCustomizationResult:
        """Get all role customization packages at the enterprise level.

        Returns the list of all role customizations present in the system for the enterprise
        level. Role customizations allow administrators to modify the default privilege sets
        of built-in roles, creating customized permission packages that can be applied to
        specific roles. This supports fine-grained access control beyond what built-in roles
        provide. Can also query at NETWORK, PARTNER, or ALL levels. Useful for auditing
        RBAC customizations and understanding non-default permission configurations.

        Returns:
            Array of role customization package objects with customization name, level,
            and modified privileges
        """
        return await self.portal(
            "role/getRoleCustomizations", request, RoleCustomizationResult
        )

    async def get_eligible_privileges_for_customization(
        self: VcoClientBaseProtocol, request: GetEligiblePrivilegesForCustomization
    ) -> EligiblePrivilegeResult:
        """Get all privileges that can be customized for a specific role.

        Returns the list of privileges that are eligible for customization for the given role.
        Not all privileges can be modified for every role -- this method identifies which
        permissions can be added to or removed from the specified role through role customization.
        Useful for building role customization interfaces and understanding which permissions
        can be adjusted for a particular role.

        Returns:
            Array of eligible privilege objects with privilege name, description, and
            current grant status
        """
        return await self.portal(
            "role/getEligiblePrivilegesForCustomization",
            request,
            EligiblePrivilegeResult,
        )

    async def get_role_customization_privileges(
        self: VcoClientBaseProtocol, request: GetRoleCustomizationPrivileges
    ) -> RoleCustomizationPrivilegeResult:
        """Get the privilege assignments within a role customization package.

        Returns the specific privilege grants and denials defined in a role customization
        package. This shows which privileges have been added to or removed from the base role
        definition through customization. Useful for understanding exactly how a role has been
        modified from its default privilege set and auditing custom RBAC configurations.

        Returns:
            Array of privilege objects with privilege name, action (READ/CREATE/UPDATE/DELETE),
            and grant status within the customization package
        """
        return await self.portal(
            "role/getRoleCustomizationPrivileges",
            request,
            RoleCustomizationPrivilegeResult,
        )
