from typing import Annotated, Any

from pydantic import model_validator
from pydantic_core import MISSING

from pyvelo.core import Alias, VcoModel, VcoTimestamp


class GetBrandingAsset(VcoModel):
    asset_name: str
    network_id: int | MISSING = MISSING
    enterprise_id: int | MISSING = MISSING
    enterprise_proxy_id: int | MISSING = MISSING


class BrandingAssetResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    asset_name: str | None | MISSING = MISSING
    blob_id: str | None | MISSING = MISSING
    content_type: str | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING

    @model_validator(mode="before")
    @classmethod
    def handle_empty_list(cls, data: Any) -> Any:
        if isinstance(data, list) and len(data) == 0:
            return {}
        return data


class GetBrandingAssetBlob(VcoModel):
    asset_name: str
    blob_id: int


class BrandingAssetBlobResult(VcoModel):
    content: Any | None | MISSING = MISSING
    content_type: str | None | MISSING = MISSING


__all__ = [
    "BrandingAssetBlobResult",
    "BrandingAssetResult",
    "GetBrandingAsset",
    "GetBrandingAssetBlob",
]
