from typing import Annotated, Any

from pydantic import MISSING

from pyvelo.core import Alias, VcoModel, VcoTimestamp


class GetSystemProperties(VcoModel):
    pass


class SystemPropertyResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    value: str | None | MISSING = MISSING
    data_type: str | None | MISSING = MISSING
    default_value: str | None | MISSING = MISSING
    description: str | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING


class GetSystemProperty(VcoModel):
    name: str
    network_id: int | MISSING = MISSING
    id_: Annotated[int | MISSING, Alias("id")] = MISSING


class SystemPropertyDetailResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    value: str | None | MISSING = MISSING
    data_type: str | None | MISSING = MISSING
    default_value: str | None | MISSING = MISSING
    description: str | None | MISSING = MISSING


class GetVersionInfo(VcoModel):
    pass


class VersionInfoResult(VcoModel):
    version: str | None | MISSING = MISSING
    build: str | None | MISSING = MISSING
    release: str | None | MISSING = MISSING
    data: dict[str, Any] | None | MISSING = MISSING


__all__ = [
    "GetSystemProperties",
    "GetSystemProperty",
    "GetVersionInfo",
    "SystemPropertyDetailResult",
    "SystemPropertyResult",
    "VersionInfoResult",
]
