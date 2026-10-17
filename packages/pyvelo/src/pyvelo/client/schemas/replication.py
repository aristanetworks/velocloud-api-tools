from typing import Annotated, Any

from pydantic_core import MISSING

from pyvelo.client.enums.monitoring import GetReplicationStatusWith
from pyvelo.core import Alias, VcoModel


class GetReplicationStatus(VcoModel):
    enterprise_id: int | MISSING = MISSING
    with_: Annotated[list[GetReplicationStatusWith] | MISSING, Alias("with")] = MISSING


class ReplicationStatusResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    state: str | None | MISSING = MISSING
    lag: int | None | MISSING = MISSING
    health: str | None | MISSING = MISSING
    client_contact_counts: dict[str, Any] | None | MISSING = MISSING
    state_history: list[dict[str, Any]] | None | MISSING = MISSING
    storage: dict[str, Any] | None | MISSING = MISSING


class GetReplicationBlob(VcoModel):
    enterprise_id: int | MISSING = MISSING


class ReplicationBlobResult(VcoModel):
    blob: str | None | MISSING = MISSING
    data: dict[str, Any] | None | MISSING = MISSING


__all__ = [
    "GetReplicationBlob",
    "GetReplicationStatus",
    "ReplicationBlobResult",
    "ReplicationStatusResult",
]
