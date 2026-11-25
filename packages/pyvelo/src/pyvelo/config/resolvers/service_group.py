"""ServiceGroupResolver — resolves service/port group names to ServiceGroupIdentity via VCO API."""

from typing import override

from pyvelo.client.enums.enterprise import ObjectGroupType
from pyvelo.client.schemas.enterprise import GetObjectGroups
from pyvelo.config.resolver import BaseResolver, ResolverContext
from pyvelo.core.identity import (
    IdentityKey,
    ServiceGroupId,
    ServiceGroupIdentity,
    ServiceGroupLogicalId,
    ServiceGroupName,
)


class ServiceGroupResolver(BaseResolver[ServiceGroupIdentity]):
    """Resolves a service-group (port-group) identifier to a :class:`ServiceGroupIdentity`.

    Accepts:
        - :class:`ServiceGroupName`: human-readable group name.
        - :class:`ServiceGroupId`: numeric VCO object ID.
        - :class:`ServiceGroupLogicalId`: UUID logical ID.

    Loads via ``enterprise/getObjectGroups`` with type ``port_group``.
    """

    identity_type = ServiceGroupIdentity

    @override
    def lookup_key(
        self, key: IdentityKey[ServiceGroupIdentity]
    ) -> ServiceGroupIdentity:
        match key:
            case ServiceGroupName(value=name):
                return self.lookup(name)
            case ServiceGroupId(value=v):
                return self._by_key("id", v)
            case ServiceGroupLogicalId(value=v):
                return self._by_key("logical_id", v)
            case _:
                raise TypeError(f"Unsupported key type: {type(key).__name__}")

    @override
    async def load(self, context: ResolverContext) -> None:
        request = GetObjectGroups(type_=ObjectGroupType.PORT_GROUP)
        if context.enterprise_id is not None:
            request.enterprise_id = context.enterprise_id

        groups = await self._client.get_object_groups(request)

        self._clear()

        for group in groups:
            name = group.name
            group_id = group.id_
            logical_id = group.logical_id
            identity = ServiceGroupIdentity(
                name=name, id=group_id, logical_id=logical_id
            )
            self._register(name, identity, id=group_id, logical_id=logical_id)
