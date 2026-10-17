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

from pyvelo.core import PaginatedVcoModel, VcoModel

if TYPE_CHECKING:
    from pyvelo.client.schemas.configuration import UpdateConfigurationModule


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
        self, request: UpdateConfigurationModule
    ) -> None: ...

    def _on_stale_websocket_closed(self, task: asyncio.Task[None]) -> None: ...
