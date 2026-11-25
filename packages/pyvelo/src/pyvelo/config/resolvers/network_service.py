"""NetworkServiceResolver: resolves a network service of an enterprise by name or ID."""

from typing import Self, override

from pyvelo.client.protocol import VcoClientProtocol
from pyvelo.client.schemas.enterprise import GetEnterpriseServices
from pyvelo.config.resolver import BaseResolver, ResolverContext, resolution_error
from pyvelo.core.identity import (
    IdentityKey,
    NetworkServiceIdentity,
    NetworkServiceLogicalId,
    NetworkServiceName,
    NetworkServiceObjectId,
    ResolutionError,
)


class NetworkServiceResolver(BaseResolver[NetworkServiceIdentity]):
    """Resolves a network service to a :class:`NetworkServiceIdentity`.

    Accepts:
        - :class:`NetworkServiceName`: the service's name.
        - :class:`NetworkServiceLogicalId`: its UUID logical ID.
        - :class:`NetworkServiceObjectId`: its numeric VCO object ID.

    A service is not found until ``load()`` has read every service of the
    enterprise from ``enterprise/getEnterpriseServices``, scoped to the
    enterprise of the :class:`ResolverContext`; before that the lookups raise
    ``ResolverNotLoadedError``. Services of different types may share a
    name, so a name that two services hold is refused with a message that asks
    for the logical ID, which every service has alone. ``BaseResolver`` keeps
    one identity per name, so this resolver also keeps every service it loaded,
    and ``identities()`` returns them all.
    """

    identity_type = NetworkServiceIdentity
    requires_load = True

    def __init__(self, client: VcoClientProtocol) -> None:
        super().__init__(client)
        self._services: list[NetworkServiceIdentity] = []

    @override
    def lookup(self, name: str) -> NetworkServiceIdentity:
        self._require_loaded()
        named = [s for s in self._services if s.name == name]
        if not named:
            named = [s for s in self._services if s.name.lower() == name.lower()]
        if len(named) == 1:
            return named[0]
        if not named:
            raise resolution_error(self._entity_label, name, self._by_name.keys())
        listed = ", ".join(
            f"a service of type {s.type_!r} ({s.logical_id})" for s in named
        )
        raise ResolutionError(
            name=name,
            identity_type=NetworkServiceIdentity,
            candidates=[str(s.logical_id) for s in named],
            message=(
                f"Several network services are named {name!r}: {listed}. "
                "Give the logical ID of the one you mean instead of its name."
            ),
        )

    @override
    def lookup_key(
        self, key: IdentityKey[NetworkServiceIdentity]
    ) -> NetworkServiceIdentity:
        self._require_loaded()
        match key:
            case NetworkServiceName(value=name):
                return self.lookup(name)
            case NetworkServiceLogicalId(value=v):
                return self._one("logical_id", v)
            case NetworkServiceObjectId(value=v):
                return self._one("object_id", v)
            case _:
                raise TypeError(f"Unsupported key type: {type(key).__name__}")

    @override
    async def load(self, context: ResolverContext) -> None:
        """Fetch every service of the enterprise and populate the lookup tables."""
        request = GetEnterpriseServices()
        if context.enterprise_id is not None:
            request.enterprise_id = context.enterprise_id

        summaries = await self._client.get_enterprise_service_summaries(request)

        self._clear()
        for summary in summaries:
            identity = NetworkServiceIdentity(
                name=summary.name,
                logical_id=summary.logical_id,
                object_id=summary.id_,
                type_=summary.type_,
            )
            self._services.append(identity)
            self._register(
                summary.name,
                identity,
                logical_id=summary.logical_id,
                object_id=summary.id_,
            )
        self.loaded = True

    @override
    def identities(self) -> list[NetworkServiceIdentity]:
        """Every service loaded so far, also those that share a name."""
        return list(self._services)

    @override
    def unloaded(self) -> Self:
        twin = super().unloaded()
        twin._services = []
        return twin

    @override
    def _clear(self) -> None:
        super()._clear()
        self._services.clear()

    def _one(self, id_attr: str, value: object) -> NetworkServiceIdentity:
        """The loaded service whose ``id_attr`` is *value*, also one that shares its name."""
        for service in self._services:
            if getattr(service, id_attr) == value:
                return service
        raise ResolutionError(
            name=str(value),
            identity_type=NetworkServiceIdentity,
            candidates=[],
            message=f"No {self._entity_label} has {id_attr} {value}.",
        )
