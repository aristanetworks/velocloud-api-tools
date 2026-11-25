"""Identity key types and dataclasses for typed ID resolution.

Key types (SegmentId, SegmentName, etc.) are frozen dataclasses used as
typed lookup keys for the resolver system. Each key type subclasses
``IdentityKey`` of its identity type, which a resolver's ``resolve`` returns.

Identity dataclasses (SegmentIdentity, etc.) hold all resolved fields
for an entity, using plain int/uuid.UUID types.
"""

import json
import re
import types
import typing
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar, Literal, NewType, cast, override

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
        self.name: str = name
        self.identity_type: type | str = identity_type
        self.candidates: list[str] = candidates
        super().__init__(message or self._default_message())

    @override
    def __reduce__(self) -> tuple[Any, ...]:
        attributes = dict(self.__dict__)
        if "__notes__" in attributes:
            # A shallow copy must not add its notes to this error's list.
            attributes["__notes__"] = list(cast("list[str]", attributes["__notes__"]))
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


class IdentityKey[IdentityT]:
    """Base of every lookup key: a key of ``IdentityKey[SegmentIdentity]`` resolves to a ``SegmentIdentity``.

    A key class names its identity type in its base, ``class SegmentId(IdentityKey[SegmentIdentity])``,
    and ``identity_type`` is set from it, so the registry finds the resolver
    for a key and a type checker knows what resolving the key returns. A
    subclass of a concrete key inherits its parent's identity type. A class
    whose bases name none raises ``TypeError``.
    """

    identity_type: ClassVar[type[Any]]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for base in types.get_original_bases(cls):
            if typing.get_origin(base) is IdentityKey:
                (identity_type,) = typing.get_args(base)
                cls.identity_type = identity_type
                return
        if not hasattr(cls, "identity_type"):
            raise TypeError(
                f"`{cls.__name__}` must name its identity type: "
                f"`class {cls.__name__}(IdentityKey[SomeIdentity])`"
            )


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
class SegmentId(IdentityKey[SegmentIdentity]):
    value: int


@dataclass(frozen=True)
class SegmentObjectId(IdentityKey[SegmentIdentity]):
    value: int


@dataclass(frozen=True)
class SegmentLogicalId(IdentityKey[SegmentIdentity]):
    value: uuid.UUID


@dataclass(frozen=True)
class SegmentName(IdentityKey[SegmentIdentity]):
    value: str


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
class EdgeId(IdentityKey[EdgeIdentity]):
    value: int


@dataclass(frozen=True)
class EdgeLogicalId(IdentityKey[EdgeIdentity]):
    value: uuid.UUID


@dataclass(frozen=True)
class EdgeName(IdentityKey[EdgeIdentity]):
    value: str


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
class LinkId(IdentityKey[LinkIdentity]):
    value: int


@dataclass(frozen=True)
class LinkLogicalId(IdentityKey[LinkIdentity]):
    value: str


@dataclass(frozen=True)
class LinkInternalId(IdentityKey[LinkIdentity]):
    value: uuid.UUID


@dataclass(frozen=True)
class LinkName(IdentityKey[LinkIdentity]):
    value: str


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GatewayIdentity:
    name: str
    id: int
    logical_id: uuid.UUID


@dataclass(frozen=True)
class GatewayId(IdentityKey[GatewayIdentity]):
    value: int


@dataclass(frozen=True)
class GatewayName(IdentityKey[GatewayIdentity]):
    value: str


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
class ProfileId(IdentityKey[ProfileIdentity]):
    value: int


@dataclass(frozen=True)
class ProfileName(IdentityKey[ProfileIdentity]):
    value: str


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApplicationIdentity:
    name: str
    id: int


@dataclass(frozen=True)
class ApplicationId(IdentityKey[ApplicationIdentity]):
    value: int


@dataclass(frozen=True)
class ApplicationName(IdentityKey[ApplicationIdentity]):
    value: str


# ---------------------------------------------------------------------------
# Address Group
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AddressGroupIdentity:
    name: str
    id: int
    logical_id: uuid.UUID


@dataclass(frozen=True)
class AddressGroupId(IdentityKey[AddressGroupIdentity]):
    value: int


@dataclass(frozen=True)
class AddressGroupLogicalId(IdentityKey[AddressGroupIdentity]):
    value: uuid.UUID


@dataclass(frozen=True)
class AddressGroupName(IdentityKey[AddressGroupIdentity]):
    value: str


# ---------------------------------------------------------------------------
# Service Group
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServiceGroupIdentity:
    name: str
    id: int
    logical_id: uuid.UUID


@dataclass(frozen=True)
class ServiceGroupId(IdentityKey[ServiceGroupIdentity]):
    value: int


@dataclass(frozen=True)
class ServiceGroupLogicalId(IdentityKey[ServiceGroupIdentity]):
    value: uuid.UUID


@dataclass(frozen=True)
class ServiceGroupName(IdentityKey[ServiceGroupIdentity]):
    value: str


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
class LicenseId(IdentityKey[LicenseIdentity]):
    value: int


@dataclass(frozen=True)
class LicenseSku(IdentityKey[LicenseIdentity]):
    value: str


@dataclass(frozen=True)
class LicenseName(IdentityKey[LicenseIdentity]):
    value: str


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
class NetworkServiceName(IdentityKey[NetworkServiceIdentity]):
    value: str


@dataclass(frozen=True)
class NetworkServiceLogicalId(IdentityKey[NetworkServiceIdentity]):
    value: uuid.UUID


@dataclass(frozen=True)
class NetworkServiceObjectId(IdentityKey[NetworkServiceIdentity]):
    value: int


# ---------------------------------------------------------------------------
# Hub
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HubIdentity:
    """An Edge or a cluster that can be a hub, as a hub list entry names it.

    *kind* is ``"edge"`` or ``"cluster"`` and *type_* the entry's ``type`` text
    that goes with it. *logical_id* is the Edge's or the cluster's own. The
    Edge fields are set for an Edge, the cluster fields for a cluster; the
    others are None.
    """

    name: str
    kind: Literal["edge", "cluster"]
    type_: Literal["edgeHub", "edgeHubCluster"]
    logical_id: uuid.UUID
    edge_id: int | None
    edge_logical_id: uuid.UUID | None
    cluster_id: int | None
    cluster_logical_id: uuid.UUID | None


@dataclass(frozen=True)
class HubName(IdentityKey[HubIdentity]):
    value: str


@dataclass(frozen=True)
class HubLogicalId(IdentityKey[HubIdentity]):
    value: uuid.UUID


@dataclass(frozen=True)
class HubService:
    """The network service that stands for a hub in one profile and segment.

    *object_id* and *logical_id* are the service's own; *data_logical_id* is
    the logical ID its data holds, when that differs, and *segment_object_id*
    the segment it is kept for. *kind* and *name* are the hub's.
    """

    object_id: int
    logical_id: uuid.UUID
    data_logical_id: uuid.UUID | None
    segment_object_id: int | None
    kind: Literal["edge", "cluster"]
    name: str


class HubServiceNotFoundError(ResolutionError):
    """A QoS rule names a hub that has no service for its profile and segment.

    The row that goes with the rule's hub points at the hub's service for the
    profile and segment, and only a role call makes one (a save of the
    profile's device settings that adds the hub to the segment's lists does).
    *hub* is the hub's name, else its logical ID; *segment* the segment's name
    and *path* the entry of the rule. It is a ``ResolutionError`` and pickles
    and copies as one does, with its three attributes.
    """

    def __init__(
        self, *, hub: str, segment: str, path: str, message: str | None = None
    ) -> None:
        self.hub: str = hub
        self.segment: str = segment
        self.path: str = path
        super().__init__(
            name=hub,
            identity_type=HubService,
            candidates=[],
            message=message or self._service_message(),
        )

    def _service_message(self) -> str:
        # *hub* is the hub's name, else its logical ID.
        try:
            _ = uuid.UUID(self.hub)
            key = "logicalId"
        except ValueError:
            key = "name"
        add = {
            "segments": {
                self.segment: {
                    "vpn": {
                        "edgeToEdgeHub": {
                            "vpnHubs": {"+": {"_op": "add", key: self.hub}}
                        }
                    }
                }
            }
        }
        return (
            f"{self.path} names the hub {self.hub!r}, which has no service for "
            f"this profile in the segment {self.segment!r}, so the row that "
            "goes with the rule cannot be added: the VCO would store a row "
            "that points at nothing. A hub gets its service when a save of "
            "the profile's device settings adds it to the segment's "
            "Branch-to-Hub list, so save the hub first and the rule after. "
            f"To add the hub to the list, send the patch {json.dumps(add)}."
        )
