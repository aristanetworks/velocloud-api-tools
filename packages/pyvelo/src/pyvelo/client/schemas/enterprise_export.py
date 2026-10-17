from typing import Any

from pydantic_core import MISSING

from pyvelo.core import (
    VcoModel,
    VcoTimestamp,
)

__all__ = [
    "ExportMetadataResult",
    "GetExportMetadata",
    "GetImportResult",
    "ImportResultResult",
]


# --- get_export_metadata (enterpriseExport/getExportMetadata) ---


class GetExportMetadata(VcoModel):
    """"""

    job_id: int
    enterprise_id: int | MISSING = MISSING


class ExportMetadataResult(VcoModel):
    """"""

    job_id: int | None | MISSING = MISSING
    enterprise_id: int | None | MISSING = MISSING
    status: str | None | MISSING = MISSING
    secret_key: str | None | MISSING = MISSING
    file_size: int | None | MISSING = MISSING
    hmac: str | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING


# --- get_import_result (enterpriseImport/getImportResult) ---


class GetImportResult(VcoModel):
    """"""

    job_id: int


class ImportResultResult(VcoModel):
    """"""

    job_id: int | None | MISSING = MISSING
    status: str | None | MISSING = MISSING
    enterprise_id: int | None | MISSING = MISSING
    enterprise_name: str | None | MISSING = MISSING
    data: dict[str, Any] | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING
