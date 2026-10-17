import asyncio
import json
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Protocol, Self

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from pyvelo.client.auth import VcoAuth
from pyvelo.client.exceptions import (
    VcoApiError,
    VcoClientNotInitializedError,
    VcoEmptyResultError,
    VcoHttpError,
    VcoOperationError,
)
from pyvelo.client.operations.alert import AlertMixin
from pyvelo.client.operations.branding import BrandingMixin
from pyvelo.client.operations.composite_role import CompositeRoleMixin
from pyvelo.client.operations.configuration import ConfigurationMixin
from pyvelo.client.operations.diagnostic_bundles import DiagnosticBundlesMixin
from pyvelo.client.operations.disaster_recovery import DisasterRecoveryMixin
from pyvelo.client.operations.edge import EdgeMixin
from pyvelo.client.operations.enterprise import EnterpriseMixin
from pyvelo.client.operations.enterprise_proxy import EnterpriseProxyMixin
from pyvelo.client.operations.event import EventMixin
from pyvelo.client.operations.firewall import FirewallMixin
from pyvelo.client.operations.functional_role import FunctionalRoleMixin
from pyvelo.client.operations.gateway import GatewayMixin
from pyvelo.client.operations.generic import GenericMixin
from pyvelo.client.operations.license import LicenseMixin
from pyvelo.client.operations.metrics import MetricsMixin
from pyvelo.client.operations.migration import MigrationMixin
from pyvelo.client.operations.monitoring import MonitoringMixin
from pyvelo.client.operations.network import NetworkMixin
from pyvelo.client.operations.pki import PkiMixin
from pyvelo.client.operations.role import RoleMixin
from pyvelo.client.operations.service import ServiceMixin
from pyvelo.client.operations.software_update import SoftwareUpdateMixin
from pyvelo.client.operations.system import SystemMixin
from pyvelo.client.operations.websocket import RemoteDiagnosticsMixin
from pyvelo.core import (
    JsonRpcRequest,
    JsonRpcResponse,
    PaginatedResult,
    PaginatedVcoModel,
    VcoModel,
)


@dataclass(frozen=True, slots=True)
class Exchange:
    """Captures a single HTTP request/response exchange for inspection."""

    index: int
    method: str
    request_body: dict[str, Any] | list[Any] | str | None
    response: httpx.Response
    result_type: type | None = None
    validation_error: ValidationError | None = None

    def parse_response(self) -> Any:
        """Parse the response body as JSON."""
        return self.response.json()


ExchangeHook = Callable[[Exchange], None]
"""Callback invoked after every HTTP exchange."""


def make_file_dump_hook(dump_dir: Path) -> ExchangeHook:
    """Return an :data:`ExchangeHook` that writes JSON files to *dump_dir*.

    Files are organized as ``<index>_<method>_req.json`` and
    ``<index>_<method>_resp.json``.
    """

    def _write(exchange: Exchange) -> None:
        safe_method = exchange.method.replace("/", "_")
        req_path = dump_dir / f"{exchange.index:04d}_{safe_method}_req.json"
        resp_path = dump_dir / f"{exchange.index:04d}_{safe_method}_resp.json"

        if isinstance(exchange.request_body, (dict, list)):
            req_path.write_text(json.dumps(exchange.request_body, indent=2))
        elif exchange.request_body:
            req_path.write_text(str(exchange.request_body))

        try:
            resp_body = exchange.response.json()
            resp_path.write_text(json.dumps(resp_body, indent=2))
        except Exception:
            resp_path.write_bytes(exchange.response.content)

    return _write


def _is_paginated_envelope_mismatch(err: ValidationError) -> bool:
    """Return True when *err* indicates a paginated envelope where a list was expected.

    When VCO wraps a list result in ``{"metaData": {...}, "data": [...]}`` and we
    parse it as ``JsonRpcResponse[list[T]]``, Pydantic reports a ``list_type`` error
    at ``loc=("result",)`` with the envelope dict as the failing input.  That is a
    format mismatch, not a data validation error, so it is safe to retry with the
    paginated schema.  Any other error type or location indicates that the data
    itself is malformed and should be propagated to the caller.
    """
    return any(
        e["type"] == "list_type"
        and e.get("loc") == ("result",)
        and isinstance(e.get("input"), dict)
        for e in err.errors()
    )


class VcoClientProtocol(
    AlertMixin,
    BrandingMixin,
    CompositeRoleMixin,
    ConfigurationMixin,
    DiagnosticBundlesMixin,
    DisasterRecoveryMixin,
    EdgeMixin,
    EnterpriseMixin,
    EnterpriseProxyMixin,
    EventMixin,
    FirewallMixin,
    FunctionalRoleMixin,
    GatewayMixin,
    GenericMixin,
    LicenseMixin,
    MetricsMixin,
    MigrationMixin,
    MonitoringMixin,
    NetworkMixin,
    PkiMixin,
    RoleMixin,
    ServiceMixin,
    SoftwareUpdateMixin,
    SystemMixin,
    RemoteDiagnosticsMixin,
    Protocol,
):
    _http: httpx.AsyncClient | None = None
    _vco_fqdn: str
    _token: SecretStr
    _verify_ssl: bool
    _timeout: float = 60.0
    _exchange_hook: ExchangeHook | None = None
    _request_index: int = 0
    _ws_cache: Any = None
    _ws_cleanup_tasks: set[asyncio.Task[None]]

    async def __aenter__(self) -> Self:
        await self._connect()
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None:
        await self.close_websocket()
        if self._http is not None:
            await self._http.__aexit__(_exc_type, _exc_val, _exc_tb)
            self._http = None

    async def _connect(self) -> None:
        headers = {"Content-Type": "application/json"}
        self._http = await httpx.AsyncClient(
            auth=VcoAuth(self._token.get_secret_value()),
            base_url=f"https://{self._vco_fqdn}",
            headers=headers,
            verify=self._verify_ssl,
            timeout=self._timeout,
        ).__aenter__()

    def _http_client(self) -> httpx.AsyncClient:
        if self._http is not None:
            return self._http
        raise VcoClientNotInitializedError()

    # -- Transport hook (overridden by VcoClient for retry/rate-limiting) ----

    def on_exchange(self, hook: ExchangeHook | None) -> None:
        """Register (or clear) a callback invoked after every HTTP exchange."""
        self._exchange_hook = hook
        self._request_index = 0

    def enable_dump(self, dump_dir: str | Path = "vco_dump") -> None:
        """Convenience: register a file-writing exchange hook.

        Files are organized as ``<index>_<method>_req.json`` and
        ``<index>_<method>_resp.json``.
        """
        path = Path(dump_dir)
        path.mkdir(parents=True, exist_ok=True)
        self.on_exchange(make_file_dump_hook(path))

    def _notify_exchange(
        self,
        content: str,
        response: httpx.Response,
        *,
        result_type: type | None = None,
        validation_error: ValidationError | None = None,
    ) -> None:
        if self._exchange_hook is None:
            return

        method = "unknown"
        req_body: Any = None
        try:
            req_body = json.loads(content)
            method = req_body.get("method", method)
        except json.JSONDecodeError, AttributeError:
            req_body = content

        idx = self._request_index
        self._request_index += 1
        self._exchange_hook(
            Exchange(idx, method, req_body, response, result_type, validation_error)
        )

    @staticmethod
    def _check_http_status(response: httpx.Response) -> None:
        """Raise :class:`VcoHttpError` for non-2xx responses.

        Called by portal methods before JSON parsing so that HTML error
        pages (e.g. from a load balancer) don't produce cryptic
        Pydantic ``ValidationError``s.
        """
        if not response.is_success:
            raise VcoHttpError(response.status_code, response.text)

    def _check_rpc_result[T](self, method: str, response: JsonRpcResponse[T]) -> T:
        """Raise on JSON-RPC error or empty result, otherwise return the result."""
        if response.error is not None:
            err = response.error
            raise VcoOperationError(
                method, err.message, code=err.code, data=err.data
            ) from VcoApiError(err.code, err.message, err.data)
        if response.result is None:
            raise VcoEmptyResultError(method)
        return response.result

    async def _send_request(
        self,
        http_method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        content: str | None = None,
    ) -> httpx.Response:
        """Dispatch an HTTP request via the underlying client.

        Subclasses override this to add retry, rate-limiting, and
        concurrency control around the actual HTTP call.
        """
        client = self._http_client()
        method = http_method.upper()
        if method == "GET":
            response = await client.get(url, params=params)
        elif method == "POST":
            response = await client.post(
                url, params=params, json=json_data, content=content
            )
        elif method == "PUT":
            response = await client.put(
                url, params=params, json=json_data, content=content
            )
        elif method == "DELETE":
            response = await client.delete(url, params=params)
        elif method == "PATCH":
            response = await client.patch(
                url, params=params, json=json_data, content=content
            )
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")
        return response

    # -- Portal methods -----------------------------------------------------

    async def portal_paginated[T: BaseModel](
        self,
        method: str,
        request: PaginatedVcoModel,
        ItemType: type[T],
    ) -> AsyncGenerator[list[T]]:
        """Yield one page per source request until VCO reports no more pages.

        After each page, *request* records ``_page_has_more`` and
        ``_page_next_link`` so a caller that stops early can resume from that
        cursor. The caller's ``next_page_link`` is left unchanged.
        """
        more = True
        request._page_has_more = False
        request._page_next_link = None
        page_request = request.model_copy()

        ResultType = PaginatedResult[ItemType]
        ResponseType = JsonRpcResponse[ResultType]

        while more:
            rpc_request = JsonRpcRequest(method=method, params=page_request)
            content = rpc_request.model_dump_json(by_alias=True)
            response = await self._send_request(
                "POST",
                "/portal/",
                content=content,
            )

            self._check_http_status(response)
            try:
                validated_response = ResponseType.model_validate_json(response.content)
            except ValidationError as exc:
                self._notify_exchange(
                    content, response, result_type=ItemType, validation_error=exc
                )
                raise
            self._notify_exchange(content, response, result_type=ItemType)
            result = self._check_rpc_result(method, validated_response)

            meta = result.metadata
            more = meta.more
            if more:
                next_page_link = meta.next_page_link
                if not next_page_link:
                    raise VcoOperationError(
                        method, "Incomplete response omitted its continuation cursor"
                    )
                page_request.next_page_link = next_page_link

            request._page_has_more = more
            request._page_next_link = meta.next_page_link if more else None

            yield result.data

    async def portal[T: BaseModel](
        self,
        method: str,
        request: VcoModel,
        ResultType: type[T],
        *,
        validation_context: dict[str, Any] | None = None,
    ) -> T:
        rpc_request = JsonRpcRequest(method=method, params=request)
        content = rpc_request.model_dump_json(by_alias=True)
        response = await self._send_request(
            "POST",
            "/portal/",
            content=content,
        )

        self._check_http_status(response)
        ResponseType = JsonRpcResponse[ResultType]

        try:
            validated_response = ResponseType.model_validate_json(
                response.content, context=validation_context
            )
        except ValidationError as exc:
            self._notify_exchange(
                content, response, result_type=ResultType, validation_error=exc
            )
            raise
        self._notify_exchange(content, response, result_type=ResultType)
        return self._check_rpc_result(method, validated_response)

    async def portal_list[T: BaseModel](
        self,
        method: str,
        request: VcoModel,
        ItemType: type[T],
        *,
        hint: Literal["list"] | None = None,
    ) -> Sequence[T]:
        """Return the list result of one non-paginated portal request.

        Some list endpoints wrap their result in a paginated envelope. A complete
        envelope is unwrapped; an incomplete one raises, because following
        cursors belongs to :meth:`portal_paginated_list`.
        """
        rpc_request = JsonRpcRequest(method=method, params=request)
        content = rpc_request.model_dump_json(by_alias=True)
        response = await self._send_request(
            "POST",
            "/portal/",
            content=content,
        )

        self._check_http_status(response)

        # Try bare list first, fall back to paginated envelope on structural mismatch.
        try:
            list_response = JsonRpcResponse[list[ItemType]].model_validate_json(
                response.content
            )
        except ValidationError as list_err:
            if hint == "list" or not _is_paginated_envelope_mismatch(list_err):
                self._notify_exchange(
                    content,
                    response,
                    result_type=ItemType,
                    validation_error=list_err,
                )
                raise
        else:
            self._notify_exchange(content, response, result_type=ItemType)
            return self._check_rpc_result(method, list_response)

        try:
            pag_response = JsonRpcResponse[
                PaginatedResult[ItemType]
            ].model_validate_json(response.content)
        except ValidationError as exc:
            self._notify_exchange(
                content, response, result_type=ItemType, validation_error=exc
            )
            raise
        self._notify_exchange(content, response, result_type=ItemType)
        page = self._check_rpc_result(method, pag_response)

        if page.metadata.more:
            if isinstance(request, PaginatedVcoModel):
                raise VcoOperationError(
                    method,
                    "Response is incomplete: paginated endpoints must use "
                    "portal_paginated_list",
                )
            raise VcoOperationError(
                method,
                "Response is incomplete: pagination is unsupported for this request",
            )
        return page.data

    async def portal_paginated_list[T: BaseModel](
        self,
        method: str,
        request: PaginatedVcoModel,
        ItemType: type[T],
    ) -> Sequence[T]:
        """Collect pages until VCO reports no more or ``request._max_pages`` is hit.

        ``_max_pages`` of 0 means no cap. When the cap stops collection,
        ``request._page_has_more`` stays True and ``request._page_next_link``
        holds the cursor for the next page.
        """
        items: list[T] = []
        page_count = 0
        async with aclosing(self.portal_paginated(method, request, ItemType)) as pages:
            async for page in pages:
                items.extend(page)
                page_count += 1
                if request._max_pages and page_count >= request._max_pages:
                    break
        return items
