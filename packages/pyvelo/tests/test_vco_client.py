"""Tests for VcoClient.portal() and its list and pagination variants."""

import asyncio
import json
from unittest.mock import patch

import httpx
import pytest
from pyvelo.client.exceptions import (
    VcoApiError,
    VcoEmptyResultError,
    VcoOperationError,
    VcoRateLimitError,
)
from pyvelo.core import (
    PaginatedVcoModel,
    VcoModel,
)


class SampleRequest(VcoModel):
    enterprise_id: int


class SampleResult(VcoModel):
    name: str
    value: int


class SamplePaginatedRequest(PaginatedVcoModel):
    enterprise_id: int


def paged_handler(json_rpc_response, page_count, sent_cursors):
    """Serve *page_count* one-item pages linked by ``page<N>`` cursors."""

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = json.loads(request.content)["params"].get("nextPageLink")
        sent_cursors.append(cursor)
        index = 1 if cursor is None else int(cursor.removeprefix("page"))
        more = index < page_count
        meta = {"more": more, "nextPageLink": f"page{index + 1}" if more else None}
        return httpx.Response(
            200,
            json=json_rpc_response(
                result={
                    "metaData": meta,
                    "data": [{"name": f"item{index}", "value": index}],
                }
            ),
        )

    return handler


class TestPortal:
    async def test_successful_request(self, mock_vco_client, json_rpc_response):
        """portal() should deserialize a successful JSON-RPC response."""

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["method"] == "test/getStuff"
            assert body["params"]["enterpriseId"] == 42
            return httpx.Response(
                200,
                json=json_rpc_response(result={"name": "test", "value": 123}),
            )

        client = await mock_vco_client(handler)
        result = await client.portal(
            "test/getStuff", SampleRequest(enterprise_id=42), SampleResult
        )
        assert result.name == "test"
        assert result.value == 123

    async def test_error_response_raises(self, mock_vco_client, json_rpc_response):
        """portal() should raise VcoOperationError on JSON-RPC error."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=json_rpc_response(
                    error={"code": -32600, "message": "Invalid request"}
                ),
            )

        client = await mock_vco_client(handler)
        with pytest.raises(VcoOperationError) as exc_info:
            await client.portal(
                "test/getStuff", SampleRequest(enterprise_id=1), SampleResult
            )
        assert exc_info.value.code == -32600
        assert exc_info.value.error == "Invalid request"
        assert exc_info.value.operation == "test/getStuff"
        assert isinstance(exc_info.value.__cause__, VcoApiError)

    async def test_null_result_raises(self, mock_vco_client, json_rpc_response):
        """portal() should raise when result is null."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=json_rpc_response(result=None))

        client = await mock_vco_client(handler)
        with pytest.raises(VcoEmptyResultError):
            await client.portal(
                "test/getStuff", SampleRequest(enterprise_id=1), SampleResult
            )

    async def test_auth_header_sent(self, mock_vco_client, json_rpc_response):
        """portal() should include the auth token in requests."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert "Authorization" in request.headers
            assert request.headers["Authorization"] == "Token test-token"
            return httpx.Response(
                200,
                json=json_rpc_response(result={"name": "x", "value": 0}),
            )

        client = await mock_vco_client(handler)
        await client.portal(
            "test/getStuff", SampleRequest(enterprise_id=1), SampleResult
        )

    async def test_request_serialized_as_camel_case(
        self, mock_vco_client, json_rpc_response
    ):
        """Request params should be serialized with camelCase aliases."""

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert "enterpriseId" in body["params"]
            assert "enterprise_id" not in body["params"]
            return httpx.Response(
                200,
                json=json_rpc_response(result={"name": "x", "value": 0}),
            )

        client = await mock_vco_client(handler)
        await client.portal(
            "test/getStuff", SampleRequest(enterprise_id=1), SampleResult
        )


class TestPortalPaginated:
    async def test_single_page(self, mock_vco_client, json_rpc_response):
        """portal_paginated() with a single page of results."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=json_rpc_response(
                    result={
                        "metaData": {"limit": 100, "more": False},
                        "data": [
                            {"name": "item1", "value": 1},
                            {"name": "item2", "value": 2},
                        ],
                    }
                ),
            )

        client = await mock_vco_client(handler)
        pages = []
        async for page in client.portal_paginated(
            "test/listStuff",
            SamplePaginatedRequest(enterprise_id=1),
            SampleResult,
        ):
            pages.append(page)

        assert len(pages) == 1
        assert len(pages[0]) == 2
        assert pages[0][0].name == "item1"
        assert pages[0][1].value == 2

    async def test_multi_page(self, mock_vco_client, json_rpc_response):
        """portal_paginated() should follow pagination links."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                return httpx.Response(
                    200,
                    json=json_rpc_response(
                        result={
                            "metaData": {
                                "limit": 1,
                                "more": True,
                                "nextPageLink": "page2",
                            },
                            "data": [{"name": "item1", "value": 1}],
                        }
                    ),
                )
            else:
                return httpx.Response(
                    200,
                    json=json_rpc_response(
                        result={
                            "metaData": {"limit": 1, "more": False},
                            "data": [{"name": "item2", "value": 2}],
                        }
                    ),
                )

        client = await mock_vco_client(handler)
        pages = []
        async for page in client.portal_paginated(
            "test/listStuff",
            SamplePaginatedRequest(enterprise_id=1),
            SampleResult,
        ):
            pages.append(page)

        assert len(pages) == 2
        assert call_count == 2

    async def test_error_response_raises(self, mock_vco_client, json_rpc_response):
        """portal_paginated() should raise VcoOperationError on error."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=json_rpc_response(error={"code": -1, "message": "fail"}),
            )

        client = await mock_vco_client(handler)
        with pytest.raises(VcoOperationError) as exc_info:
            async for _ in client.portal_paginated(
                "test/listStuff",
                SamplePaginatedRequest(enterprise_id=1),
                SampleResult,
            ):
                pass
        assert exc_info.value.code == -1
        assert exc_info.value.operation == "test/listStuff"
        assert isinstance(exc_info.value.__cause__, VcoApiError)

    async def test_records_page_status_on_callers_request(
        self, mock_vco_client, json_rpc_response
    ):
        """A caller that stops after one page can resume from the recorded cursor."""
        sent = []
        client = await mock_vco_client(paged_handler(json_rpc_response, 2, sent))
        request = SamplePaginatedRequest(enterprise_id=1)

        async for page in client.portal_paginated(
            "test/listStuff", request, SampleResult
        ):
            assert [item.value for item in page] == [1]
            assert request._page_has_more is True
            assert request._page_next_link == "page2"
            break

        assert sent == [None]
        assert "next_page_link" not in request.model_dump(exclude_unset=True)

    async def test_terminal_page_clears_status(
        self, mock_vco_client, json_rpc_response
    ):
        sent = []
        client = await mock_vco_client(paged_handler(json_rpc_response, 2, sent))
        request = SamplePaginatedRequest(enterprise_id=1)

        pages = [
            page
            async for page in client.portal_paginated(
                "test/listStuff", request, SampleResult
            )
        ]

        assert len(pages) == 2
        assert sent == [None, "page2"]
        assert request._page_has_more is False
        assert request._page_next_link is None

    async def test_more_without_cursor_raises(self, mock_vco_client, json_rpc_response):
        """An incomplete page with no cursor must not resend the same request."""
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                json=json_rpc_response(result={"metaData": {"more": True}, "data": []}),
            )

        client = await mock_vco_client(handler)
        with pytest.raises(VcoOperationError, match="omitted its continuation cursor"):
            async for _ in client.portal_paginated(
                "test/listStuff", SamplePaginatedRequest(enterprise_id=1), SampleResult
            ):
                pass
        assert calls == 1


class TestPortalPaginatedList:
    async def test_collects_every_page_without_cap(
        self, mock_vco_client, json_rpc_response
    ):
        sent = []
        client = await mock_vco_client(paged_handler(json_rpc_response, 3, sent))
        request = SamplePaginatedRequest(enterprise_id=1)

        items = await client.portal_paginated_list(
            "test/listStuff", request, SampleResult
        )

        assert [item.value for item in items] == [1, 2, 3]
        assert sent == [None, "page2", "page3"]
        assert request._page_has_more is False
        assert request._page_next_link is None

    @pytest.mark.parametrize("max_pages", [1, 2])
    async def test_max_pages_stops_and_keeps_resume_cursor(
        self, mock_vco_client, json_rpc_response, max_pages
    ):
        sent = []
        client = await mock_vco_client(paged_handler(json_rpc_response, 3, sent))
        request = SamplePaginatedRequest(enterprise_id=1)
        request._max_pages = max_pages

        items = await client.portal_paginated_list(
            "test/listStuff", request, SampleResult
        )

        assert [item.value for item in items] == list(range(1, max_pages + 1))
        assert len(sent) == max_pages
        assert request._page_has_more is True
        assert request._page_next_link == f"page{max_pages + 1}"

    async def test_max_pages_reaching_last_page_reports_complete(
        self, mock_vco_client, json_rpc_response
    ):
        sent = []
        client = await mock_vco_client(paged_handler(json_rpc_response, 2, sent))
        request = SamplePaginatedRequest(enterprise_id=1)
        request._max_pages = 2

        items = await client.portal_paginated_list(
            "test/listStuff", request, SampleResult
        )

        assert len(items) == 2
        assert request._page_has_more is False
        assert request._page_next_link is None

    async def test_resumes_from_recorded_cursor(
        self, mock_vco_client, json_rpc_response
    ):
        sent = []
        client = await mock_vco_client(paged_handler(json_rpc_response, 3, sent))
        request = SamplePaginatedRequest(enterprise_id=1)
        request._max_pages = 1
        await client.portal_paginated_list("test/listStuff", request, SampleResult)

        next_page_link = request._page_next_link
        assert next_page_link is not None
        resumed = SamplePaginatedRequest(enterprise_id=1, next_page_link=next_page_link)
        resumed._max_pages = 1
        items = await client.portal_paginated_list(
            "test/listStuff", resumed, SampleResult
        )

        assert [item.value for item in items] == [2]
        assert sent == [None, "page2"]
        assert resumed._page_next_link == "page3"


class TestPortalList:
    async def test_successful_list_request(self, mock_vco_client, json_rpc_response):
        """portal_list() should deserialize a JSON-RPC response with array result."""

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["method"] == "test/listStuff"
            return httpx.Response(
                200,
                json=json_rpc_response(
                    result=[
                        {"name": "item1", "value": 1},
                        {"name": "item2", "value": 2},
                    ]
                ),
            )

        client = await mock_vco_client(handler)
        result = await client.portal_list(
            "test/listStuff", SampleRequest(enterprise_id=42), SampleResult
        )
        assert len(result) == 2
        assert result[0].name == "item1"
        assert result[1].value == 2

    async def test_error_response_raises(self, mock_vco_client, json_rpc_response):
        """portal_list() should raise VcoOperationError on JSON-RPC error."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=json_rpc_response(
                    error={"code": -32600, "message": "Invalid request"}
                ),
            )

        client = await mock_vco_client(handler)
        with pytest.raises(VcoOperationError) as exc_info:
            await client.portal_list(
                "test/listStuff", SampleRequest(enterprise_id=1), SampleResult
            )
        assert exc_info.value.code == -32600
        assert isinstance(exc_info.value.__cause__, VcoApiError)

    async def test_null_result_raises(self, mock_vco_client, json_rpc_response):
        """portal_list() should raise when result is null."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=json_rpc_response(result=None))

        client = await mock_vco_client(handler)
        with pytest.raises(VcoEmptyResultError):
            await client.portal_list(
                "test/listStuff", SampleRequest(enterprise_id=1), SampleResult
            )

    async def test_empty_list(self, mock_vco_client, json_rpc_response):
        """portal_list() should handle empty array result."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=json_rpc_response(result=[]),
            )

        client = await mock_vco_client(handler)
        result = await client.portal_list(
            "test/listStuff", SampleRequest(enterprise_id=1), SampleResult
        )
        assert result == []

    async def test_complete_paginated_envelope_is_unwrapped(
        self, mock_vco_client, json_rpc_response
    ):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=json_rpc_response(
                    result={
                        "metaData": {"more": False},
                        "data": [{"name": "item1", "value": 1}],
                    }
                ),
            )

        client = await mock_vco_client(handler)
        result = await client.portal_list(
            "test/listStuff", SampleRequest(enterprise_id=1), SampleResult
        )
        assert [item.value for item in result] == [1]

    @pytest.mark.parametrize(
        "request_model,message",
        [
            (SampleRequest(enterprise_id=1), "pagination is unsupported"),
            (SamplePaginatedRequest(enterprise_id=1), "portal_paginated_list"),
        ],
    )
    async def test_incomplete_paginated_envelope_raises_without_following(
        self, mock_vco_client, json_rpc_response, request_model, message
    ):
        sent = []
        client = await mock_vco_client(paged_handler(json_rpc_response, 2, sent))

        with pytest.raises(VcoOperationError, match=message):
            await client.portal_list("test/listStuff", request_model, SampleResult)
        assert sent == [None]


class TestRetry429:
    async def test_retries_on_429_then_succeeds(
        self, mock_vco_client, json_rpc_response
    ):
        """_post() should retry on 429 and succeed when server recovers."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return httpx.Response(429, text="Too Many Requests")
            return httpx.Response(
                200,
                json=json_rpc_response(result={"name": "ok", "value": 1}),
            )

        with patch("pyvelo.client.vco_client.asyncio.sleep", return_value=None):
            client = await mock_vco_client(handler, max_retries=3)
            result = await client.portal(
                "test/getStuff", SampleRequest(enterprise_id=1), SampleResult
            )

        assert result.name == "ok"
        assert call_count == 3

    async def test_respects_retry_after_header(
        self, mock_vco_client, json_rpc_response
    ):
        """_post() should use Retry-After header value as delay."""
        delays: list[float] = []

        async def mock_sleep(seconds: float) -> None:
            delays.append(seconds)

        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(
                    429,
                    text="Too Many Requests",
                    headers={"Retry-After": "2.5"},
                )
            return httpx.Response(
                200,
                json=json_rpc_response(result={"name": "ok", "value": 1}),
            )

        with patch("pyvelo.client.vco_client.asyncio.sleep", side_effect=mock_sleep):
            client = await mock_vco_client(handler, max_retries=3)
            await client.portal(
                "test/getStuff", SampleRequest(enterprise_id=1), SampleResult
            )

        assert len(delays) == 1
        assert delays[0] == 2.5

    async def test_raises_after_retries_exhausted(self, mock_vco_client):
        """_post() should raise VcoRateLimitError when max retries exhausted."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="Too Many Requests")

        with patch("pyvelo.client.vco_client.asyncio.sleep", return_value=None):
            client = await mock_vco_client(handler, max_retries=2)
            with pytest.raises(VcoRateLimitError) as exc_info:
                await client.portal(
                    "test/getStuff", SampleRequest(enterprise_id=1), SampleResult
                )
            assert exc_info.value.attempts == 3

    async def test_non_429_errors_not_retried(self, mock_vco_client, json_rpc_response):
        """_post() should not retry on non-429 HTTP errors."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(500, text="Internal Server Error")

        client = await mock_vco_client(handler, max_retries=3)
        # 500 goes through to JSON parsing, which fails with ValidationError
        with pytest.raises(Exception):  # noqa: B017
            await client.portal(
                "test/getStuff", SampleRequest(enterprise_id=1), SampleResult
            )
        assert call_count == 1


class TestConcurrencySemaphore:
    async def test_limits_concurrent_requests(self, mock_vco_client, json_rpc_response):
        """Semaphore should limit the number of concurrent in-flight requests."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=json_rpc_response(result={"name": "x", "value": 0}),
            )

        client = await mock_vco_client(
            handler, max_concurrent_requests=2, max_retries=0
        )

        active_count = 0
        peak_count = 0
        count_lock = asyncio.Lock()

        original_http_post = client._http_client().post

        async def counting_post(*args, **kwargs):
            nonlocal active_count, peak_count
            async with count_lock:
                active_count += 1
                peak_count = max(peak_count, active_count)
            await asyncio.sleep(0.05)
            result = await original_http_post(*args, **kwargs)
            async with count_lock:
                active_count -= 1
            return result

        client._http_client().post = counting_post  # type: ignore[assignment]

        tasks = [
            asyncio.create_task(
                client.portal("test/get", SampleRequest(enterprise_id=i), SampleResult)
            )
            for i in range(6)
        ]
        await asyncio.gather(*tasks)

        assert peak_count <= 2


class TestRateLimiter:
    async def test_throttles_requests_within_window(
        self, mock_vco_client, json_rpc_response
    ):
        """Rate limiter should throttle when window budget is exhausted."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=json_rpc_response(result={"name": "x", "value": 0}),
            )

        sleep_calls: list[float] = []

        async def mock_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        with patch("pyvelo.client.vco_client.asyncio.sleep", side_effect=mock_sleep):
            client = await mock_vco_client(
                handler, max_requests_per_window=3, max_retries=0
            )

            # Fire 4 requests -- the 4th should trigger a rate-limit sleep
            for i in range(4):
                await client.portal(
                    "test/get", SampleRequest(enterprise_id=i), SampleResult
                )

        # At least one sleep call should have been made for rate limiting
        assert len(sleep_calls) >= 1

    async def test_no_throttle_when_disabled(self, mock_vco_client, json_rpc_response):
        """No rate limiting when max_requests_per_window is not set."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=json_rpc_response(result={"name": "x", "value": 0}),
            )

        sleep_calls: list[float] = []

        async def mock_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        with patch("pyvelo.client.vco_client.asyncio.sleep", side_effect=mock_sleep):
            client = await mock_vco_client(handler, max_retries=0)
            for i in range(10):
                await client.portal(
                    "test/get", SampleRequest(enterprise_id=i), SampleResult
                )

        assert len(sleep_calls) == 0


class TestVcoAuth:
    """VcoAuth should accept tokens with or without a prefix."""

    async def test_raw_token_gets_prefix(self, mock_vco_client, json_rpc_response):
        """A bare token should be sent as 'Token <value>'."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Token test-token"
            return httpx.Response(
                200,
                json=json_rpc_response(result={"name": "x", "value": 0}),
            )

        client = await mock_vco_client(handler)
        await client.portal(
            "test/getStuff", SampleRequest(enterprise_id=1), SampleResult
        )

    async def test_token_prefix_not_duplicated(self, json_rpc_response):
        """A token that already starts with 'Token ' must not be double-prefixed."""
        from pyvelo.client import VcoClient
        from pyvelo.client.auth import VcoAuth
        from pyvelo.testing import make_transport

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Token already-prefixed"
            return httpx.Response(
                200,
                json=json_rpc_response(result={"name": "x", "value": 0}),
            )

        client = VcoClient("test.velocloud.net", "Token already-prefixed")
        client._http = httpx.AsyncClient(
            auth=VcoAuth("Token already-prefixed"),
            transport=make_transport(handler),
            base_url="https://test.velocloud.net",
        )
        await client.portal(
            "test/getStuff", SampleRequest(enterprise_id=1), SampleResult
        )

    async def test_custom_prefix_preserved(self, json_rpc_response):
        """A token with an arbitrary prefix like 'RandomPrefix ' must be sent verbatim."""
        from pyvelo.client import VcoClient
        from pyvelo.client.auth import VcoAuth
        from pyvelo.testing import make_transport

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "RandomPrefix my-jwt"
            return httpx.Response(
                200,
                json=json_rpc_response(result={"name": "x", "value": 0}),
            )

        client = VcoClient("test.velocloud.net", "RandomPrefix my-jwt")
        client._http = httpx.AsyncClient(
            auth=VcoAuth("RandomPrefix my-jwt"),
            transport=make_transport(handler),
            base_url="https://test.velocloud.net",
        )
        await client.portal(
            "test/getStuff", SampleRequest(enterprise_id=1), SampleResult
        )
