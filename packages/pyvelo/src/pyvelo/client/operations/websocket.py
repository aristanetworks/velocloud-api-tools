"""Mixin for VCO WebSocket remote diagnostics."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Protocol

from pyvelo.client.websocket import VcoWebSocket

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol

logger = logging.getLogger(__name__)


class RemoteDiagnosticsMixin(Protocol):
    """Provides a ``websocket()`` factory for WebSocket-based remote diagnostics.

    The first call to ``websocket()`` creates a new ``VcoWebSocket`` and
    caches it.  Subsequent calls return the same instance as long as it
    is still connected, avoiding redundant handshakes::

        async with client.websocket() as ws:
            await ws.run_diagnostic(lid, "ARP_DUMP")

        # Second call reuses the existing connection:
        async with client.websocket() as ws:
            await ws.run_diagnostic(lid, "ROUTE_DUMP")

    Call ``close_websocket()`` (or let ``VcoClient.__aexit__`` do it)
    to tear down the cached connection explicitly.
    """

    _ws_cache: VcoWebSocket | None

    def websocket(self: VcoClientBaseProtocol) -> VcoWebSocket:
        """Return a (possibly cached) ``VcoWebSocket``.

        If a cached connection exists and is still alive, it is returned
        directly.  Otherwise a new ``VcoWebSocket`` is created, cached,
        and returned.  Any stale cached instance is closed before being
        replaced.
        """
        ws = getattr(self, "_ws_cache", None)
        if ws is not None and ws.connected:
            logger.debug("Reusing cached WebSocket connection")
            return ws

        if ws is not None:
            logger.debug("Cached WebSocket is stale, closing before replacement")
            cleanup_task = asyncio.get_running_loop().create_task(ws._close())
            self._ws_cleanup_tasks.add(cleanup_task)
            cleanup_task.add_done_callback(self._on_stale_websocket_closed)

        ws = VcoWebSocket(self._vco_fqdn, self._http_client())
        self._ws_cache = ws
        return ws

    async def close_websocket(self: VcoClientBaseProtocol) -> None:
        """Close the cached WebSocket connection, if any."""
        ws = getattr(self, "_ws_cache", None)
        if ws is not None:
            self._ws_cache = None
            await ws._close()
        cleanup_tasks = tuple(self._ws_cleanup_tasks)
        if cleanup_tasks:
            results = await asyncio.gather(*cleanup_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    logger.warning("Stale WebSocket cleanup failed: %s", result)

    def _on_stale_websocket_closed(
        self: VcoClientBaseProtocol, task: asyncio.Task[None]
    ) -> None:
        self._ws_cleanup_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning("Stale WebSocket cleanup failed: %s", error)
