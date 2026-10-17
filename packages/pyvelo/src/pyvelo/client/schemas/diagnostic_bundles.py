from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field
from pydantic_core import MISSING

from pyvelo.client.enums.common import PendingActionStatus
from pyvelo.client.enums.diagnostic_bundles import DiagnosticBundleType
from pyvelo.core import Alias, VcoModel, VcoTimestamp
from pyvelo.core.fields import Resolvable
from pyvelo.core.identity import EdgeIdentity

__all__ = [
    "DeleteDiagnosticBundle",
    "DeleteDiagnosticBundleResult",
    "DiagnosticBundle",
    "DiagnosticBundleType",
    "DiagnosticDumpOptions",
    "GetDiagnosticBundle",
    "GetDiagnosticBundles",
    "InsertDiagnosticBundle",
    "InsertDiagnosticBundleResult",
    "PacketCaptureOptions",
    "UpdateDiagnosticBundle",
    "UpdateDiagnosticBundleFields",
    "UpdateDiagnosticBundleResult",
]


class DiagnosticDumpOptions(VcoModel):
    type: Literal["diagnosticDump"] = "diagnosticDump"
    max_cores: int | MISSING = MISSING


class PacketCaptureOptions(VcoModel):
    type: Literal["packetCapture"] = "packetCapture"
    interface: str
    duration: str | MISSING = MISSING
    role: str | MISSING = MISSING
    filter: str | MISSING = MISSING


class InsertDiagnosticBundle(VcoModel):
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")]
    enterprise_id: int | MISSING = MISSING
    reason: str | MISSING = MISSING
    options: DiagnosticDumpOptions | PacketCaptureOptions | MISSING = MISSING


class InsertDiagnosticBundleResult(VcoModel):
    id_: Annotated[int, Alias("id")]


class GetDiagnosticBundle(VcoModel):
    id_: Annotated[int, Alias("id")]
    enterprise_id: int | MISSING = MISSING


class GetDiagnosticBundles(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    sort: Annotated[
        Literal["asc", "desc"],
        Field(
            default="desc",
            description=(
                "Client-side sort direction by created timestamp. "
                "'desc' (default) returns newest bundles first. Not sent to VCO."
            ),
            exclude=True,
        ),
    ] = "desc"
    count: Annotated[
        int,
        Field(
            default=20,
            ge=1,
            description=(
                "Client-side cap on how many bundles to return after sorting. "
                "Defaults to 20. Not sent to VCO — VCO always returns the full list."
            ),
            exclude=True,
        ),
    ] = 20


class DiagnosticBundle(VcoModel):
    id_: Annotated[int, Alias("id")]
    request_id: Annotated[str | None | MISSING, Alias("requestId")] = MISSING
    user_id: str | None | MISSING = MISSING
    edge_id: Annotated[int | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    edge_name: str | None | MISSING = MISSING
    enterprise_id: int | MISSING = MISSING
    created: VcoTimestamp | MISSING = MISSING
    last_updated: Annotated[VcoTimestamp | MISSING, Alias("lastModified")] = MISSING
    timestamp: VcoTimestamp | MISSING = MISSING
    status: PendingActionStatus | MISSING = MISSING
    edge_response: dict[str, Any] | None | MISSING = MISSING
    download_url: str | None | MISSING = MISSING
    download_token: str | None | MISSING = MISSING
    response_data: str | dict[str, Any] | None | MISSING = MISSING
    reason: str | None | MISSING = MISSING
    upload_details: dict[str, Any] | None | MISSING = MISSING
    type: str | None | MISSING = MISSING


class DeleteDiagnosticBundle(VcoModel):
    id_: Annotated[int, Alias("id")]
    enterprise_id: int | MISSING = MISSING


class DeleteDiagnosticBundleResult(VcoModel):
    rows: int


class UpdateDiagnosticBundleFields(VcoModel):
    age_out_time: datetime | None


class UpdateDiagnosticBundle(VcoModel):
    id_: Annotated[int, Alias("id")]
    enterprise_id: int | MISSING = MISSING
    update: Annotated[UpdateDiagnosticBundleFields, Alias("_update")]


class UpdateDiagnosticBundleResult(VcoModel):
    rows: int
