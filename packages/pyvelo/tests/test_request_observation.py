"""Count actual HTTP attempts and waits without changing source retry policy."""

import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import TypedDict, Unpack, cast
from unittest.mock import patch

import httpx
import pytest
from pyvelo.client import RequestObservation, RequestObserver, VcoClient
from pyvelo.client.exceptions import (
    VcoClientNotInitializedError,
    VcoOperationError,
    VcoRateLimitError,
)
from pyvelo.client.protocol import VcoClientProtocol
from pyvelo.core import PaginatedVcoModel, VcoModel

from ._type_support import parse_json_object


class Request(VcoModel):
    enterprise_id: int = 42


class PageRequest(PaginatedVcoModel):
    enterprise_id: int = 42


class Result(VcoModel):
    value: int


class ClientOptions(TypedDict, total=False):
    max_retries: int
    max_concurrent_requests: int | None
    max_requests_per_window: int | None


@asynccontextmanager
async def observed_client(
    handler: Callable[[httpx.Request], httpx.Response],
    observer: RequestObserver,
    **options: Unpack[ClientOptions],
) -> AsyncGenerator[VcoClient]:
    client = VcoClient(
        "example.test", "private-token", request_observer=observer, **options
    )
    async with httpx.AsyncClient(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    ) as http:
        setattr(client, "_http", http)
        yield client


async def test_retry_after_and_limiter_waits_match_actual_attempts():
    clock: list[float] = [0.0]
    sleeps: list[float] = []
    records: list[RequestObservation] = []
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        clock[0] += 0.2
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "2.5"})
        return httpx.Response(200, json={"result": {"value": 1}})

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    with (
        patch("pyvelo.client.vco_client.time.monotonic", side_effect=lambda: clock[0]),
        patch("pyvelo.client.vco_client.asyncio.sleep", side_effect=sleep),
    ):
        async with observed_client(
            handle, records.append, max_requests_per_window=1
        ) as client:
            assert (await client.portal("test/get", Request(), Result)).value == 1
    record = records[0]
    assert len(records) == 1 and len(calls) == record.http_attempts == 2
    assert record.http_429_count == 1
    assert record.retry_delays == record.retry_after_delays == (2.5,)
    assert record.retry_wait_seconds == pytest.approx(2.5)
    assert record.limiter_wait_seconds == pytest.approx(2.3)
    assert record.http_seconds == pytest.approx(0.4)
    assert record.elapsed_seconds == pytest.approx(5.2)
    assert record.max_requests_per_window == 1 and record.rate_limit_window_seconds == 5
    assert record.max_retries == 5 and record.last_http_status == 200
    assert sleeps == pytest.approx([2.5, 2.3])
    assert "private-token" not in json.dumps(asdict(record))
    assert record.rpc_method == "test/get"


async def test_exhaustion_records_all_attempts_with_no_extra_retry():
    records: list[RequestObservation] = []

    async def sleep(_seconds: float) -> None:
        pass

    with patch("pyvelo.client.vco_client.asyncio.sleep", side_effect=sleep):
        async with observed_client(
            lambda _: httpx.Response(429, headers={"Retry-After": "0"}),
            records.append,
            max_retries=2,
        ) as client:
            with pytest.raises(VcoRateLimitError) as error:
                _ = await client.portal("test/get", Request(), Result)
    record = records[0]
    assert error.value.attempts == record.http_attempts == record.http_429_count == 3
    assert len(record.retry_delays) == 2
    assert record.outcome == "retries_exhausted"


async def test_timeout_is_one_attempt_and_does_not_trigger_retries():
    records: list[RequestObservation] = []

    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private diagnostic", request=request)

    async with observed_client(handle, records.append) as client:
        with pytest.raises(httpx.ReadTimeout):
            _ = await client.portal("test/get", Request(), Result)
    assert records[0].http_attempts == 1
    assert records[0].outcome == "transport_error"
    assert records[0].last_http_status is None
    assert "private diagnostic" not in repr(records)


async def test_cancelled_retry_wait_is_accounted_and_not_retried():
    records: list[RequestObservation] = []
    sleeping = asyncio.Event()

    async def sleep(_seconds: float) -> None:
        sleeping.set()
        _ = await asyncio.Event().wait()

    with patch("pyvelo.client.vco_client.asyncio.sleep", side_effect=sleep):
        async with observed_client(
            lambda _: httpx.Response(429, headers={"Retry-After": "3"}), records.append
        ) as client:
            task = asyncio.create_task(client.portal("test/get", Request(), Result))
            _ = await sleeping.wait()
            _ = task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert records[0].http_attempts == 1
    assert records[0].outcome == "cancelled"
    assert records[0].retry_delays == (3.0,)
    assert records[0].retry_wait_seconds >= 0


async def test_pagination_records_separate_source_requests_and_cursor_presence():
    records: list[RequestObservation] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = parse_json_object(request.content)
        params = body["params"]
        assert isinstance(params, dict)
        continued = "nextPageLink" in params
        return httpx.Response(
            200,
            json={
                "result": {
                    "data": [{"value": 1}],
                    "metaData": {
                        "more": not continued,
                        "nextPageLink": None if continued else "private-cursor",
                    },
                }
            },
        )

    async with observed_client(handle, records.append) as client:
        pages = [
            page
            async for page in client.portal_paginated(
                "test/list", PageRequest(), Result
            )
        ]
    assert len(pages) == len(records) == 2
    assert sum(r.http_attempts for r in records) == 2
    assert [r.has_cursor for r in records] == [False, True]
    assert "private-cursor" not in repr(records)


async def test_observer_failure_does_not_change_source_result_or_log_exception(
    caplog: pytest.LogCaptureFixture,
):
    def observer(_record: RequestObservation) -> None:
        raise ValueError("private credential")

    async with observed_client(
        lambda _: httpx.Response(200, json={"result": {"value": 1}}), observer
    ) as client:
        assert (await client.portal("test/get", Request(), Result)).value == 1
    assert "private credential" not in caplog.text
    assert "observer failed" in caplog.text


async def test_observer_preparation_failure_preserves_source_error(
    caplog: pytest.LogCaptureFixture,
):
    records: list[RequestObservation] = []
    failure = httpx.InvalidURL("original transport error")
    async with observed_client(lambda _: httpx.Response(200), records.append) as client:
        with patch.object(VcoClientProtocol, "_send_request", side_effect=failure):
            with pytest.raises(httpx.InvalidURL) as error:
                send_request = cast(
                    Callable[[str, str], Awaitable[httpx.Response]],
                    getattr(client, "_send_request"),
                )
                _ = await send_request("POST", "https://[invalid")
    assert error.value is failure
    assert records == []
    assert "observer failed" in caplog.text


async def test_uninitialized_client_records_no_http_attempt():
    records: list[RequestObservation] = []
    client = VcoClient("example.test", "private-token", request_observer=records.append)
    with pytest.raises(VcoClientNotInitializedError):
        _ = await client.portal("test/get", Request(), Result)
    assert records[0].http_attempts == 0


async def test_observation_strips_host_query_and_payload():
    records: list[RequestObservation] = []
    async with observed_client(lambda _: httpx.Response(200), records.append) as client:
        send_request = cast(
            Callable[[str, str], Awaitable[httpx.Response]],
            getattr(client, "_send_request"),
        )
        _ = await send_request(
            "GET", "https://example.test/rest/test?token=private-query"
        )
    assert records[0].endpoint == "/rest/test"
    assert "private-query" not in repr(records)


async def test_rpc_error_is_distinct_from_http_success():
    records: list[RequestObservation] = []
    async with observed_client(
        lambda _: httpx.Response(200, json={"error": {"code": 1, "message": "failed"}}),
        records.append,
    ) as client:
        with pytest.raises(VcoOperationError):
            _ = await client.portal("test/get", Request(), Result)
    assert records[0].outcome == "http_response"
    assert records[0].last_http_status == 200
