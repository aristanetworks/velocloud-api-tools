"""An enterprise service's usage: the configurations, profiles and Edges that use it."""

import uuid
from typing import Annotated, Any

from pyvelo.core import Alias, ConfigModel, VcoTimestamp


class ServiceConfiguration(ConfigModel):
    bastion_state: str | None = None
    configuration_type: str | None = None
    created: VcoTimestamp | None = None
    description: str | None = None
    effective: VcoTimestamp | None = None
    id_: Annotated[int | None, Alias("id")] = None
    logical_id: uuid.UUID | None = None
    modified: VcoTimestamp | None = None
    module_id: int | None = None
    name: str | None = None
    schema_version: str | None = None
    segment_object_id: int | None = None
    version: str | None = None


class ServiceQosUsage(ConfigModel):
    segment_object_id: int | None = None
    edge_id: int | None = None
    edge_specific_id: int | None = None
    name: str | None = None
    logical_id: uuid.UUID | None = None
    profile_id: int | None = None
    ref: str | dict[str, Any] | None = None


class ServiceEdgeUsage(ConfigModel):
    configuration_id: int | None = None
    edge_specific_id: int | None = None
    name: str | None = None
    logical_id: uuid.UUID | None = None
    profile_id: int | None = None
    segment_object_id: int | None = None
    vnf_status: dict[str, Any] | None = None


class SymantecWssProfileUsage(ConfigModel):
    id_: Annotated[int | None, Alias("id")] = None
    name: str | None = None
    segment_object_id: int | None = None


class SymantecWssLocationUsage(ConfigModel):
    profiles: list[SymantecWssProfileUsage] | None = None
    location_count: int | None = None
    location_job_count_by_status: dict[str, int] | None = None
