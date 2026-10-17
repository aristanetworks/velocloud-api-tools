"""ServiceGroupResolver — resolves service/port group names to ServiceGroupIdentity via VCO API."""

from typing import override

from pyvelo.client.enums.enterprise import ObjectGroupType
from pyvelo.client.schemas.enterprise import GetObjectGroups
from pyvelo.config.resolver import BaseResolver, ResolverContext
from pyvelo.core.identity import (
    ServiceGroupId,
    ServiceGroupIdentity,
    ServiceGroupLogicalId,
    ServiceGroupName,
)

ServiceGroupKey = ServiceGroupName | ServiceGroupId | ServiceGroupLogicalId


class ServiceGroupResolver(BaseResolver[ServiceGroupKey, ServiceGroupIdentity]):
    """Resolves a service-group (port-group) identifier to a :class:`ServiceGroupIdentity`.

    Accepts:
        - :class:`ServiceGroupName`: human-readable group name.
        - :class:`ServiceGroupId`: numeric VCO object ID.
        - :class:`ServiceGroupLogicalId`: UUID logical ID.

    Loads via ``enterprise/getObjectGroups`` with type ``port_group``.
    """

    identity_type = ServiceGroupIdentity

    @override
    async def resolve_key(self, key: ServiceGroupKey) -> ServiceGroupIdentity:
        match key:
            case ServiceGroupName(value=name):
                return await self.resolve(name)
            case ServiceGroupId(value=v):
                name = self._reverse["id"][v]
                return self._by_name[name]
            case ServiceGroupLogicalId(value=v):
                name = self._reverse["logical_id"][v]
                return self._by_name[name]
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
