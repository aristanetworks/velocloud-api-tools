"""ApplicationResolver — resolves application names to ApplicationIdentity via VCO API."""

from typing import override

from pydantic_core import MISSING

from pyvelo.client.schemas.configuration import GetRoutableApplications
from pyvelo.config.resolver import BaseResolver, ResolverContext
from pyvelo.core.identity import ApplicationId, ApplicationIdentity, ApplicationName

ApplicationKey = ApplicationName | ApplicationId


class ApplicationResolver(BaseResolver[ApplicationKey, ApplicationIdentity]):
    """Resolves an application identifier to an :class:`ApplicationIdentity`.

    Accepts:
        - :class:`ApplicationName`: human-readable application name (display or canonical).
        - :class:`ApplicationId`: numeric application ID.

    Loads via ``configuration/getIdentifiableApplications``.
    """

    identity_type = ApplicationIdentity

    @override
    async def resolve_key(self, key: ApplicationKey) -> ApplicationIdentity:
        match key:
            case ApplicationName(value=name):
                return await self.resolve(name)
            case ApplicationId(value=v):
                name = self._reverse["id"][v]
                return self._by_name[name]
            case _:
                raise TypeError(f"Unsupported key type: {type(key).__name__}")

    @override
    async def load(self, context: ResolverContext) -> None:
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
            if display is MISSING or display is None:
                display = app.name
            if display is MISSING or display is None:
                continue

            app_id_raw = app.id_
            if app_id_raw is MISSING or app_id_raw is None:
                continue

            app_id = app_id_raw
            identity = ApplicationIdentity(name=display, id=app_id)
            self._register(display, identity, id=app_id)
            self._reverse.setdefault("name", {})[display] = display
