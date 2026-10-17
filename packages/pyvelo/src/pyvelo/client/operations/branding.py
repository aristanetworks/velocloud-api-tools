from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pyvelo.client.schemas.branding import (
    BrandingAssetBlobResult,
    BrandingAssetResult,
    GetBrandingAsset,
    GetBrandingAssetBlob,
)

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class BrandingMixin(Protocol):
    """Operations for branding/ API endpoints."""

    async def get_branding_asset(
        self: VcoClientBaseProtocol, request: GetBrandingAsset
    ) -> BrandingAssetResult:
        """Get branding asset metadata for the VCO portal UI.

        Retrieves metadata for a specific branding asset used to customize the VeloCloud
        Orchestrator web portal appearance. Branding assets can be configured at operator,
        partner, or enterprise level to white-label the portal with custom logos, company names,
        support contact information, and terms of service. The returned metadata includes a
        blobId that can be used with get_branding_asset_blob to retrieve the actual asset content.
        Useful for reading current branding configuration and verifying white-label customization.

        Returns:
            Branding asset metadata including blobId, asset name, content type, and
            modification timestamp
        """
        return await self.portal(
            "branding/getBrandingAsset", request, BrandingAssetResult
        )

    async def get_branding_asset_blob(
        self: VcoClientBaseProtocol, request: GetBrandingAssetBlob
    ) -> BrandingAssetBlobResult:
        """Get the actual content (blob) of a branding asset.

        Retrieves the binary content of a specific branding asset identified by asset name and
        blob ID. The blob ID is obtained from a prior call to get_branding_asset. For image
        assets (logos, favicons), this returns the image data. For text assets (terms of service,
        company name), this returns the text content. Useful for downloading branding assets
        for backup, migration, or display purposes.

        Returns:
            Branding asset content (binary data for images, text for string assets)
        """
        return await self.portal(
            "branding/getBrandingAssetBlob", request, BrandingAssetBlobResult
        )
