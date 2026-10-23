from typing import Annotated, Any

from pydantic import MISSING

from pyvelo.core import Alias, VcoModel


class GetEnterpriseAlertDefinitions(VcoModel):
    enterprise_id: int | MISSING = MISSING


class AlertDefinitionResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    alert_type: str | None | MISSING = MISSING
    name: str | None | MISSING = MISSING
    description: str | None | MISSING = MISSING
    severity: str | None | MISSING = MISSING
    enabled: bool | None | MISSING = MISSING
    parameters: dict[str, Any] | None | MISSING = MISSING


class GetEnterpriseAlertableEvents(VcoModel):
    enterprise_id: int | MISSING = MISSING


class AlertableEventItem(VcoModel):
    event_name: str
    severity: str


class AlertableEventsResult(VcoModel):
    event_list: list[AlertableEventItem]


__all__ = [
    "AlertDefinitionResult",
    "AlertableEventItem",
    "AlertableEventsResult",
    "GetEnterpriseAlertDefinitions",
    "GetEnterpriseAlertableEvents",
]
