from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from pyvelo.client.schemas.migration import (
    GetMigrationActions,
    GetMigrationGateways,
    MigrationActionResult,
    MigrationGatewayResult,
)

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class MigrationMixin(Protocol):
    """Operations for migration/ API endpoints."""

    async def get_migration_actions(
        self: VcoClientBaseProtocol, request: GetMigrationActions
    ) -> Sequence[MigrationActionResult]:
        """Get migration actions from migration entity records for an enterprise.

        Returns pending and completed migration actions filtered by enterprise ID. Migration
        actions track operations such as EDGE_REBALANCE (redistributing edges across gateways),
        ENTERPRISE_REBALANCE (rebalancing an enterprise's gateway assignments), and
        DATACENTER_SWITCH_GATEWAY (switching an enterprise's datacenter gateway). These actions
        are generated during gateway maintenance, capacity planning, or infrastructure changes.
        Useful for monitoring migration progress and understanding pending infrastructure changes.

        Returns:
            Array of migration action records with action type, status, affected entities,
            and timestamps
        """
        return await self.portal_list(
            "migration/getMigrationActions", request, MigrationActionResult
        )

    async def get_migration_gateways(
        self: VcoClientBaseProtocol, request: GetMigrationGateways
    ) -> Sequence[MigrationGatewayResult]:
        """Get NSD migration gateways for an enterprise.

        Returns the list of gateways involved in Non-SD-WAN Destination (NSD) migration for
        the specified enterprise. NSD migrations occur when traffic routing through gateways
        to third-party services needs to be moved from one gateway to another, typically during
        gateway maintenance or capacity rebalancing. Useful for identifying which gateways are
        participating in NSD service migrations and tracking migration readiness.

        Returns:
            Array of gateway objects involved in NSD migration with gateway identity and
            migration status
        """
        return await self.portal_list(
            "migration/getMigrationGateways", request, MigrationGatewayResult
        )
