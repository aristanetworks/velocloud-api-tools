"""ProfileResolver — resolves profile names to ProfileIdentity via VCO API."""

from typing import override

from pydantic import MISSING

from pyvelo.client.schemas.profiles import GetEnterpriseConfigurations
from pyvelo.config.resolver import BaseResolver, ResolverContext
from pyvelo.core.identity import IdentityKey, ProfileId, ProfileIdentity, ProfileName


class ProfileResolver(BaseResolver[ProfileIdentity]):
    """Resolves a profile identifier to a :class:`ProfileIdentity`.

    Accepts:
        - :class:`ProfileName`: human-readable profile name (exact or case-insensitive).
        - :class:`ProfileId`: numeric VCO configuration ID.

    Loads via ``enterprise/getEnterpriseConfigurations``.
    """

    identity_type = ProfileIdentity

    @override
    def lookup_key(self, key: IdentityKey[ProfileIdentity]) -> ProfileIdentity:
        match key:
            case ProfileName(value=name):
                return self.lookup(name)
            case ProfileId(value=v):
                return self._by_key("id", v)
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
            profile_id = profile.id_
            if profile_id is None or profile_id is MISSING:
                continue
            identity = ProfileIdentity(name=name, id=profile_id)
            self._register(name, identity, id=profile_id)
