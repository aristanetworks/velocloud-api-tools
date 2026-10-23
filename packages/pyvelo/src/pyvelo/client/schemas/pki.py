from typing import Annotated

from pydantic import MISSING, Field

from pyvelo.core import Alias, VcoModel, VcoTimestamp
from pyvelo.core.fields import Resolvable
from pyvelo.core.identity import EdgeIdentity, GatewayIdentity


class GetCurrentRevokedCertificates(VcoModel):
    after_revocation_date: VcoTimestamp | MISSING = Field(
        MISSING, description="Only return certificates revoked after this date"
    )


class RevokedCertificateResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    serial_number: str | None | MISSING = MISSING
    revocation_date: VcoTimestamp | None | MISSING = MISSING
    reason: str | None | MISSING = MISSING
    device_type: str | None | MISSING = MISSING
    device_id: int | None | MISSING = MISSING


class RevokedCertificatesResult(VcoModel):
    revoked_edges: list[RevokedCertificateResult] | None | MISSING = MISSING
    revoked_gateways: list[RevokedCertificateResult] | None | MISSING = MISSING


class GetEdgeCsr(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")]


class EdgeCsrResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    csr: str | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    signed: bool | None | MISSING = MISSING
    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING


class GetEdgePendingCsrs(VcoModel):
    after: str | MISSING = MISSING
    edge_id: Annotated[int | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING


class PendingCsrResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    csr: str | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    enterprise_id: int | None | MISSING = MISSING


class RenewEdgeCertificate(VcoModel):
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    enterprise_id: int | MISSING = Field(
        MISSING, description="VCO enterprise ID; omit to use the session enterprise"
    )


class RenewEdgeCertificateResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    rows: int | None | MISSING = MISSING
    error: str | None | MISSING = MISSING


class GetGatewayCsr(VcoModel):
    gateway_id: Annotated[int, Resolvable(GatewayIdentity, "id")]


class GatewayCsrResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    csr: str | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    signed: bool | None | MISSING = MISSING
    gateway_id: Annotated[int | None | MISSING, Resolvable(GatewayIdentity, "id")] = (
        MISSING
    )


class GetGatewayPendingCsrs(VcoModel):
    gateway_id: Annotated[int | MISSING, Resolvable(GatewayIdentity, "id")] = MISSING
    after: str | MISSING = MISSING


class GatewayPendingCsrResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    csr: str | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    gateway_id: Annotated[int | None | MISSING, Resolvable(GatewayIdentity, "id")] = (
        MISSING
    )


__all__ = [
    "EdgeCsrResult",
    "GatewayCsrResult",
    "GatewayPendingCsrResult",
    "GetCurrentRevokedCertificates",
    "GetEdgeCsr",
    "GetEdgePendingCsrs",
    "GetGatewayCsr",
    "GetGatewayPendingCsrs",
    "PendingCsrResult",
    "RevokedCertificateResult",
    "RevokedCertificatesResult",
]
