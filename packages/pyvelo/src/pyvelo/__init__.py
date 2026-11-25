"""pyvelo: async VeloCloud Orchestrator (VCO) SDK.

The canonical entry points are re-exported here for convenience:

- :class:`VcoClient` and :class:`VcoClientProtocol` for API access, and
  :func:`get_vco_credentials` for reading ``VCO_HOST`` / ``VCO_TOKEN``.
- :class:`VcoModel` plus the field markers and identity types from
  :mod:`pyvelo.core` for building/extending request and response schemas.
- The resolver framework (:class:`Resolver`, :class:`BaseResolver`,
  :class:`ResolverRegistry`, :class:`ResolverContext`,
  :class:`ResolutionError`) and concrete resolvers under
  :mod:`pyvelo.config.resolvers` for translating object names to IDs.

The configuration pipeline and its state (:class:`ConfigPipeline`,
:class:`ConfigState`) are in :mod:`pyvelo.config`.
"""

from pyvelo.client import (
    ComposedModuleError,
    ConfigConflictError,
    DanglingReferenceError,
    DetachedObjectError,
    FixedListError,
    GroupOwnershipError,
    HubListError,
    LayerUnknownError,
    LossyFieldError,
    ModuleWriteError,
    RefsDroppedError,
    RefsNotLoadedError,
    SegmentSetError,
    UnboundError,
    VcoApiError,
    VcoAuth,
    VcoClient,
    VcoClientProtocol,
    VcoCredentialsError,
    VcoEmptyResultError,
    VcoError,
    VcoHttpError,
    VcoOperationError,
    VcoRateLimitError,
    VcoWebSocketConnectionError,
    VcoWebSocketDisconnectedError,
    VcoWebSocketError,
    VcoWebSocketTimeoutError,
    VcoWebSocketValidationError,
    WriteNotAppliedError,
    WriteOutcomeUnknownError,
    get_vco_credentials,
)
from pyvelo.config.resolver import (
    BaseResolver,
    ResolutionError,
    Resolver,
    ResolverContext,
    ResolverRegistry,
)
from pyvelo.config.resolvers.address_group import AddressGroupResolver
from pyvelo.config.resolvers.application import ApplicationResolver
from pyvelo.config.resolvers.edge import EdgeResolver
from pyvelo.config.resolvers.license import LicenseResolver
from pyvelo.config.resolvers.link import LinkResolver
from pyvelo.config.resolvers.profile import ProfileResolver
from pyvelo.config.resolvers.segment import SegmentResolver
from pyvelo.config.resolvers.service_group import ServiceGroupResolver
from pyvelo.core import VcoModel

__all__ = [
    # Concrete resolvers
    "AddressGroupResolver",
    "ApplicationResolver",
    # Resolver framework
    "BaseResolver",
    "ComposedModuleError",
    "ConfigConflictError",
    "DanglingReferenceError",
    "DetachedObjectError",
    "EdgeResolver",
    "FixedListError",
    "GroupOwnershipError",
    "HubListError",
    "LayerUnknownError",
    "LicenseResolver",
    "LinkResolver",
    "LossyFieldError",
    "ModuleWriteError",
    "ProfileResolver",
    "RefsDroppedError",
    "RefsNotLoadedError",
    "ResolutionError",
    "Resolver",
    "ResolverContext",
    "ResolverRegistry",
    "SegmentResolver",
    "SegmentSetError",
    "ServiceGroupResolver",
    # Client
    "UnboundError",
    "VcoApiError",
    "VcoAuth",
    "VcoClient",
    "VcoClientProtocol",
    "VcoCredentialsError",
    "VcoEmptyResultError",
    "VcoError",
    "VcoHttpError",
    # Models
    "VcoModel",
    "VcoOperationError",
    "VcoRateLimitError",
    "VcoWebSocketConnectionError",
    "VcoWebSocketDisconnectedError",
    "VcoWebSocketError",
    "VcoWebSocketTimeoutError",
    "VcoWebSocketValidationError",
    "WriteNotAppliedError",
    "WriteOutcomeUnknownError",
    "get_vco_credentials",
]
