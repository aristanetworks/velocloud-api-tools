"""WebSocket client for VCO remote diagnostics.

Provides ``VcoWebSocket``, an async context manager that maintains a
persistent WebSocket connection to the VCO and dispatches incoming
messages to pending futures based on ``(action, logicalId, test)``
correlation keys.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import logging
import uuid
from collections.abc import Callable, Coroutine
from types import TracebackType
from typing import Any, Self, overload

import httpx
from httpx_ws import (
    AsyncWebSocketSession,
    HTTPXWSException,
    WebSocketDisconnect,
    aconnect_ws,
)

from pyvelo.client.exceptions import (
    VcoWebSocketConnectionError,
    VcoWebSocketDisconnectedError,
    VcoWebSocketError,
    VcoWebSocketTimeoutError,
    VcoWebSocketValidationError,
)
from pyvelo.client.schemas.gateway import GwRouteTableEntry

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 120.0
_RECONNECT_BACKOFF_BASE = 1.0
_RECONNECT_MAX_BACKOFF = 30.0
_RECONNECT_MAX_ATTEMPTS = 5
_SEND_INTERVAL = 0.1  # minimum seconds between outgoing messages

# Correlation key type: (action,) | (action, logicalId) | (action, logicalId, test)
_Key = tuple[str] | tuple[str, uuid.UUID | str] | tuple[str, uuid.UUID | str, str]


class PendingDiagnostic[T]:
    """Awaitable wrapper around a diagnostic request future.

    Returned by ``VcoWebSocket.submit_diagnostic`` so callers can fire
    off a request and await the result later.  The generic ``context``
    field lets callers attach arbitrary data (e.g. edge name) without
    maintaining a separate mapping.

    If ``max_retries > 0``, transient failures (any ``VcoWebSocketError``
    except ``VcoWebSocketValidationError``) trigger a transparent
    resubmit when the result is awaited.
    """

    def __init__(
        self,
        future: asyncio.Future[dict[str, Any]],
        *,
        test: str,
        logical_id: uuid.UUID,
        context: T,
        timeout: float,
        max_retries: int,
        _on_timeout: Callable[[], object],
        _resubmit: (
            Callable[[], Coroutine[Any, Any, asyncio.Future[dict[str, Any]]]] | None
        ),
    ) -> None:
        self._future = future
        self.test = test
        self.logical_id = logical_id
        self.context = context
        self._timeout = timeout
        self._max_retries = max_retries
        self._on_timeout = _on_timeout
        self._resubmit = _resubmit

    @property
    def done(self) -> bool:
        return self._future.done()

    async def _wait(self) -> dict[str, Any]:
        future = self._future
        for attempt in range(1 + self._max_retries):
            try:
                return await asyncio.wait_for(future, timeout=self._timeout)
            except TimeoutError:
                self._on_timeout()
                if attempt < self._max_retries and self._resubmit is not None:
                    logger.warning(
                        "Diagnostic %s on %s timed out (attempt %d), retrying",
                        self.test,
                        self.logical_id,
                        attempt + 1,
                    )
                    future = await self._resubmit()
                    continue
                raise VcoWebSocketTimeoutError(
                    f"Timeout after {self._timeout:.1f}s waiting for "
                    f"diagnostic {self.test} on {self.logical_id}",
                    timeout=self._timeout,
                ) from None
            except VcoWebSocketValidationError:
                raise
            except VcoWebSocketError as exc:
                if attempt < self._max_retries and self._resubmit is not None:
                    logger.warning(
                        "Diagnostic %s on %s failed (attempt %d), retrying: %s",
                        self.test,
                        self.logical_id,
                        attempt + 1,
                        exc,
                    )
                    future = await self._resubmit()
                    continue
                raise
        raise AssertionError("unreachable")  # pragma: no cover

    def __await__(self):
        return self._wait().__await__()


class VcoWebSocket:
    """Async context manager for VCO WebSocket remote diagnostics.

    Usage::

        async with client.websocket() as ws:
            result = await ws.run_diagnostic(logical_id, "ARP_DUMP")
    """

    def __init__(
        self,
        vco_fqdn: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._vco_fqdn = vco_fqdn
        self._http = http_client

        self._ws: AsyncWebSocketSession | None = None
        self._ws_cm: Any = None  # contextmanager handle for aconnect_ws
        self._token: str | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[_Key, asyncio.Future[dict[str, Any]]] = {}
        self._closing = False
        self._recent_sends: collections.deque[_Key] = collections.deque(maxlen=64)
        self._send_times: dict[str, float] = {}

    @property
    def connected(self) -> bool:
        """True when the WebSocket transport is open and the reader loop is running."""
        return (
            self._ws is not None
            and self._token is not None
            and self._reader_task is not None
            and not self._reader_task.done()
            and not self._closing
        )

    # -- Context manager -----------------------------------------------------

    async def __aenter__(self) -> Self:
        await self._open()
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None:
        await self._close()

    # -- Connection lifecycle ------------------------------------------------

    async def _establish_connection(self) -> None:
        """Open WebSocket, receive noop, and extract session token.

        Shared by _open (initial connection) and _reconnect (retry loop).
        """
        ws_url = f"wss://{self._vco_fqdn}/ws/"
        logger.info("Connecting WebSocket to %s", ws_url)
        self._ws_cm = aconnect_ws(ws_url, client=self._http)
        ws = await self._ws_cm.__aenter__()
        self._ws = ws
        logger.debug("WebSocket transport established, awaiting noop token")

        try:
            # First message is always a noop carrying the session token.
            noop_raw = await ws.receive_text()
            noop = json.loads(noop_raw)
            self._token = noop.get("token")
            if not self._token:
                raise VcoWebSocketConnectionError("No session token in noop message")
        except VcoWebSocketConnectionError:
            with contextlib.suppress(Exception):
                await self._ws_cm.__aexit__(None, None, None)
            self._ws_cm = None
            self._ws = None
            raise
        except Exception as exc:
            with contextlib.suppress(Exception):
                await self._ws_cm.__aexit__(None, None, None)
            self._ws_cm = None
            self._ws = None
            raise VcoWebSocketConnectionError(
                f"WebSocket handshake failed: {exc}"
            ) from exc
        logger.info("WebSocket connected, session token acquired")

    async def _open(self) -> None:
        self._closing = False
        await self._establish_connection()
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def _close(self) -> None:
        logger.info("Closing WebSocket connection (pending=%d)", len(self._pending))
        self._closing = True

        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None

        # Fail any pending futures.
        if self._pending:
            logger.warning(
                "Failing %d pending futures on close: %s",
                len(self._pending),
                list(self._pending.keys()),
            )
        for future in self._pending.values():
            if not future.done():
                future.set_exception(VcoWebSocketError("WebSocket connection closed"))
        self._pending.clear()

        if self._ws_cm is not None:
            with contextlib.suppress(Exception):
                await self._ws_cm.__aexit__(None, None, None)
            self._ws_cm = None
            self._ws = None

        self._token = None

    def _fail_pending(self, exc: VcoWebSocketError) -> None:
        """Fail all pending futures and clear connection state."""
        self._ws = None
        self._ws_cm = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()
        self._recent_sends.clear()

    def _discard_send(self, key: _Key) -> None:
        """Remove a resolved key from the recent-sends deque."""
        try:
            self._recent_sends.remove(key)
        except ValueError:
            pass

    async def _teardown_transport(self) -> None:
        """Close the WebSocket transport without touching pending futures."""
        if self._ws_cm is not None:
            with contextlib.suppress(Exception):
                await self._ws_cm.__aexit__(None, None, None)
            self._ws_cm = None
            self._ws = None
        self._token = None

    # -- Send / receive primitives -------------------------------------------

    async def _send(self, action: str, data: dict[str, Any], *, dest: str = "") -> None:
        if self._ws is None or self._token is None:
            raise VcoWebSocketError("WebSocket is not connected")

        loop = asyncio.get_running_loop()
        last = self._send_times.get(dest, 0.0)
        elapsed = loop.time() - last
        if elapsed < _SEND_INTERVAL:
            await asyncio.sleep(_SEND_INTERVAL - elapsed)

        payload = {"action": action, "data": data, "token": self._token}
        logger.info("Sending action=%s", action)
        logger.debug("Sending payload: %s", data)
        await self._ws.send_text(json.dumps(payload, default=str))
        self._send_times[dest] = loop.time()

    async def _reader_loop(self) -> None:
        """Background task that reads messages and dispatches to futures."""
        logger.debug("Reader loop started")
        while not self._closing:
            try:
                if self._ws is None:
                    logger.warning("Reader loop: WebSocket is None, exiting")
                    return
                raw = await self._ws.receive_text()
                msg = json.loads(raw)
                self._dispatch(msg)
            except WebSocketDisconnect:
                if self._closing:
                    return
                logger.warning("WebSocket disconnected, attempting reconnect")
                try:
                    await self._reconnect()
                except Exception:
                    logger.exception("Reconnect failed, failing pending futures")
                    self._fail_pending(
                        VcoWebSocketDisconnectedError("Reconnect failed")
                    )
                    await self._teardown_transport()
                    return
            except asyncio.CancelledError:
                return
            except HTTPXWSException as exc:
                if self._closing:
                    return
                logger.exception("WebSocket transport error: %s", exc)
                self._fail_pending(
                    VcoWebSocketConnectionError(f"WebSocket transport error: {exc}")
                )
                await self._teardown_transport()
                return
            except Exception:
                if self._closing:
                    return
                logger.exception("Reader loop error")
                self._fail_pending(
                    VcoWebSocketError("Reader loop terminated unexpectedly")
                )
                await self._teardown_transport()
                return

    def _dispatch(self, msg: dict[str, Any]) -> None:
        """Match an incoming message to a pending future.

        Every VCO WebSocket message carries a top-level ``action`` and a
        ``data`` dict with ``logicalId`` and optionally ``test``.  We match
        against pending futures using progressively less specific keys:
        ``(action, logicalId, test)`` → ``(action, logicalId)`` → ``(action,)``.
        """

        action = msg.get("action", "")
        logger.info("Received action=%s", action or "(protocol)")
        logger.debug("Received WS message: %s", msg)
        if not action:
            # Protocol-level error responses (e.g. ValidationError) have no
            # ``action``.  Walk the recent-send deque (most-recent first) to
            # find the first key that still has a pending future.
            code = msg.get("code", "")
            if code:
                errors = msg.get("errors", [])
                detail = (
                    "; ".join(e.get("message", "") for e in errors)
                    if errors
                    else msg.get("message", code)
                )
                err: VcoWebSocketError = (
                    VcoWebSocketValidationError(f"VCO {code}: {detail}")
                    if code == "ValidationError"
                    else VcoWebSocketError(f"VCO {code}: {detail}")
                )
                logger.error("VCO protocol error: %s", err)
                for key in reversed(self._recent_sends):
                    future = self._pending.pop(key, None)
                    if future is not None and not future.done():
                        future.set_exception(err)
                        self._discard_send(key)
                        return
                logger.warning(
                    "Protocol error with no pending future to attribute: %s", err
                )
                return
            logger.debug("Ignoring message without action (keys: %s)", list(msg.keys()))
            return

        # The VCO sends unsolicited "connected" messages when device
        # connections are established; we don't track these.
        if action == "connected":
            logger.debug(
                "Ignoring connected message for %s",
                msg.get("data", {}).get("logicalId", ""),
            )
            return

        data = msg.get("data", {})
        raw_id = data.get("logicalId", "")
        try:
            logical_id: uuid.UUID | str = uuid.UUID(raw_id) if raw_id else ""
        except ValueError:
            logical_id = raw_id
        test = data.get("test", "")

        logger.debug(
            "Received action=%s logicalId=%s test=%s",
            action,
            logical_id,
            test,
        )

        candidates: list[_Key] = []
        if test:
            candidates.append((action, logical_id, test))
        if logical_id:
            candidates.append((action, logical_id))
        candidates.append((action,))

        for key in candidates:
            future = self._pending.pop(key, None)
            if future is not None and not future.done():
                logger.debug("Dispatched to future: key=%s", key)
                future.set_result(data)
                self._discard_send(key)
                return

        logger.warning(
            "No pending future for action=%s logicalId=%s test=%s (pending: %s)",
            action,
            logical_id,
            test,
            list(self._pending.keys()),
        )

    async def _reconnect(self) -> None:
        """Reconnect the WebSocket with exponential backoff.

        Pending futures from the old session are failed immediately — the
        VCO will not send responses for them on the new session.
        """
        stale_count = len(self._pending)
        if stale_count:
            logger.warning(
                "Failing %d stale pending futures from old session", stale_count
            )
            err = VcoWebSocketDisconnectedError(
                "WebSocket session lost; request must be resubmitted"
            )
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(err)
            self._pending.clear()
            self._recent_sends.clear()

        backoff = _RECONNECT_BACKOFF_BASE

        if self._ws_cm is not None:
            with contextlib.suppress(Exception):
                await self._ws_cm.__aexit__(None, None, None)
            self._ws_cm = None
            self._ws = None

        for attempt in range(1, _RECONNECT_MAX_ATTEMPTS + 1):
            try:
                logger.info("Reconnect attempt %d/%d", attempt, _RECONNECT_MAX_ATTEMPTS)
                await self._establish_connection()
                logger.info("Reconnected successfully")
                return
            except Exception:
                if attempt == _RECONNECT_MAX_ATTEMPTS:
                    raise
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _RECONNECT_MAX_BACKOFF)

    # -- Request helper ------------------------------------------------------

    async def _request(
        self,
        action: str,
        data: dict[str, Any],
        key: _Key,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        dest: str = "",
    ) -> dict[str, Any]:
        """Create a future, register it, send the message, and await."""
        logger.info("Request: action=%s key=%s timeout=%.1fs", action, key, timeout)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[key] = future
        self._recent_sends.append(key)

        try:
            await self._send(action, data, dest=dest)
            result = await asyncio.wait_for(future, timeout=timeout)
            logger.info("Response received: action=%s key=%s", action, key)
            return result
        except TimeoutError:
            self._pending.pop(key, None)
            self._discard_send(key)
            logger.error(
                "Timeout after %.1fs waiting for %s (key=%s, pending=%s)",
                timeout,
                action,
                key,
                list(self._pending.keys()),
            )
            raise VcoWebSocketTimeoutError(
                f"Timeout after {timeout:.1f}s waiting for {action} response (key={key})",
                timeout=timeout,
            ) from None
        except Exception:
            self._pending.pop(key, None)
            self._discard_send(key)
            raise

    # -- Helpers -------------------------------------------------------------

    @staticmethod
    def _diagnostic_data(
        logical_id: uuid.UUID,
        test: str,
        parameters: dict[str, str] | None,
        resformat: str | None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "logicalId": logical_id,
            "test": test,
        }
        if resformat:
            data["resformat"] = resformat
        if parameters:
            data["parameters"] = parameters
        return data

    # -- Public API ----------------------------------------------------------

    async def list_diagnostics(self, logical_id: uuid.UUID) -> dict[str, Any]:
        """Get available diagnostic tests for a device."""
        return await self._request(
            "listDiagnostics",
            {"logicalId": logical_id},
            ("listDiagnostics", logical_id),
            dest=str(logical_id),
        )

    async def run_diagnostic(
        self,
        logical_id: uuid.UUID,
        test: str,
        parameters: dict[str, str] | None = None,
        *,
        resformat: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = 0,
    ) -> dict[str, Any]:
        """Run a diagnostic test and await the result.

        Args:
            logical_id: Edge logical ID.
            test: Diagnostic test name (e.g. ``"ARP_DUMP"``).
            parameters: Test-specific parameters.
            resformat: Response format (e.g. ``"JSON"`` for structured output).
            timeout: Seconds to wait for the result.
            max_retries: Number of retry attempts through disconnects.
        """
        logger.info(
            "Running diagnostic: test=%s edge=%s resformat=%s timeout=%.1fs",
            test,
            logical_id,
            resformat,
            timeout,
        )
        data = self._diagnostic_data(logical_id, test, parameters, resformat)
        key: _Key = ("runDiagnostics", logical_id, test)
        last_err: Exception | None = None

        for attempt in range(1 + max_retries):
            try:
                return await self._request(
                    "runDiagnostics",
                    data,
                    key,
                    timeout=timeout,
                    dest=str(logical_id),
                )
            except VcoWebSocketValidationError:
                raise
            except VcoWebSocketError as exc:
                last_err = exc
                if attempt < max_retries:
                    logger.warning(
                        "Diagnostic attempt %d failed, retrying: %s",
                        attempt + 1,
                        exc,
                    )
                    continue
                raise

        # Should be unreachable, but satisfy type checker.
        assert last_err is not None
        raise last_err

    @overload
    async def submit_diagnostic(
        self,
        logical_id: uuid.UUID,
        test: str,
        parameters: dict[str, str] | None = ...,
        *,
        resformat: str | None = ...,
        timeout: float = ...,
        max_retries: int = ...,
    ) -> PendingDiagnostic[None]: ...

    @overload
    async def submit_diagnostic[T](
        self,
        logical_id: uuid.UUID,
        test: str,
        parameters: dict[str, str] | None = ...,
        *,
        resformat: str | None = ...,
        timeout: float = ...,
        max_retries: int = ...,
        context: T,
    ) -> PendingDiagnostic[T]: ...

    async def submit_diagnostic[T](
        self,
        logical_id: uuid.UUID,
        test: str,
        parameters: dict[str, str] | None = None,
        *,
        resformat: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = 0,
        context: T | None = None,
    ) -> PendingDiagnostic[T] | PendingDiagnostic[None]:
        """Submit a diagnostic test and return a PendingDiagnostic for later resolution.

        Args:
            logical_id: Edge logical ID.
            test: Diagnostic test name (e.g. ``"ROUTE_DUMP"``).
            parameters: Optional test-specific parameters.
            resformat: Response format (e.g. ``"JSON"`` for structured output).
            timeout: Seconds to wait when the result is awaited.
            max_retries: Number of retry attempts on transient failure (when awaited).
            context: Arbitrary data attached to the PendingDiagnostic for caller use.
        """
        data = self._diagnostic_data(logical_id, test, parameters, resformat)
        key: _Key = ("runDiagnostics", logical_id, test)
        future = await self._submit_send(key, data, dest=str(logical_id))

        async def resubmit() -> asyncio.Future[dict[str, Any]]:
            return await self._submit_send(key, data, dest=str(logical_id))

        def on_timeout() -> None:
            self._pending.pop(key, None)
            self._discard_send(key)

        if context is None:
            return PendingDiagnostic(
                future,
                test=test,
                logical_id=logical_id,
                context=None,
                timeout=timeout,
                max_retries=max_retries,
                _on_timeout=on_timeout,
                _resubmit=resubmit if max_retries else None,
            )

        return PendingDiagnostic(
            future,
            test=test,
            logical_id=logical_id,
            context=context,
            timeout=timeout,
            max_retries=max_retries,
            _on_timeout=on_timeout,
            _resubmit=resubmit if max_retries else None,
        )

    async def get_gw_route_table(
        self,
        logical_id: str,
        segment_id: int,
        enterprise_logical_id: uuid.UUID,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> list[GwRouteTableEntry]:
        """Get the gateway route table for a segment.

        Args:
            logical_id: Gateway logical ID (e.g. ``"gateway4a72...0722"``).
            segment_id: Segment ID.
            enterprise_logical_id: Enterprise logical ID.
            timeout: Seconds to wait for the result.
        """
        data: dict[str, Any] = {
            "segmentId": segment_id,
            "logicalId": logical_id,
            "enterpriseLogicalId": enterprise_logical_id,
        }
        response = await self._request(
            "getGwRouteTable",
            data,
            ("getGwRouteTable", logical_id),
            timeout=timeout,
            dest=logical_id,
        )
        return [GwRouteTableEntry(**entry) for entry in response.get("result", [])]

    async def _submit_send(
        self,
        key: _Key,
        data: dict[str, Any],
        *,
        dest: str,
    ) -> asyncio.Future[dict[str, Any]]:
        """Register a future for *key*, send the message, and return the future."""
        existing = self._pending.get(key)
        if existing is not None and not existing.done():
            raise VcoWebSocketError(f"Diagnostic already pending for key {key}")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[key] = future
        self._recent_sends.append(key)
        await self._send("runDiagnostics", data, dest=dest)
        return future
