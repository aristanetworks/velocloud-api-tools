from typing import Annotated

from pydantic_core import MISSING

from pyvelo.core import Alias, VcoModel, VcoTimestamp


class GetOperatorUser(VcoModel):
    id_: Annotated[int, Alias("id")]
    username: str | MISSING = MISSING


class OperatorUserResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    username: str | None | MISSING = MISSING
    email: str | None | MISSING = MISSING
    role: str | None | MISSING = MISSING
    role_id: int | None | MISSING = MISSING
    is_active: int | None | MISSING = MISSING
    last_login: VcoTimestamp | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING


class GetOperatorUserApiTokens(VcoModel):
    id_: Annotated[int, Alias("id")]


class OperatorUserApiTokenResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    expiration: VcoTimestamp | None | MISSING = MISSING
    is_active: int | None | MISSING = MISSING


class GetOperatorUsersStatus(VcoModel):
    user_ids: list[int]


class OperatorUserStatusResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    is_active: int | None | MISSING = MISSING
    is_locked: int | None | MISSING = MISSING
    last_login: VcoTimestamp | None | MISSING = MISSING


__all__ = [
    "GetOperatorUser",
    "GetOperatorUserApiTokens",
    "GetOperatorUsersStatus",
    "OperatorUserApiTokenResult",
    "OperatorUserResult",
    "OperatorUserStatusResult",
]
