from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pyvelo.client.operations._tagging import SYSTEM, domain_tag
from pyvelo.client.schemas.replication import (
    GetReplicationBlob,
    GetReplicationStatus,
    ReplicationBlobResult,
    ReplicationStatusResult,
)

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class DisasterRecoveryMixin(Protocol):
    """Operations for disasterRecovery/ API endpoints."""

    @domain_tag(SYSTEM)
    async def get_replication_status(
        self: VcoClientBaseProtocol, request: GetReplicationStatus
    ) -> ReplicationStatusResult:
        """Get the VCO disaster recovery replication status.

        Returns the current disaster recovery replication status for the VCO. VeloCloud
        Orchestrators can be configured in active/standby pairs for disaster recovery, where
        the active VCO replicates its database and state to a standby VCO. This method returns
        the replication state, lag, and health information. Optionally includes client contact
        counts, state transition history, and storage information. Useful for monitoring DR
        readiness and troubleshooting replication issues.

        Returns:
            Replication status object with replication state, lag metrics, health indicators,
            and optionally client contact and storage information
        """
        return await self.portal(
            "disasterRecovery/getReplicationStatus", request, ReplicationStatusResult
        )

    @domain_tag(SYSTEM)
    async def get_replication_blob(
        self: VcoClientBaseProtocol, request: GetReplicationBlob
    ) -> ReplicationBlobResult:
        """Get the blob needed to configure VCO replication on the standby orchestrator.

        Returns the replication configuration blob from the active orchestrator that is needed
        to set up replication on the standby VCO. This method is used during disaster recovery
        configuration when the active VCO's configureActiveForReplication was called with
        autoConfigStandby set to false. The blob contains the necessary connection parameters
        and credentials for the standby to connect to the active VCO for replication. Useful
        during manual DR setup procedures.

        Returns:
            Replication configuration blob containing connection parameters and credentials
            for standby VCO setup
        """
        return await self.portal(
            "disasterRecovery/getReplicationBlob", request, ReplicationBlobResult
        )
