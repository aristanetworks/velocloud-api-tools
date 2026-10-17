from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from pyvelo.client.operations._tagging import ADMIN, EDGE, domain_tag
from pyvelo.client.schemas.license import (
    GetEnterpriseEdgeLicenses,
    GetEnterpriseEdgeLicensesResultItem,
)

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class LicenseMixin(Protocol):
    """Operations for license/ API endpoints."""

    @domain_tag(ADMIN, EDGE)
    async def get_enterprise_edge_licenses(
        self: VcoClientBaseProtocol, request: GetEnterpriseEdgeLicenses
    ) -> Sequence[GetEnterpriseEdgeLicensesResultItem]:
        """List edge license assignments and usage for an enterprise.

        Retrieves the license inventory for all Edges in the enterprise, showing each
        Edge's assigned license tier (e.g. Standard, Enterprise, Premium), bandwidth
        allocation, activation date, and expiration. VeloCloud licenses are per-Edge
        and determine the available feature set and maximum throughput. Useful for
        license compliance auditing, identifying Edges approaching license expiry,
        and planning license renewals or tier upgrades.

        Requests include ``with=["counts"]`` by default to return license usage count
        fields, such as ``edge_count`` and ``activated_edge_count``, when supported by
        VCO.

        Returns:
            Array of Edge license records with tier, bandwidth, and validity info
        """
        return await self.portal_list(
            "license/getEnterpriseEdgeLicenses",
            request,
            GetEnterpriseEdgeLicensesResultItem,
        )
