from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from pyvelo.client.operations._tagging import SYSTEM, domain_tag
from pyvelo.client.schemas.system import (
    GetSystemProperties,
    GetSystemProperty,
    GetVersionInfo,
    SystemPropertyDetailResult,
    SystemPropertyResult,
    VersionInfoResult,
)
from pyvelo.client.schemas.vco_diagnostics import (
    GetVcoDbDiagnostics,
    VcoDbDiagnosticsResultItem,
)
from pyvelo.client.schemas.vco_inventory import (
    GetPendingInventory,
    PendingInventoryResult,
)

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class SystemMixin(Protocol):
    """Operations for system/, systemProperty/, vcoDiagnostics/, and vcoInventory/ API endpoints."""

    # --- systemProperty/ endpoints ---

    @domain_tag(SYSTEM)
    async def get_system_properties(
        self: VcoClientBaseProtocol, request: GetSystemProperties
    ) -> Sequence[SystemPropertyResult]:
        """Get all VCO system properties.

        Returns the complete set of system-level properties configured on the VeloCloud
        Orchestrator. System properties are global key-value settings that control VCO behavior,
        feature flags, default values, and operational parameters across all enterprises and
        partners. Examples include session timeout, maximum API request sizes, feature toggles,
        and default configuration values. Useful for understanding VCO-wide settings and
        troubleshooting system-level behavior.

        Returns:
            Array of system property objects, each with name, value, dataType, defaultValue,
            and description
        """
        return await self.portal_list(
            "systemProperty/getSystemProperties",
            request,
            SystemPropertyResult,
        )

    @domain_tag(SYSTEM)
    async def get_system_property(
        self: VcoClientBaseProtocol, request: GetSystemProperty
    ) -> SystemPropertyDetailResult:
        """Get a specific VCO system property by name or ID.

        Retrieves a single system property identified by name or object ID. System properties
        are global VCO-level settings that control platform behavior, feature availability,
        and default configuration values. Each property has a name, value, data type, and
        default value. Useful for querying specific VCO settings without fetching the entire
        property list.

        Returns:
            System property object with name, value, dataType, defaultValue, and description
        """
        return await self.portal(
            "systemProperty/getSystemProperty",
            request,
            SystemPropertyDetailResult,
        )

    # --- system/ endpoints ---

    @domain_tag(SYSTEM)
    async def get_version_info(
        self: VcoClientBaseProtocol, request: GetVersionInfo
    ) -> VersionInfoResult:
        """Get the VeloCloud Orchestrator software version information.

        Returns the system version information for the VCO, including the software version
        number, build number, and release details. Useful for verifying which version of the
        orchestrator software is running, checking compatibility with edge software versions,
        and documenting the deployment for support purposes.

        Returns:
            Version information object with software version, build number, and release details
        """
        return await self.portal("system/getVersionInfo", request, VersionInfoResult)

    # --- vcoDiagnostics/ endpoints ---

    @domain_tag(SYSTEM)
    async def get_vco_db_diagnostics(
        self: VcoClientBaseProtocol, request: GetVcoDbDiagnostics
    ) -> Sequence[VcoDbDiagnosticsResultItem]:
        """Get diagnostic information about the VCO database.

        Returns diagnostic data about the VeloCloud Orchestrator's database, including
        database health metrics, table sizes, replication status, query performance statistics,
        and storage utilization. This is an operator-level diagnostic tool for monitoring the
        VCO's backend database infrastructure. Useful for troubleshooting VCO performance issues,
        monitoring database growth, and assessing database health during operational reviews.

        Returns:
            Array of database diagnostic results with health metrics, table statistics,
            replication status, and storage utilization data
        """
        return await self.portal_list(
            "vcoDiagnostics/getVcoDbDiagnostics",
            request,
            VcoDbDiagnosticsResultItem,
        )

    # --- vcoInventory/ endpoints ---

    @domain_tag(SYSTEM)
    async def get_pending_inventory(
        self: VcoClientBaseProtocol, request: GetPendingInventory
    ) -> PendingInventoryResult:
        """Get pending edge inventory from Maestro awaiting assignment.

        Returns edge appliances that have been registered in the Maestro inventory system but
        have not yet been assigned to a partner or enterprise. These are new or unprovisioned
        edge devices that are available for deployment. The inventory is retrieved using Partner
        or Enterprise ID and stored in the VCO database with a "new" state. Useful for viewing
        available hardware inventory, planning new edge deployments, and managing the provisioning
        pipeline for new edge appliances.

        Returns:
            Array of pending inventory items with edge model, serial number, activation key,
            and registration status
        """
        return await self.portal(
            "vcoInventory/getPendingInventory",
            request,
            PendingInventoryResult,
        )
