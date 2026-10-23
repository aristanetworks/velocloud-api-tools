from typing import Annotated, Any

from pydantic import MISSING

from pyvelo.core import (
    Alias,
    VcoModel,
    VcoTimestamp,
)

__all__ = [
    "EnterpriseUserApiTokenItem",
    "EnterpriseUserResult",
    "EnterpriseUsersStatusResult",
    "GetEnterpriseUser",
    "GetEnterpriseUserApiTokens",
    "GetEnterpriseUsersStatus",
]


# --- get_enterprise_user (enterpriseUser/getEnterpriseUser) ---


class GetEnterpriseUser(VcoModel):

    id_: Annotated[int, Alias("id")]
    username: str | MISSING = MISSING


class EnterpriseUserResult(VcoModel):

    id_: Annotated[int, Alias("id")]
    created: VcoTimestamp | None | MISSING = MISSING
    enterprise_id: int | None | MISSING = MISSING
    username: str | None | MISSING = MISSING
    email: str | None | MISSING = MISSING
    first_name: str | None | MISSING = MISSING
    last_name: str | None | MISSING = MISSING
    role_id: int | None | MISSING = MISSING
    role_name: str | None | MISSING = MISSING
    is_active: int | None | MISSING = MISSING
    is_locked: int | None | MISSING = MISSING
    is_native: int | None | MISSING = MISSING
    last_login: VcoTimestamp | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING
    user_type: str | None | MISSING = MISSING
    domain: str | None | MISSING = MISSING
    access_level: str | None | MISSING = MISSING


# --- get_enterprise_user_api_tokens (enterpriseUser/getApiTokens) ---


class GetEnterpriseUserApiTokens(VcoModel):

    id_: Annotated[int, Alias("id")]


class EnterpriseUserApiTokenItem(VcoModel):

    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    expiration: VcoTimestamp | None | MISSING = MISSING
    is_active: int | None | MISSING = MISSING
    user_id: int | None | MISSING = MISSING


# --- get_enterprise_users_status (enterpriseUser/getEnterpriseUsersStatus) ---


class GetEnterpriseUsersStatus(VcoModel):

    user_ids: list[int]


class EnterpriseUsersStatusResult(VcoModel):

    data: dict[str, Any] | None | MISSING = MISSING
