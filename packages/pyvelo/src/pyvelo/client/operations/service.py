from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pyvelo.client.operations._tagging import CONFIG, domain_tag
from pyvelo.client.schemas.enterprise import (
    EnterpriseServiceLicenseResult,
    GetEnterpriseServiceLicense,
)

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class ServiceMixin(Protocol):
    """Operations for service/ API endpoints."""

    @domain_tag(CONFIG)
    async def get_enterprise_service_license(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseServiceLicense,
    ) -> EnterpriseServiceLicenseResult:
        """Get the service license information for an enterprise.

        Returns the service license details for the specified enterprise, including the license
        type (e.g., Standard, Advanced, Premium), license expiration date, licensed edge count,
        active edge count, and feature entitlements. The service license determines which SD-WAN
        features are available to the enterprise, such as enhanced firewall, URL filtering, IDS/IPS,
        and advanced analytics. Useful for license compliance auditing, capacity planning, and
        verifying feature entitlements.

        Returns:
            Service license object with license type, edition, expiration date, licensed/active
            edge counts, bandwidth tier, and feature entitlement details
        """
        return await self.portal(
            "service/getEnterpriseServiceLicense",
            request,
            EnterpriseServiceLicenseResult,
        )
