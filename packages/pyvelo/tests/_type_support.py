"""Typing helpers shared by the test suite."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from typing import Protocol, cast

import httpx
from pydantic import MISSING, BaseModel
from pyvelo.client import VcoClient
from pyvelo.client.protocol import VcoClientProtocol
from pyvelo.config.resolvers.segment import SegmentResolver

type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


class JsonRpcResponseFactory(Protocol):
    def __call__(
        self,
        result: JsonValue = None,
        error: JsonObject | None = None,
        id: int = 1,
    ) -> JsonObject: ...


class MockVcoClientFactory(Protocol):
    def __call__(
        self,
        handler: Callable[[httpx.Request], httpx.Response],
        *,
        max_retries: int = 5,
        max_concurrent_requests: int | None = None,
        max_requests_per_window: int | None = None,
    ) -> Awaitable[VcoClient]: ...


class SegmentResolverFactory(Protocol):
    def __call__(
        self, segments: Sequence[Mapping[str, object]]
    ) -> Awaitable[SegmentResolver]: ...


def parse_json_object(content: bytes | str) -> JsonObject:
    """Decode a JSON object at the stdlib parser's untyped boundary."""
    return cast(JsonObject, json.loads(content))


class MockPaginatedClient[RequestT: BaseModel, ItemT: BaseModel]:
    def __init__(
        self,
        pages: list[list[ItemT]],
        *,
        on_page: Callable[[int], None] | None = None,
    ) -> None:
        self.pages: list[list[ItemT]] = pages
        self.on_page: Callable[[int], None] | None = on_page
        self.captured_requests: list[RequestT] = []

    @property
    def protocol(self) -> VcoClientProtocol:
        return cast(VcoClientProtocol, cast(object, self))

    async def portal_paginated(
        self, _method: str, request: RequestT, _item_type: type[ItemT]
    ) -> AsyncGenerator[list[ItemT]]:
        self.captured_requests.append(request)
        for index, page in enumerate(self.pages):
            if self.on_page is not None:
                self.on_page(index)
            yield page


class MockListClient[RequestT: BaseModel, ItemT: BaseModel]:
    def __init__(self, items: Sequence[ItemT]) -> None:
        self.items: Sequence[ItemT] = items
        self.captured_requests: list[RequestT] = []

    @property
    def protocol(self) -> VcoClientProtocol:
        return cast(VcoClientProtocol, cast(object, self))

    async def portal_list(
        self,
        _method: str,
        request: RequestT,
        _item_type: type[ItemT],
        **_kwargs: object,
    ) -> Sequence[ItemT]:
        self.captured_requests.append(request)
        return self.items


def require_present[T](value: T | MISSING) -> T:
    """Return a field value, failing the test if Pydantic left it missing."""
    if value is MISSING:
        raise AssertionError("expected a present model field")
    return value
