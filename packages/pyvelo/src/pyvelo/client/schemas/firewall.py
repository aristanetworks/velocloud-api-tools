import uuid
from typing import Annotated, Any, Self

from pydantic import ConfigDict, RootModel, model_validator
from pydantic_core import MISSING

from pyvelo.client.enums.firewall import GetFirewallLogsWith
from pyvelo.core import Alias, VcoModel, VcoTimestamp
from pyvelo.core.base import ApiInterval, PaginatedVcoModel
from pyvelo.core.fields import Resolvable
from pyvelo.core.identity import EdgeIdentity, SegmentIdentity

__all__ = [
    "GetEnterpriseFirewallLogs",
    "GetEnterpriseFirewallLogsResult",
    "GetEnterpriseFirewallRules",
    "GetEnterpriseFirewallRulesResult",
    "GetMaliciousIpCategories",
    "GetMaliciousIpCategoriesResult",
    "GetUrlFilteringCategories",
    "GetUrlFilteringCategoriesResult",
]


class GetEnterpriseFirewallLogsInterval(VcoModel):
    start: int
    end: int


class GetEnterpriseFirewallLogsFilter(VcoModel):
    limit: int = 100


class GetEnterpriseFirewallLogs(VcoModel):
    enterprise_id: int | MISSING = MISSING
    interval: GetEnterpriseFirewallLogsInterval
    filter: GetEnterpriseFirewallLogsFilter | MISSING = MISSING
    edge_id: Annotated[int | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    rules: list[str] | MISSING = MISSING
    source_ips: list[str] | MISSING = MISSING
    dest_ips: list[str] | MISSING = MISSING
    edges: list[int] | MISSING = MISSING
    with_: Annotated[list[GetFirewallLogsWith] | MISSING, Alias("with")] = MISSING


class GetEnterpriseFirewallLogsResult(VcoModel):
    data: list[dict[str, Any]] | None | MISSING = MISSING
    meta_data: dict[str, Any] | None | MISSING = MISSING
    rules: dict[str, Any] | None | MISSING = MISSING


class GetEnterpriseFirewallLogsList(PaginatedVcoModel):
    enterprise_id: int | MISSING = MISSING
    interval: ApiInterval
    limit: int | MISSING = MISSING


class GetEnterpriseFirewallLogsListItem(VcoModel):
    model_config = ConfigDict(extra="allow")


class GetEnterpriseFirewallRules(VcoModel):
    enterprise_id: int | MISSING = MISSING
    rule_logical_ids: list[str] | MISSING = MISSING
    rule_names: list[str] | MISSING = MISSING

    @model_validator(mode="after")
    def _require_ids_or_names(self) -> Self:
        if self.rule_logical_ids is MISSING and self.rule_names is MISSING:
            raise ValueError(
                "At least one of rule_logical_ids or rule_names is required"
            )
        return self


class FirewallRuleEntry(VcoModel):
    """A single enterprise firewall rule."""

    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    deactivated: VcoTimestamp | None | MISSING = MISSING
    logical_id: str | None | MISSING = MISSING
    type_: Annotated[str | None | MISSING, Alias("type")] = MISSING
    name: str | None | MISSING = MISSING
    data: str | None | MISSING = MISSING
    edge_logical_id: Annotated[
        uuid.UUID | None | MISSING, Resolvable(EdgeIdentity, "logical_id")
    ] = MISSING
    enterprise_id: int | None | MISSING = MISSING
    segment_object_id: Annotated[
        int | None | MISSING, Resolvable(SegmentIdentity, "object_id")
    ] = MISSING
    segment_name: str | None | MISSING = MISSING


class GetEnterpriseFirewallRulesResult(VcoModel):
    """Enterprise firewall rules retrieved by logical ID or name."""

    rules: list[FirewallRuleEntry] | None | MISSING = MISSING


class GetMaliciousIpCategories(VcoModel):
    pass


GetMaliciousIpCategoriesResult = RootModel[dict[str, int]]


class GetUrlFilteringCategories(VcoModel):
    enterprise_id: int | MISSING = MISSING


class GetUrlFilteringCategoriesResultItem(VcoModel):
    category_id: Annotated[int, Alias("catid")]
    category_name: Annotated[str, Alias("catname")]
    category_group: Annotated[str, Alias("catgroup")]


GetUrlFilteringCategoriesResult = RootModel[
    dict[int, GetUrlFilteringCategoriesResultItem]
]
