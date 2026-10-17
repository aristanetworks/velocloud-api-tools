from typing import Annotated, Any

from pydantic_core import MISSING

from pyvelo.core import Alias, VcoModel


class GetPendingInventory(VcoModel):
    enterprise_proxy_id: int | MISSING = MISSING
    enterprise_id: int | MISSING = MISSING
    serial_number: str | MISSING = MISSING


class PendingInventoryResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    model: str | None | MISSING = MISSING
    serial_number: str | None | MISSING = MISSING
    activation_key: str | None | MISSING = MISSING
    status: str | None | MISSING = MISSING
    enterprise_id: int | None | MISSING = MISSING
    enterprise_proxy_id: int | None | MISSING = MISSING
    data: dict[str, Any] | None | MISSING = MISSING


__all__ = [
    "GetPendingInventory",
    "PendingInventoryResult",
]
