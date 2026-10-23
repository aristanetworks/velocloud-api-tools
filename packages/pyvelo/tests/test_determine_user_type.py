"""Tests for VcoClient.determine_user_type()."""

from collections.abc import Callable
from typing import cast

import httpx
import pytest
from pyvelo.client.enums import UserType
from pyvelo.client.exceptions import VcoUserTypeUnknownError

from ._type_support import (
    JsonObject,
    JsonValue,
    MockVcoClientFactory,
    parse_json_object,
)


def _make_handler(
    responses: dict[str, httpx.Response], call_order: list[str] | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    """Return a handler that dispatches by JSON-RPC method name."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = parse_json_object(request.content)
        method = body.get("method")
        if not isinstance(method, str):
            raise TypeError("JSON-RPC request did not include a method")
        if call_order is not None:
            call_order.append(method)
        return responses[method]

    return handler


def _success(result: JsonValue) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})


def _error(code: int = -32600, message: str = "Not authorized") -> httpx.Response:
    return httpx.Response(
        200,
        json={"jsonrpc": "2.0", "id": 1, "error": {"code": code, "message": message}},
    )


OPERATOR_RESULT: JsonValue = cast(
    JsonValue,
    [
        {
            "id": 1,
            "name": "ent",
            "logicalId": "00000000-0000-0000-0000-000000000001",
        }
    ],
)
MSP_RESULT: JsonValue = cast(
    JsonValue,
    {
        "id": 1,
        "name": "partner",
        "logicalId": "00000000-0000-0000-0000-000000000002",
        "created": "2026-01-01T00:00:00.000Z",
        "networkId": 1,
        "proxyType": "MSP",
        "operateGateways": 1,
        "modified": "2026-01-01T00:00:00.000Z",
    },
)
ENTERPRISE_RESULT: JsonValue = cast(
    JsonValue,
    {
        "id": 1,
        "name": "enterprise",
        "logicalId": "00000000-0000-0000-0000-000000000003",
        "created": "2026-01-01T00:00:00.000Z",
        "networkId": 1,
        "gatewayPoolId": 1,
        "alertsEnabled": 1,
        "operatorAlertsEnabled": 1,
        "endpointPkiMode": "CERTIFICATE_OPTIONAL",
        "modified": "2026-01-01T00:00:00.000Z",
    },
)


class TestDetermineUserType:
    async def test_operator_detected_first(self, mock_vco_client: MockVcoClientFactory):
        """When all three probes succeed, OPERATOR wins and later probes are skipped."""
        calls: list[str] = []
        handler = _make_handler(
            {
                "network/getNetworkEnterprises": _success(OPERATOR_RESULT),
                "enterpriseProxy/getEnterpriseProxy": _success(MSP_RESULT),
                "enterprise/getEnterprise": _success(ENTERPRISE_RESULT),
            },
            calls,
        )
        client = await mock_vco_client(handler)
        result = await client.determine_user_type()
        assert result == UserType.OPERATOR
        assert calls == ["network/getNetworkEnterprises"]

    async def test_msp_when_operator_fails(self, mock_vco_client: MockVcoClientFactory):
        """When operator probe fails, MSP is returned and enterprise probe is skipped."""
        calls: list[str] = []
        handler = _make_handler(
            {
                "network/getNetworkEnterprises": _error(),
                "enterpriseProxy/getEnterpriseProxy": _success(MSP_RESULT),
                "enterprise/getEnterprise": _success(ENTERPRISE_RESULT),
            },
            calls,
        )
        client = await mock_vco_client(handler)
        result = await client.determine_user_type()
        assert result == UserType.MSP
        assert calls == [
            "network/getNetworkEnterprises",
            "enterpriseProxy/getEnterpriseProxy",
        ]

    async def test_enterprise_when_operator_and_msp_fail(
        self, mock_vco_client: MockVcoClientFactory
    ):
        """When operator and MSP probes fail, ENTERPRISE is returned."""
        handler = _make_handler(
            {
                "network/getNetworkEnterprises": _error(),
                "enterpriseProxy/getEnterpriseProxy": _error(),
                "enterprise/getEnterprise": _success(ENTERPRISE_RESULT),
            }
        )
        client = await mock_vco_client(handler)
        result = await client.determine_user_type()
        assert result == UserType.ENTERPRISE

    async def test_all_fail_raises(self, mock_vco_client: MockVcoClientFactory):
        """When all probes fail, VcoUserTypeUnknownError is raised."""
        handler = _make_handler(
            {
                "network/getNetworkEnterprises": _error(),
                "enterpriseProxy/getEnterpriseProxy": _error(),
                "enterprise/getEnterprise": _error(),
            }
        )
        client = await mock_vco_client(handler)
        with pytest.raises(VcoUserTypeUnknownError):
            _ = await client.determine_user_type()

    async def test_operator_probe_omits_network_id(
        self, mock_vco_client: MockVcoClientFactory
    ):
        """The operator probe should NOT send networkId (let VCO infer from token)."""
        captured_params: dict[str, JsonObject] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            body = parse_json_object(request.content)
            method = body.get("method")
            params = body.get("params")
            if not isinstance(method, str) or not isinstance(params, dict):
                raise TypeError("JSON-RPC request was missing method or params")
            captured_params[method] = params
            if method == "network/getNetworkEnterprises":
                return _success(OPERATOR_RESULT)
            return _error()

        client = await mock_vco_client(handler)
        _ = await client.determine_user_type()
        assert "networkId" not in captured_params["network/getNetworkEnterprises"]
