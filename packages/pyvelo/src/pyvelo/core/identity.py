"""Identity key types and dataclasses for typed ID resolution.

Key types (SegmentId, SegmentName, etc.) are frozen dataclasses used as
typed lookup keys for the resolver system. Each key type declares its
associated identity type via a ClassVar.

Identity dataclasses (SegmentIdentity, etc.) hold all resolved fields
for an entity, using plain int/uuid.UUID types.
"""

import re
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar, NewType, cast, override

from pydantic import BeforeValidator, PlainSerializer

from pyvelo.core.enums import ModelNumber, NetworkSegmentType


class ResolutionError(KeyError):
    """Raised when a resolver cannot find a name.

    It is a ``KeyError``, so a caller that catches a missing key catches it.
    Like the client errors, it is pickled and copied from its class, its
    ``args`` and its attributes (notes included), not by calling the
    constructor again, which takes keywords only. ``__cause__``,
    ``__context__`` and the traceback are not carried.
    """

    def __init__(
        self,
        *,
        name: str,
        identity_type: type | str,
        candidates: list[str],
        message: str | None = None,
    ) -> None:
        self.name = name
        self.identity_type = identity_type
        self.candidates = candidates
        super().__init__(message or self._default_message())

    @override
    def __reduce__(self) -> tuple[Any, ...]:
        attributes = dict(self.__dict__)
        if "__notes__" in attributes:
            # A shallow copy must not add its notes to this error's list.
            attributes["__notes__"] = list(cast(list[str], attributes["__notes__"]))
        return (_rebuild_resolution_error, (type(self), self.args), attributes)

    @override
    def __str__(self) -> str:
        # KeyError prints the repr of its argument, with quotes and a literal \n.
        return str(self.args[0])

    def _default_message(self) -> str:
        label = (
            self.identity_type
            if isinstance(self.identity_type, str)
            else self.identity_type.__name__
        )
        if self.candidates:
            cands = "\n".join(f"  - {c}" for c in self.candidates)
            return f"Unknown {label}: {self.name!r}.\nDid you mean:\n{cands}"
        return f"Unknown {label}: {self.name!r}."


def entity_label(identity_type: type) -> str:
    """The words of an identity type's name without ``Identity``: ``"network service"`` for ``NetworkServiceIdentity``."""
    name = identity_type.__name__.removesuffix("Identity")
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).lower()


class ResolverNotLoadedError(ResolutionError):
    """Raised when a lookup needs a resolver that has not been loaded.

    It keeps ``identity_type``; ``name`` is ``""`` and ``candidates`` is empty.
    It pickles and copies as a ``ResolutionError`` does.
    """

    def __init__(self, identity_type: type) -> None:
        label = entity_label(identity_type)
        type_name = identity_type.__name__
        super().__init__(
            name="",
            identity_type=identity_type,
            candidates=[],
            message=(
                f"The {type_name} lookups are not loaded, so a {label} cannot be "
                "found yet. With a ConfigState, a save, a dry run and apply load "
                f"them themselves; `await pipeline.load_resolvers(state, {type_name})` "
                "loads them now. With plan_module or update_module, pass "
                f"resolvers= a registry in which a loaded resolver for {type_name} "
                "is registered."
            ),
        )


def _rebuild_resolution_error(
    cls: type[ResolutionError], args: tuple[Any, ...]
) -> ResolutionError:
    """Build an error of *cls* without calling its constructor; its attributes arrive as state."""
    error = cls.__new__(cls)
    error.args = args
    return error


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


# ---------------------------------------------------------------------------
# Network service
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NetworkServiceIdentity:
    """An enterprise network service; ``type_`` is its type text as the VCO answers it."""

    name: str
    logical_id: uuid.UUID
    object_id: int
    type_: str


@dataclass(frozen=True)
class NetworkServiceName:
    value: str
    identity_type: ClassVar[type[NetworkServiceIdentity]] = NetworkServiceIdentity


@dataclass(frozen=True)
class NetworkServiceLogicalId:
    value: uuid.UUID
    identity_type: ClassVar[type[NetworkServiceIdentity]] = NetworkServiceIdentity


@dataclass(frozen=True)
class NetworkServiceObjectId:
    value: int
    identity_type: ClassVar[type[NetworkServiceIdentity]] = NetworkServiceIdentity
