from typing import Annotated

from pydantic_core import MISSING

from pyvelo.core import Alias, VcoModel
from pyvelo.core.fields import Resolvable
from pyvelo.core.identity import EdgeIdentity, GatewayIdentity, SegmentIdentity

__all__ = [
    "GetGatewayEdgeAssignments",
    "GetGatewayEdgeAssignmentsResultItem",
    "GetGatewayProperties",
    "GetGatewayPropertiesResultItem",
    "GwRouteTableEntry",
]


class GwRouteTableEntry(VcoModel):
    network_addr: str
    network_mask: str
    type: str
    peer_name: str
    reachable: bool
    metric: int
    preference: int
    flags: str
    age: int
    c_tag: int
    s_tag: int
    handoff: str
    mode: str
    lost_reason: str


class GetGatewayEdgeAssignments(VcoModel):
    gateway_id: Annotated[int, Resolvable(GatewayIdentity, "id")]
    enterprise_proxy_id: int | MISSING = MISSING
    enterprise_id: int | MISSING = MISSING


class GetGatewayEdgeAssignmentsResultItem(VcoModel):
    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    edge_name: str | None | MISSING = MISSING
    enterprise_id: int | None | MISSING = MISSING
    enterprise_name: str | None | MISSING = MISSING
    assignment_type: str | None | MISSING = MISSING
    segment_id: Annotated[
        int | None | MISSING, Resolvable(SegmentIdentity, "segment_id")
    ] = MISSING


class GetGatewayProperties(VcoModel):
    gateway_id: Annotated[int, Resolvable(GatewayIdentity, "id")]
    name: str | MISSING = MISSING
    id_: Annotated[int | MISSING, Alias("id")] = MISSING
    is_read_only: bool | MISSING = MISSING


class GetGatewayPropertiesResultItem(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    value: str | None | MISSING = MISSING
    data_type: str | None | MISSING = MISSING
    is_read_only: bool | None | MISSING = MISSING
