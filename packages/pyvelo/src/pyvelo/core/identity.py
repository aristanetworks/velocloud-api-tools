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
        """*message* replaces the default, which names *name* and lists *candidates*."""
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
        """The error for *identity_type*; its message says how to load the lookups."""
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
        """Set ``identity_type`` from the class's ``IdentityKey[...]`` base.

        A subclass whose bases name none keeps the one it inherits, and raises
        ``TypeError`` when it has none to inherit.
        """
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
    """An enterprise network segment, as the segment resolver loads it.

    *segment_id* is the number in the segment's data (``data.segmentId``), the one
    the configuration modules name it by; *object_id* is the ``id`` of its network
    segment object. *type_* is ``REGULAR``, ``CDE`` or ``PRIVATE``.
    """

    name: str
    segment_id: int
    object_id: int
    logical_id: uuid.UUID
    type_: NetworkSegmentType = NetworkSegmentType.REGULAR


@dataclass(frozen=True)
class SegmentId(IdentityKey[SegmentIdentity]):
    """The key of a segment by its ``segmentId``, the number in the segment's data."""

    value: int


@dataclass(frozen=True)
class SegmentObjectId(IdentityKey[SegmentIdentity]):
    """The key of a segment by the ``id`` of its network segment object."""

    value: int


@dataclass(frozen=True)
class SegmentLogicalId(IdentityKey[SegmentIdentity]):
    """The key of a segment by its logical ID."""

    value: uuid.UUID


@dataclass(frozen=True)
class SegmentName(IdentityKey[SegmentIdentity]):
    """The key of a segment by its name."""

    value: str


# ---------------------------------------------------------------------------
# Edge
# ---------------------------------------------------------------------------


DeviceFamily = NewType("DeviceFamily", str)
"""Profile model class key (e.g. ``"edge6X0"``, ``"virtual"``)."""


@dataclass(frozen=True)
class EdgeIdentity:
    """An Edge, as the edge resolver loads it.

    *model_number* is the Edge's model, and *device_family* the profile model class
    that model belongs to; both are None when the VCO gives no model.
    """

    name: str
    id: int
    logical_id: uuid.UUID
    model_number: ModelNumber | None = None
    device_family: DeviceFamily | None = None


@dataclass(frozen=True)
class EdgeId(IdentityKey[EdgeIdentity]):
    """The key of an Edge by its ``id``."""

    value: int


@dataclass(frozen=True)
class EdgeLogicalId(IdentityKey[EdgeIdentity]):
    """The key of an Edge by its logical ID."""

    value: uuid.UUID


@dataclass(frozen=True)
class EdgeName(IdentityKey[EdgeIdentity]):
    """The key of an Edge by its name."""

    value: str


# ---------------------------------------------------------------------------
# Link
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LinkIdentity:
    """A WAN link of one Edge, as the link resolver loads it from the recent links.

    *name* is the link's display name, else its name. *logical_id* is the link's
    ``logicalId`` text and *internal_id* its ``internalId``; *edge_id* is the Edge
    the links were loaded for.
    """

    name: str
    id: int
    logical_id: str
    internal_id: uuid.UUID
    edge_id: int


@dataclass(frozen=True)
class LinkId(IdentityKey[LinkIdentity]):
    """The key of a link by its ``id``."""

    value: int


@dataclass(frozen=True)
class LinkLogicalId(IdentityKey[LinkIdentity]):
    """The key of a link by its ``logicalId`` text."""

    value: str


@dataclass(frozen=True)
class LinkInternalId(IdentityKey[LinkIdentity]):
    """The key of a link by its ``internalId``."""

    value: uuid.UUID


@dataclass(frozen=True)
class LinkName(IdentityKey[LinkIdentity]):
    """The key of a link by its display name, else its name."""

    value: str


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GatewayIdentity:
    """A Gateway's name, ``id`` and logical ID."""

    name: str
    id: int
    logical_id: uuid.UUID


@dataclass(frozen=True)
class GatewayId(IdentityKey[GatewayIdentity]):
    """The key of a Gateway by its ``id``."""

    value: int


@dataclass(frozen=True)
class GatewayName(IdentityKey[GatewayIdentity]):
    """The key of a Gateway by its name."""

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
    """A profile (an enterprise configuration) by its name and ``id``."""

    name: str
    id: int


@dataclass(frozen=True)
class ProfileId(IdentityKey[ProfileIdentity]):
    """The key of a profile by its ``id``."""

    value: int


@dataclass(frozen=True)
class ProfileName(IdentityKey[ProfileIdentity]):
    """The key of a profile by its name."""

    value: str


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApplicationIdentity:
    """A routable application, by its display name (else its name) and ``id``."""

    name: str
    id: int


@dataclass(frozen=True)
class ApplicationId(IdentityKey[ApplicationIdentity]):
    """The key of an application by its ``id``."""

    value: int


@dataclass(frozen=True)
class ApplicationName(IdentityKey[ApplicationIdentity]):
    """The key of an application by its display name, else its name."""

    value: str


# ---------------------------------------------------------------------------
# Address Group
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AddressGroupIdentity:
    """An ``address_group`` object group, by its name, ``id`` and logical ID."""

    name: str
    id: int
    logical_id: uuid.UUID


@dataclass(frozen=True)
class AddressGroupId(IdentityKey[AddressGroupIdentity]):
    """The key of an address group by its ``id``."""

    value: int


@dataclass(frozen=True)
class AddressGroupLogicalId(IdentityKey[AddressGroupIdentity]):
    """The key of an address group by its logical ID."""

    value: uuid.UUID


@dataclass(frozen=True)
class AddressGroupName(IdentityKey[AddressGroupIdentity]):
    """The key of an address group by its name."""

    value: str


# ---------------------------------------------------------------------------
# Service Group
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServiceGroupIdentity:
    """A service group (a ``port_group`` object group): name, ``id`` and logical ID."""

    name: str
    id: int
    logical_id: uuid.UUID


@dataclass(frozen=True)
class ServiceGroupId(IdentityKey[ServiceGroupIdentity]):
    """The key of a service group by its ``id``."""

    value: int


@dataclass(frozen=True)
class ServiceGroupLogicalId(IdentityKey[ServiceGroupIdentity]):
    """The key of a service group by its logical ID."""

    value: uuid.UUID


@dataclass(frozen=True)
class ServiceGroupName(IdentityKey[ServiceGroupIdentity]):
    """The key of a service group by its name."""

    value: str


# ---------------------------------------------------------------------------
# License
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LicenseIdentity:
    """An Edge license of the enterprise, as the license resolver loads it.

    *id* and *license_id* are the license row's ``id`` and ``licenseId``, and *sku*
    its SKU.
    """

    name: str
    id: int
    license_id: int
    sku: str


@dataclass(frozen=True)
class LicenseId(IdentityKey[LicenseIdentity]):
    """The key of a license by its ``id``."""

    value: int


@dataclass(frozen=True)
class LicenseSku(IdentityKey[LicenseIdentity]):
    """The key of a license by its SKU."""

    value: str


@dataclass(frozen=True)
class LicenseName(IdentityKey[LicenseIdentity]):
    """The key of a license by its name."""

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
    """The key of a network service by its name."""

    value: str


@dataclass(frozen=True)
class NetworkServiceLogicalId(IdentityKey[NetworkServiceIdentity]):
    """The key of a network service by its logical ID."""

    value: uuid.UUID


@dataclass(frozen=True)
class NetworkServiceObjectId(IdentityKey[NetworkServiceIdentity]):
    """The key of a network service by the ``id`` of its object."""

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
    """The key of a hub by its name."""

    value: str


@dataclass(frozen=True)
class HubLogicalId(IdentityKey[HubIdentity]):
    """The key of a hub by the Edge's or the cluster's logical ID."""

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
        """*message* replaces the default.

        The default says to save the hub first and gives the patch that adds it to the
        segment's Branch-to-Hub list.
        """
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
