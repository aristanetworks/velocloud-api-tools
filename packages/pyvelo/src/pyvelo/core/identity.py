"""Identity key types and dataclasses for typed ID resolution.

Key types (SegmentId, SegmentName, etc.) are frozen dataclasses used as
typed lookup keys for the resolver system. Each key type declares its
associated identity type via a ClassVar.

Identity dataclasses (SegmentIdentity, etc.) hold all resolved fields
for an entity, using plain int/uuid.UUID types.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar, NewType

from pydantic import BeforeValidator, PlainSerializer

from pyvelo.core.enums import ModelNumber, NetworkSegmentType

# ---------------------------------------------------------------------------
# Segment
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SegmentIdentity:
    name: str
    segment_id: int
    object_id: int
    logical_id: uuid.UUID
    type_: NetworkSegmentType = NetworkSegmentType.REGULAR


@dataclass(frozen=True)
class SegmentId:
    value: int
    identity_type: ClassVar[type[SegmentIdentity]] = SegmentIdentity


@dataclass(frozen=True)
class SegmentObjectId:
    value: int
    identity_type: ClassVar[type[SegmentIdentity]] = SegmentIdentity


@dataclass(frozen=True)
class SegmentLogicalId:
    value: uuid.UUID
    identity_type: ClassVar[type[SegmentIdentity]] = SegmentIdentity


@dataclass(frozen=True)
class SegmentName:
    value: str
    identity_type: ClassVar[type[SegmentIdentity]] = SegmentIdentity


# ---------------------------------------------------------------------------
# Edge
# ---------------------------------------------------------------------------


DeviceFamily = NewType("DeviceFamily", str)
"""Profile model class key (e.g. ``"edge6X0"``, ``"virtual"``)."""


@dataclass(frozen=True)
class EdgeIdentity:
    name: str
    id: int
    logical_id: uuid.UUID
    model_number: ModelNumber | None = None
    device_family: DeviceFamily | None = None


@dataclass(frozen=True)
class EdgeId:
    value: int
    identity_type: ClassVar[type[EdgeIdentity]] = EdgeIdentity


@dataclass(frozen=True)
class EdgeLogicalId:
    value: uuid.UUID
    identity_type: ClassVar[type[EdgeIdentity]] = EdgeIdentity


@dataclass(frozen=True)
class EdgeName:
    value: str
    identity_type: ClassVar[type[EdgeIdentity]] = EdgeIdentity


# ---------------------------------------------------------------------------
# Link
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LinkIdentity:
    name: str
    id: int
    logical_id: str
    internal_id: uuid.UUID
    edge_id: int


@dataclass(frozen=True)
class LinkId:
    value: int
    identity_type: ClassVar[type[LinkIdentity]] = LinkIdentity


@dataclass(frozen=True)
class LinkLogicalId:
    value: str
    identity_type: ClassVar[type[LinkIdentity]] = LinkIdentity


@dataclass(frozen=True)
class LinkInternalId:
    value: uuid.UUID
    identity_type: ClassVar[type[LinkIdentity]] = LinkIdentity


@dataclass(frozen=True)
class LinkName:
    value: str
    identity_type: ClassVar[type[LinkIdentity]] = LinkIdentity


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GatewayIdentity:
    name: str
    id: int
    logical_id: uuid.UUID


@dataclass(frozen=True)
class GatewayId:
    value: int
    identity_type: ClassVar[type[GatewayIdentity]] = GatewayIdentity


@dataclass(frozen=True)
class GatewayName:
    value: str
    identity_type: ClassVar[type[GatewayIdentity]] = GatewayIdentity


_GATEWAY_PREFIX = "gateway"


def _parse_gateway_logical_id(v: Any) -> uuid.UUID:
    if isinstance(v, uuid.UUID):
        return v
    if isinstance(v, str) and v.startswith(_GATEWAY_PREFIX):
        return uuid.UUID(v[len(_GATEWAY_PREFIX) :])
    return uuid.UUID(v)


type GatewayLogicalId = Annotated[
    uuid.UUID,
    BeforeValidator(_parse_gateway_logical_id),
    PlainSerializer(lambda v: f"{_GATEWAY_PREFIX}{v}", return_type=str),
]


# ---------------------------------------------------------------------------
# Profile / Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProfileIdentity:
    name: str
    id: int


@dataclass(frozen=True)
class ProfileId:
    value: int
    identity_type: ClassVar[type[ProfileIdentity]] = ProfileIdentity


@dataclass(frozen=True)
class ProfileName:
    value: str
    identity_type: ClassVar[type[ProfileIdentity]] = ProfileIdentity


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApplicationIdentity:
    name: str
    id: int


@dataclass(frozen=True)
class ApplicationId:
    value: int
    identity_type: ClassVar[type[ApplicationIdentity]] = ApplicationIdentity


@dataclass(frozen=True)
class ApplicationName:
    value: str
    identity_type: ClassVar[type[ApplicationIdentity]] = ApplicationIdentity


# ---------------------------------------------------------------------------
# Address Group
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AddressGroupIdentity:
    name: str
    id: int
    logical_id: uuid.UUID


@dataclass(frozen=True)
class AddressGroupId:
    value: int
    identity_type: ClassVar[type[AddressGroupIdentity]] = AddressGroupIdentity


@dataclass(frozen=True)
class AddressGroupLogicalId:
    value: uuid.UUID
    identity_type: ClassVar[type[AddressGroupIdentity]] = AddressGroupIdentity


@dataclass(frozen=True)
class AddressGroupName:
    value: str
    identity_type: ClassVar[type[AddressGroupIdentity]] = AddressGroupIdentity


# ---------------------------------------------------------------------------
# Service Group
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServiceGroupIdentity:
    name: str
    id: int
    logical_id: uuid.UUID


@dataclass(frozen=True)
class ServiceGroupId:
    value: int
    identity_type: ClassVar[type[ServiceGroupIdentity]] = ServiceGroupIdentity


@dataclass(frozen=True)
class ServiceGroupLogicalId:
    value: uuid.UUID
    identity_type: ClassVar[type[ServiceGroupIdentity]] = ServiceGroupIdentity


@dataclass(frozen=True)
class ServiceGroupName:
    value: str
    identity_type: ClassVar[type[ServiceGroupIdentity]] = ServiceGroupIdentity


# ---------------------------------------------------------------------------
# License
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LicenseIdentity:
    name: str
    id: int
    license_id: int
    sku: str


@dataclass(frozen=True)
class LicenseId:
    value: int
    identity_type: ClassVar[type[LicenseIdentity]] = LicenseIdentity


@dataclass(frozen=True)
class LicenseSku:
    value: str
    identity_type: ClassVar[type[LicenseIdentity]] = LicenseIdentity


@dataclass(frozen=True)
class LicenseName:
    value: str
    identity_type: ClassVar[type[LicenseIdentity]] = LicenseIdentity
