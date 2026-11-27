"""Base transport contract shared by VCO operation mixins.

Keeping this contract separate from the composed client protocol prevents
type-only imports in operation modules from cycling back through
``pyvelo.client`` while ``client.protocol`` is composing the mixins.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel

if TYPE_CHECKING:
    import asyncio
    from collections.abc import AsyncGenerator, Sequence

    import httpx

    from pyvelo.core import PaginatedVcoModel, VcoModel


class VcoClientBaseProtocol(Protocol):
    """The transport that every operation mixin's methods are built on.

    It holds no operation. A mixin method that calls an operation of its own
    mixin on ``self`` types ``self`` as this protocol together with that mixin.
    """

    _vco_fqdn: str
    _ws_cache: Any
    _ws_cleanup_tasks: set[asyncio.Task[None]]

    def _http_client(self) -> httpx.AsyncClient: ...

    async def portal[T: BaseModel](
        self,
        method: str,
        request: VcoModel,
        result_type: type[T],
        *,
        validation_context: dict[str, Any] | None = None,
    ) -> T: ...

    async def portal_list[T: BaseModel](
        self,
        method: str,
        request: VcoModel,
        item_type: type[T],
        *,
        hint: Literal["list"] | None = None,
    ) -> Sequence[T]: ...

    def portal_paginated[T: BaseModel](
        self, method: str, request: PaginatedVcoModel, item_type: type[T]
    ) -> AsyncGenerator[list[T]]: ...

    async def portal_paginated_list[T: BaseModel](
        self, method: str, request: PaginatedVcoModel, item_type: type[T]
    ) -> Sequence[T]: ...

    def _on_stale_websocket_closed(self, task: asyncio.Task[None]) -> None: ...
