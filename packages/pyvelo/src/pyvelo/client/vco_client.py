"""Concrete VcoClient with retry, rate-limiting, and concurrency control."""

import asyncio
import contextlib
import json
import logging
import random
import time
from typing import Any, override
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from pyvelo.client.exceptions import VcoRateLimitError
from pyvelo.client.protocol import ExchangeHook, VcoClientProtocol
from pyvelo.client.request_observation import RequestObservation, RequestObserver

logger = logging.getLogger(__name__)

_RETRY_BASE_DELAY = 1.0
_RETRY_MAX_DELAY = 60.0
_RETRY_JITTER = 0.5
_DEFAULT_MAX_RETRIES = 5
_RATE_LIMIT_WINDOW = 5.0


class VcoClient(VcoClientProtocol):
    """Concrete VcoClient with retry, rate-limiting, and concurrency control."""

    def __init__(
        self,
        vco_fqdn: str,
        token: str,
        *,
        verify_ssl: bool = True,
        timeout: float = 60.0,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        max_concurrent_requests: int | None = None,
        max_requests_per_window: int | None = None,
        exchange_hook: ExchangeHook | None = None,
        request_observer: RequestObserver | None = None,
    ) -> None:
        self._vco_fqdn = vco_fqdn
        self._token = SecretStr(token)
        self._verify_ssl = verify_ssl
        self._timeout = timeout
        self._max_retries = max_retries
        self._exchange_hook = exchange_hook
        self._request_observer = request_observer
        self._ws_cleanup_tasks: set[asyncio.Task[None]] = set()

        if max_concurrent_requests is not None:
            self._concurrency_semaphore = asyncio.Semaphore(max_concurrent_requests)
            logger.debug(
                "Concurrency semaphore configured: %d max in-flight requests",
                max_concurrent_requests,
            )
        else:
            self._concurrency_semaphore = None

        if max_requests_per_window is not None:
            self._rate_limit = max_requests_per_window
            logger.debug(
                "Rate limiter configured: %d requests per %.0fs window",
                max_requests_per_window,
                _RATE_LIMIT_WINDOW,
            )
        else:
            self._rate_limit = None

        self._rate_lock = asyncio.Lock()
        self._window_start = time.monotonic()
        self._window_count = 0

    # -- Retry / rate-limit infrastructure ----------------------------------

    async def _apply_rate_limit(self) -> None:
        """Sleep if the current rate-limit window is exhausted."""
        if self._rate_limit is None:
            return

        async with self._rate_lock:
            now = time.monotonic()
            elapsed = now - self._window_start

            if elapsed >= _RATE_LIMIT_WINDOW:
                logger.debug(
                    "Rate-limit window reset (previous count: %d)", self._window_count
                )
                self._window_start = now
                self._window_count = 0

            if self._window_count >= self._rate_limit:
                sleep_time = _RATE_LIMIT_WINDOW - elapsed
                if sleep_time > 0:
                    logger.info(
                        "Rate limit reached (%d/%d), sleeping %.2fs",
                        self._window_count,
                        self._rate_limit,
                        sleep_time,
                    )
                    await asyncio.sleep(sleep_time)
                self._window_start = time.monotonic()
                self._window_count = 0
                logger.debug("Rate-limit window reset after sleep")

            self._window_count += 1

    @override
    async def _send_request(
        self,
        http_method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        content: str | None = None,
    ) -> httpx.Response:
        """Send an HTTP request with rate limiting, concurrency control, and 429 retry."""
        started = time.monotonic()
        attempts = rate_limited = 0
        status = None
        outcome = "transport_error"
        limiter_wait = retry_wait = http_seconds = 0.0
        retry_delays: list[float] = []
        retry_after_delays: list[float] = []
        try:
            if (
                self._concurrency_semaphore is not None
                and self._concurrency_semaphore.locked()
            ):
                logger.debug("Waiting for concurrency semaphore")

            async with self._concurrency_semaphore or contextlib.nullcontext():
                for attempt in range(self._max_retries + 1):
                    waiting = time.monotonic()
                    try:
                        await self._apply_rate_limit()
                    finally:
                        limiter_wait += time.monotonic() - waiting

                    # A client that has not connected cannot dispatch HTTP.
                    self._http_client()
                    attempts += 1
                    sending = time.monotonic()
                    try:
                        response = await super()._send_request(
                            http_method,
                            url,
                            params=params,
                            json_data=json_data,
                            content=content,
                        )
                    finally:
                        http_seconds += time.monotonic() - sending
                    status = response.status_code

                    if response.status_code != 429:
                        if attempt > 0:
                            logger.debug("Request succeeded after %d retries", attempt)
                        outcome = "http_response"
                        return response

                    rate_limited += 1
                    if attempt == self._max_retries:
                        outcome = "retries_exhausted"
                        logger.error(
                            "429 retries exhausted after %d attempts",
                            attempt + 1,
                        )
                        raise VcoRateLimitError(attempts=attempt + 1)

                    retry_after = response.headers.get("Retry-After")
                    retry_after_delay: float | None = None
                    if retry_after is not None:
                        try:
                            retry_after_delay = float(retry_after)
                        except ValueError, TypeError:
                            retry_after_delay = None
                    if retry_after_delay is not None:
                        retry_after_delays.append(retry_after_delay)
                    delay = (
                        retry_after_delay
                        if retry_after_delay is not None
                        else min(_RETRY_BASE_DELAY * (2**attempt), _RETRY_MAX_DELAY)
                        + random.uniform(0, _RETRY_JITTER)
                    )

                    logger.warning(
                        "429 received (attempt %d/%d), retrying in %.2fs",
                        attempt + 1,
                        self._max_retries + 1,
                        delay,
                    )
                    retry_delays.append(delay)
                    waiting = time.monotonic()
                    try:
                        await asyncio.sleep(delay)
                    finally:
                        retry_wait += time.monotonic() - waiting

            raise RuntimeError("Unreachable")  # pragma: no cover
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            if self._request_observer is not None:
                rpc_method = ""
                has_cursor = False
                try:
                    payload = json.loads(content) if content is not None else json_data
                    if isinstance(payload, dict):
                        method = payload.get("method")
                        rpc_method = method[:128] if isinstance(method, str) else ""
                        arguments = payload.get("params")
                        if isinstance(arguments, dict):
                            has_cursor = bool(arguments.get("nextPageLink"))
                except ValueError, TypeError:
                    pass
                try:
                    observation = RequestObservation(
                        http_method=http_method.upper(),
                        endpoint=urlsplit(url).path,
                        rpc_method=rpc_method,
                        has_cursor=has_cursor,
                        http_attempts=attempts,
                        http_429_count=rate_limited,
                        last_http_status=status,
                        outcome=outcome,
                        elapsed_seconds=time.monotonic() - started,
                        http_seconds=http_seconds,
                        limiter_wait_seconds=limiter_wait,
                        retry_wait_seconds=retry_wait,
                        retry_delays=tuple(retry_delays),
                        retry_after_delays=tuple(retry_after_delays),
                        max_requests_per_window=self._rate_limit,
                        rate_limit_window_seconds=_RATE_LIMIT_WINDOW,
                        max_retries=self._max_retries,
                    )
                    self._request_observer(observation)
                except Exception:
                    # Telemetry must not replace the source outcome or expose
                    # observer exceptions, payloads or credential values.
                    logger.warning("VCO request observer failed")
