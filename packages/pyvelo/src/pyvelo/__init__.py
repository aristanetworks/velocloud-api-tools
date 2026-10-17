"""pyvelo: async VeloCloud Orchestrator (VCO) SDK.

The canonical entry points are re-exported here for convenience:

- :class:`VcoClient` and :class:`VcoClientProtocol` for API access.
- :class:`VcoModel` plus the field markers and identity types from
  :mod:`pyvelo.core` for building/extending request and response schemas.
- The resolver framework (:class:`Resolver`, :class:`BaseResolver`,
  :class:`ResolverRegistry`, :class:`ResolverContext`,
  :class:`ResolutionError`) and concrete resolvers under
  :mod:`pyvelo.config.resolvers` for translating object names to IDs.

For configuration helpers (pipeline, aspects, state), see the ``pyvelo-intent`` package.
"""

from pyvelo.client import (
    VcoApiError,
    VcoAuth,
    VcoClient,
    VcoClientProtocol,
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
    "EdgeResolver",
    "LicenseResolver",
    "LinkResolver",
    "ProfileResolver",
    "ResolutionError",
    "Resolver",
    "ResolverContext",
    "ResolverRegistry",
    "SegmentResolver",
    "ServiceGroupResolver",
    # Client
    "VcoApiError",
    "VcoAuth",
    "VcoClient",
    "VcoClientProtocol",
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
]
