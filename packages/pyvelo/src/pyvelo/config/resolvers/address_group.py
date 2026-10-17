"""AddressGroupResolver — resolves address group names to AddressGroupIdentity via VCO API."""

from typing import override

from pyvelo.client.enums.enterprise import ObjectGroupType
from pyvelo.client.schemas.enterprise import GetObjectGroups
from pyvelo.config.resolver import BaseResolver, ResolverContext
from pyvelo.core.identity import (
    AddressGroupId,
    AddressGroupIdentity,
    AddressGroupLogicalId,
    AddressGroupName,
)

AddressGroupKey = AddressGroupName | AddressGroupId | AddressGroupLogicalId


class AddressGroupResolver(BaseResolver[AddressGroupKey, AddressGroupIdentity]):
    """Resolves an address-group identifier to an :class:`AddressGroupIdentity`.

    Accepts:
        - :class:`AddressGroupName`: human-readable group name.
        - :class:`AddressGroupId`: numeric VCO object ID.
        - :class:`AddressGroupLogicalId`: UUID logical ID.

    Loads via ``enterprise/getObjectGroups`` with type ``address_group``.
    """

    identity_type = AddressGroupIdentity

    @override
    async def resolve_key(self, key: AddressGroupKey) -> AddressGroupIdentity:
        match key:
            case AddressGroupName(value=name):
                return await self.resolve(name)
            case AddressGroupId(value=v):
                name = self._reverse["id"][v]
                return self._by_name[name]
            case AddressGroupLogicalId(value=v):
                name = self._reverse["logical_id"][v]
                return self._by_name[name]
            case _:
                raise TypeError(f"Unsupported key type: {type(key).__name__}")

    @override
    async def load(self, context: ResolverContext) -> None:
        request = GetObjectGroups(type_=ObjectGroupType.ADDRESS_GROUP)
        if context.enterprise_id is not None:
            request.enterprise_id = context.enterprise_id

        groups = await self._client.get_object_groups(request)

        self._clear()

        for group in groups:
            name = group.name
            group_id = group.id_
            logical_id = group.logical_id
            identity = AddressGroupIdentity(
                name=name, id=group_id, logical_id=logical_id
            )
            self._register(name, identity, id=group_id, logical_id=logical_id)
