from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from pyvelo.client.operations._tagging import SYSTEM, domain_tag
from pyvelo.client.schemas.software_update import (
    GenericSoftwareUpdateResult,
    GetFirmwareUpdatesList,
    GetSoftwareUpdatesList,
)

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class SoftwareUpdateMixin(Protocol):
    """Operations for softwareUpdate/ API endpoints."""

    @domain_tag(SYSTEM)
    async def get_software_updates_list(
        self: VcoClientBaseProtocol, request: GetSoftwareUpdatesList
    ) -> Sequence[GenericSoftwareUpdateResult]:
        """Get all available software image packages for edge updates.

        Returns details of all software images uploaded to the VCO that are available for
        edge software updates. Software images contain the edge operating system and SD-WAN
        application code. Each image includes version information, supported hardware models,
        release notes, and deployment status. Useful for planning edge software upgrades,
        reviewing available versions, and understanding which images are available for deployment.

        Returns:
            Array of software image objects with version, supported models, release date,
            and deployment status
        """
        return await self.portal_paginated_list(
            "softwareUpdate/getSoftwareUpdatesList",
            request,
            GenericSoftwareUpdateResult,
        )

    @domain_tag(SYSTEM)
    async def get_firmware_updates_list(
        self: VcoClientBaseProtocol, request: GetFirmwareUpdatesList
    ) -> Sequence[GenericSoftwareUpdateResult]:
        """Get all available firmware image packages for edge firmware updates.

        Returns details of all firmware images uploaded to the VCO that are available for
        edge firmware updates. Firmware images contain the low-level BIOS/UEFI and hardware
        abstraction layer code, which is separate from the software image. Firmware updates
        are less frequent than software updates and typically address hardware compatibility
        or security issues. Useful for planning firmware upgrades and reviewing available
        firmware versions.

        Returns:
            Array of firmware image objects with version, supported models, release date,
            and deployment status
        """
        return await self.portal_paginated_list(
            "softwareUpdate/getFirmwareUpdatesList",
            request,
            GenericSoftwareUpdateResult,
        )
