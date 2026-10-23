from typing import Annotated, Any

from pydantic import MISSING

from pyvelo.core import Alias, VcoModel, VcoTimestamp


class GetFunctionalRoleByEnterprise(VcoModel):
    enterprise_id: int | MISSING = MISSING
    user_type: str
    exclude_super_roles: bool | MISSING = MISSING


class GetFunctionalRoleByEnterpriseProxy(VcoModel):
    enterprise_proxy_id: int
    user_type: str
    exclude_super_roles: bool | MISSING = MISSING


class GetFunctionalRoleByOperator(VcoModel):
    network_id: int
    user_type: str
    exclude_super_roles: bool | MISSING = MISSING


class FunctionalRoleResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    description: str | None | MISSING = MISSING
    user_type: str | None | MISSING = MISSING
    privileges: list[dict[str, Any]] | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING


__all__ = [
    "FunctionalRoleResult",
    "GetFunctionalRoleByEnterprise",
    "GetFunctionalRoleByEnterpriseProxy",
    "GetFunctionalRoleByOperator",
]
