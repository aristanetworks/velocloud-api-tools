"""Base transport contract shared by VCO operation mixins.

Keeping this contract separate from the composed client protocol prevents
type-only imports in operation modules from cycling back through
``pyvelo.client`` while ``client.protocol`` is composing the mixins.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Sequence
from typing import TYPE_CHECKING, Any, Literal, Protocol

import httpx
from pydantic import BaseModel

from pyvelo.core import ConfigContext, PaginatedVcoModel, VcoModel
from pyvelo.core.base import IdentityLookup

if TYPE_CHECKING:
    from pyvelo.client.module_check import ModuleWrite
    from pyvelo.client.module_write import ModulePlan
    from pyvelo.client.schemas.configuration import (
        AnyConfigurationModule,
        ConfigurationBase,
        ConfigurationModuleBase,
        GetConfiguration,
        InsertConfigurationModule,
        InsertConfigurationModuleResult,
        UpdateConfigurationModule,
    )


class VcoClientBaseProtocol(Protocol):
    """Transport and cross-operation methods used by operation mixins."""

    _vco_fqdn: str
    _ws_cache: Any
    _ws_cleanup_tasks: set[asyncio.Task[None]]

    def _http_client(self) -> httpx.AsyncClient: ...

    async def portal[T: BaseModel](
        self,
        method: str,
        request: VcoModel,
        ResultType: type[T],
        *,
        validation_context: dict[str, Any] | None = None,
    ) -> T: ...

    async def portal_list[T: BaseModel](
        self,
        method: str,
        request: VcoModel,
        ItemType: type[T],
        *,
        hint: Literal["list"] | None = None,
    ) -> Sequence[T]: ...

    def portal_paginated[T: BaseModel](
        self, method: str, request: PaginatedVcoModel, ItemType: type[T]
    ) -> AsyncGenerator[list[T]]: ...

    async def portal_paginated_list[T: BaseModel](
        self, method: str, request: PaginatedVcoModel, ItemType: type[T]
    ) -> Sequence[T]: ...

    async def update_configuration_module(
        self,
        request: UpdateConfigurationModule,
        *,
        level: ConfigContext | None = None,
    ) -> AnyConfigurationModule | None: ...

    async def insert_configuration_module(
        self,
        request: InsertConfigurationModule,
        *,
        level: ConfigContext | None = None,
    ) -> InsertConfigurationModuleResult | AnyConfigurationModule: ...

    async def get_configuration(
        self, request: GetConfiguration
    ) -> ConfigurationBase: ...

    async def write_module[M: ConfigurationModuleBase](
        self,
        module: M,
        plan: ModulePlan,
        *,
        enterprise_id: int | None = None,
        if_absent: bool = False,
        rebase: bool = False,
        version_checked: bool = False,
        resolvers: IdentityLookup | None = None,
        profile_device_settings: ConfigurationModuleBase | None = None,
    ) -> ModuleWrite[M]: ...

    async def check_module_version[M: ConfigurationModuleBase](
        self,
        module: M,
        plan: ModulePlan,
        *,
        enterprise_id: int | None = None,
    ) -> ModuleWrite[M] | None: ...

    def _on_stale_websocket_closed(self, task: asyncio.Task[None]) -> None: ...
