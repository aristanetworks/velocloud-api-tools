"""The request and result of ``vcoDiagnostics/getVcoDbDiagnostics``."""

from typing import Any, ClassVar

from pydantic import MISSING, ConfigDict

from pyvelo.core import VcoModel


class GetVcoDbDiagnostics(VcoModel):
    pass


class VcoDbDiagnosticsResultItem(VcoModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")
    method: str | None | MISSING = MISSING
    result: dict[str, Any] | list[dict[str, Any]] | None | MISSING = MISSING


class VcoDbDiagnosticsResult(VcoModel):
    health: dict[str, Any] | None | MISSING = MISSING
    table_stats: list[dict[str, Any]] | None | MISSING = MISSING
    replication: dict[str, Any] | None | MISSING = MISSING
    storage: dict[str, Any] | None | MISSING = MISSING


__all__ = [
    "GetVcoDbDiagnostics",
    "VcoDbDiagnosticsResult",
    "VcoDbDiagnosticsResultItem",
]
