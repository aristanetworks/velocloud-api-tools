from typing import Annotated

from pydantic import MISSING, AwareDatetime, Field

from pyvelo.core import Alias, VcoModel, VcoTimestamp


class GetEnterpriseEdgeLicenses(VcoModel):
    enterprise_id: int | MISSING = MISSING
    with_: Annotated[list[str] | MISSING, Alias("with")] = Field(
        default_factory=lambda: ["counts"]
    )


class LicenseDetails(VcoModel):
    regions: list[str] | None | MISSING = MISSING
    add_ons: list[str] | None | MISSING = MISSING


class GetEnterpriseEdgeLicensesResultItem(VcoModel):
    activated_edge_count: int | None | MISSING = MISSING
    active: int | None | MISSING = MISSING
    alias: str | None | MISSING = MISSING
    bandwidth_tier: str | None | MISSING = MISSING
    created: AwareDatetime | None | MISSING = MISSING
    detail: LicenseDetails | None | MISSING = MISSING
    edge_count: int | None | MISSING = MISSING
    edition: str | None | MISSING = MISSING
    end: VcoTimestamp | None | MISSING = MISSING
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    license_id: int | None | MISSING = MISSING
    modified: AwareDatetime | None | MISSING = MISSING
    name: str | None | MISSING = MISSING
    quota: str | None | MISSING = MISSING
    sku: str | None | MISSING = MISSING
    start: VcoTimestamp | None | MISSING = MISSING
    term_months: int | None | MISSING = MISSING
