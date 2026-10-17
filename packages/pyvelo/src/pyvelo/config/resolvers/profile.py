"""ProfileResolver — resolves profile names to ProfileIdentity via VCO API."""

from typing import override

from pydantic_core import MISSING

from pyvelo.client.schemas.profiles import GetEnterpriseConfigurations
from pyvelo.config.resolver import BaseResolver, ResolverContext
from pyvelo.core.identity import ProfileId, ProfileIdentity, ProfileName

ProfileKey = ProfileName | ProfileId


class ProfileResolver(BaseResolver[ProfileKey, ProfileIdentity]):
    """Resolves a profile identifier to a :class:`ProfileIdentity`.

    Accepts:
        - :class:`ProfileName`: human-readable profile name (case-insensitive, fuzzy).
        - :class:`ProfileId`: numeric VCO configuration ID.

    Loads via ``enterprise/getEnterpriseConfigurations``.
    """

    identity_type = ProfileIdentity

    @override
    async def resolve_key(self, key: ProfileKey) -> ProfileIdentity:
        match key:
            case ProfileName(value=name):
                return await self.resolve(name)
            case ProfileId(value=v):
                name = self._reverse["id"][v]
                return self._by_name[name]
            case _:
                raise TypeError(f"Unsupported key type: {type(key).__name__}")

    @override
    async def load(self, context: ResolverContext) -> None:
        request = GetEnterpriseConfigurations()
        if context.enterprise_id is not None:
            request.enterprise_id = context.enterprise_id

        profiles = await self._client.get_enterprise_configurations(request)

        self._clear()

        for profile in profiles:
            name = profile.name
            if name is None or name is MISSING:
                continue
            profile_id = profile.id_
            if profile_id is None or profile_id is MISSING:
                continue
            identity = ProfileIdentity(name=name, id=profile_id)
            self._register(name, identity, id=profile_id)
