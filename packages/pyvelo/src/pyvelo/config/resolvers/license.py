"""LicenseResolver — resolves license names/SKUs to LicenseIdentity via VCO API."""

from typing import override

from pydantic_core import MISSING

from pyvelo.client.schemas.license import GetEnterpriseEdgeLicenses
from pyvelo.config.resolver import BaseResolver, ResolverContext
from pyvelo.core.identity import (
    LicenseId,
    LicenseIdentity,
    LicenseName,
    LicenseSku,
)

LicenseKey = LicenseName | LicenseSku | LicenseId


class LicenseResolver(BaseResolver[LicenseKey, LicenseIdentity]):
    """Resolves a license identifier to a :class:`LicenseIdentity`.

    Accepts:
        - :class:`LicenseName`: human-readable license name.
        - :class:`LicenseSku`: vendor SKU string.
        - :class:`LicenseId`: numeric VCO license ID.

    Loads via ``license/getEnterpriseEdgeLicenses``.
    """

    identity_type = LicenseIdentity

    @override
    async def resolve_key(self, key: LicenseKey) -> LicenseIdentity:
        match key:
            case LicenseName(value=name):
                return await self.resolve(name)
            case LicenseSku(value=v):
                name = self._reverse["sku"][v]
                return self._by_name[name]
            case LicenseId(value=v):
                name = self._reverse["id"][v]
                return self._by_name[name]
            case _:
                raise TypeError(f"Unsupported key type: {type(key).__name__}")

    @override
    async def load(self, context: ResolverContext) -> None:
        request = GetEnterpriseEdgeLicenses()
        if context.enterprise_id is not None:
            request.enterprise_id = context.enterprise_id

        licenses = await self._client.get_enterprise_edge_licenses(request)

        self._clear()

        for lic in licenses:
            name = lic.name
            if name is MISSING or name is None:
                continue
            lic_id = lic.id_
            if lic_id is MISSING or lic_id is None:
                continue
            license_id = lic.license_id
            if license_id is MISSING or license_id is None:
                continue
            sku = lic.sku
            if sku is MISSING or sku is None:
                continue

            identity = LicenseIdentity(
                name=name,
                id=lic_id,
                license_id=license_id,
                sku=sku,
            )
            self._register(name, identity, id=lic_id, license_id=license_id, sku=sku)
