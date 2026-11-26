"""ApplicationResolver — resolves application names to ApplicationIdentity.

Resolution is via the VCO API.
"""

from typing import override

from pydantic import MISSING

from pyvelo.client.schemas.configuration import GetRoutableApplications
from pyvelo.config.resolver import BaseResolver, ResolverContext
from pyvelo.core.identity import (
    ApplicationId,
    ApplicationIdentity,
    ApplicationName,
    IdentityKey,
)


class ApplicationResolver(BaseResolver[ApplicationIdentity]):
    """Resolves an application identifier to an :class:`ApplicationIdentity`.

    Accepts:
        - :class:`ApplicationName`: human-readable application name (display or
          canonical).
        - :class:`ApplicationId`: numeric application ID.

    Loads via ``configuration/getIdentifiableApplications``.
    """

    identity_type: type[ApplicationIdentity] = ApplicationIdentity

    @override
    def lookup_key(self, key: IdentityKey[ApplicationIdentity]) -> ApplicationIdentity:
        match key:
            case ApplicationName(value=name):
                return self.lookup(name)
            case ApplicationId(value=v):
                return self._by_key("id", v)
            case _:
                raise TypeError(f"Unsupported key type: {type(key).__name__}")

    @override
    async def _load(self, context: ResolverContext) -> None:
        request = GetRoutableApplications()
        if context.enterprise_id is not None:
            request.enterprise_id = context.enterprise_id
        if context.edge_id is not None:
            request.edge_id = context.edge_id

        result = await self._client.get_applications(request)

        self._clear()

        apps = result.all.applications or []
        for app in apps:
            display = app.display_name
            if display is MISSING or display is None:  # pyright: ignore[reportUnnecessaryComparison]  # ApplicationItem requires it; a client whose apps lack it falls back to the name
                display = app.name
            if display is MISSING:
                continue

            app_id = app.id_
            identity = ApplicationIdentity(name=display, id=app_id)
            self._register(display, identity, id=app_id)
            self._reverse.setdefault("name", {})[display] = display
