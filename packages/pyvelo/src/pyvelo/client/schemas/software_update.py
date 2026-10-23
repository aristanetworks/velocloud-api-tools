from typing import Annotated, Any

from pydantic import MISSING

from pyvelo.core import Alias, VcoModel
from pyvelo.core.base import PaginatedVcoModel, Tinyint


class GetSoftwareUpdatesList(PaginatedVcoModel):
    with_: Annotated[list[str] | MISSING, Alias("with")] = MISSING
    update_type: list[str] | MISSING = MISSING
    limit: int | MISSING = MISSING
    quick_search: dict[str, Any] | MISSING = MISSING
    filters: dict[str, Any] | MISSING = MISSING
    sort_by: dict[str, Any] | MISSING = MISSING
    count: Annotated[bool | MISSING, Alias("_count")] = MISSING
    filter_spec: Annotated[bool | MISSING, Alias("_filterSpec")] = MISSING


class GetFirmwareUpdatesList(PaginatedVcoModel):
    with_: Annotated[list[str] | MISSING, Alias("with")] = MISSING
    update_type: list[str] | MISSING = MISSING
    limit: int | MISSING = MISSING
    quick_search: dict[str, Any] | MISSING = MISSING
    filters: dict[str, Any] | MISSING = MISSING
    sort_by: dict[str, Any] | MISSING = MISSING
    count: Annotated[bool | MISSING, Alias("_count")] = MISSING
    filter_spec: Annotated[bool | MISSING, Alias("_filterSpec")] = MISSING


class GenericSoftwareUpdateResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    version: str | None | MISSING = MISSING
    build_number: str | None | MISSING = MISSING
    device_family: str | None | MISSING = MISSING
    device_category: str | None | MISSING = MISSING
    deprecated: Tinyint | None | MISSING = MISSING


__all__ = [
    "GenericSoftwareUpdateResult",
    "GetFirmwareUpdatesList",
    "GetSoftwareUpdatesList",
]
