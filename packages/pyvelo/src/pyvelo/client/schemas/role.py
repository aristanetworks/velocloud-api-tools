from typing import Annotated, Any

from pydantic import model_validator
from pydantic_core import MISSING

from pyvelo.core import Alias, VcoModel, VcoTimestamp


class GetUserTypeRoles(VcoModel):
    user_type: str = "ENTERPRISE"
    network_id: int | MISSING = MISSING


class RoleResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    description: str | None | MISSING = MISSING
    user_type: str | None | MISSING = MISSING
    privileges: list[dict[str, Any]] | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING


class GetRoleCustomizations(VcoModel):
    enterprise_id: int | MISSING = MISSING
    role_customization_level: str
    network_id: int | MISSING = MISSING
    user_id: int | MISSING = MISSING
    operator_id: int | MISSING = MISSING
    enterprise_proxy_id: int | MISSING = MISSING


class RoleCustomizationResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    level: str | None | MISSING = MISSING
    privileges: list[dict[str, Any]] | None | MISSING = MISSING

    @model_validator(mode="before")
    @classmethod
    def handle_empty_list(cls, data: Any) -> Any:
        if isinstance(data, list) and len(data) == 0:
            return {}
        return data


class GetEligiblePrivilegesForCustomization(VcoModel):
    role_id: int
    application_id: int | MISSING = MISSING


class EligiblePrivilegeResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    description: str | None | MISSING = MISSING
    granted: bool | None | MISSING = MISSING


class GetRoleCustomizationPrivileges(VcoModel):
    id_: Annotated[int, Alias("id")]


class RoleCustomizationPrivilegeResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    action: str | None | MISSING = MISSING
    granted: bool | None | MISSING = MISSING


__all__ = [
    "EligiblePrivilegeResult",
    "GetEligiblePrivilegesForCustomization",
    "GetRoleCustomizationPrivileges",
    "GetRoleCustomizations",
    "GetUserTypeRoles",
    "RoleCustomizationPrivilegeResult",
    "RoleCustomizationResult",
    "RoleResult",
]
