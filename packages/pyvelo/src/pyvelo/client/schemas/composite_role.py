from typing import Annotated, Any

from pydantic import MISSING

from pyvelo.core import Alias, VcoModel, VcoTimestamp


class GetCompositeRoleByEnterprise(VcoModel):
    enterprise_id: int | MISSING = MISSING
    user_types: list[str]
    role_ids: list[int] | MISSING = MISSING


class GetCompositeRoleByEnterpriseProxy(VcoModel):
    enterprise_proxy_id: int
    user_types: list[str]
    role_ids: list[int] | MISSING = MISSING


class GetCompositeRoleByOperator(VcoModel):
    network_id: int
    user_types: list[str]
    role_ids: list[int] | MISSING = MISSING


class CompositeRoleResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    description: str | None | MISSING = MISSING
    user_type: str | None | MISSING = MISSING
    privileges: list[dict[str, Any]] | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING


__all__ = [
    "CompositeRoleResult",
    "GetCompositeRoleByEnterprise",
    "GetCompositeRoleByEnterpriseProxy",
    "GetCompositeRoleByOperator",
]
