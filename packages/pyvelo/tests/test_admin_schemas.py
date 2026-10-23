"""Tests for enterprise and partner administration request contracts."""

import uuid

import pytest
from pydantic import MISSING, ValidationError
from pyvelo.client.enums.enterprise import GetEnterpriseWith
from pyvelo.client.enums.enterprise_proxy import (
    GetEnterpriseProxyEnterprisesWith,
    GetEnterpriseProxyWith,
)
from pyvelo.client.schemas.enterprise import EnterpriseResult, GetEnterprise
from pyvelo.client.schemas.enterprise_proxy import (
    EnterpriseProxyResult,
    GetEnterpriseProxy,
    GetEnterpriseProxyEnterprises,
)

from ._type_support import require_present

_LOGICAL_ID = uuid.UUID("da060e97-9815-4f17-b55f-edf51ff1077e")


class TestGetEnterprise:
    def test_accepts_all_vco_identifiers(self):
        by_id = GetEnterprise.model_validate({"id": 7})
        by_enterprise_id = GetEnterprise.model_validate({"enterpriseId": 8})
        by_logical_id = GetEnterprise.model_validate(
            {"enterpriseLogicalId": str(_LOGICAL_ID)}
        )

        assert by_id.id_ == 7
        assert by_enterprise_id.enterprise_id == 8
        assert by_logical_id.enterprise_logical_id == _LOGICAL_ID
        assert (
            by_logical_id.model_dump(by_alias=True)["enterpriseLogicalId"]
            == _LOGICAL_ID
        )

    def test_only_supports_enterprise_proxy_expansion(self):
        request = GetEnterprise.model_validate({"with": ["enterpriseProxy"]})
        assert request.with_ == [GetEnterpriseWith.ENTERPRISE_PROXY]

        with pytest.raises(ValidationError):
            _ = GetEnterprise.model_validate({"with": ["edges"]})

    def test_retains_expanded_enterprise_proxy(self):
        result = EnterpriseResult.model_validate(
            {
                "id": 7,
                "created": 0,
                "name": "Customer A",
                "enterpriseProxy": {
                    "id": 9,
                    "name": "Partner A",
                    "logicalId": str(_LOGICAL_ID),
                },
            }
        )

        enterprise_proxy = require_present(result.enterprise_proxy)
        assert enterprise_proxy is not None
        assert enterprise_proxy.id_ == 9
        assert enterprise_proxy.name == "Partner A"
        assert (
            result.model_dump(by_alias=True)["enterpriseProxy"]["logicalId"]
            == _LOGICAL_ID
        )

    def test_accepts_null_enterprise_proxy(self):
        result = EnterpriseResult.model_validate(
            {
                "id": 7,
                "created": 0,
                "name": "Direct Customer",
                "enterpriseProxy": None,
            }
        )

        assert result.enterprise_proxy is None


class TestGetEnterpriseProxy:
    def test_accepts_vco_identifiers_and_serializes_logical_id(self):
        by_id = GetEnterpriseProxy.model_validate({"id": 9})
        by_logical_id = GetEnterpriseProxy.model_validate(
            {"enterpriseProxyLogicalId": str(_LOGICAL_ID)}
        )

        assert by_id.id_ == 9
        assert by_logical_id.logical_id == _LOGICAL_ID
        assert (
            by_logical_id.model_dump(by_alias=True)["enterpriseProxyLogicalId"]
            == _LOGICAL_ID
        )

    def test_supports_only_gateway_pool_expansions(self):
        request = GetEnterpriseProxy.model_validate({"with": ["gatewayPoolIds"]})
        assert request.with_ == [GetEnterpriseProxyWith.GATEWAY_POOL_IDS]

        with pytest.raises(ValidationError):
            _ = GetEnterpriseProxy.model_validate({"with": ["enterprises"]})

    def test_retains_gateway_pool_expansions(self):
        result = EnterpriseProxyResult.model_validate(
            {
                "id": 9,
                "name": "Partner A",
                "gatewayPools": [{"id": 11, "name": "Pool A"}],
                "gatewayPoolIds": [11, 12],
            }
        )

        gateway_pools = require_present(result.gateway_pools)
        gateway_pool_ids = require_present(result.gateway_pool_ids)
        assert gateway_pools is not None
        assert gateway_pool_ids is not None
        assert gateway_pools[0].id_ == 11
        assert gateway_pools[0].name == "Pool A"
        assert gateway_pool_ids == [11, 12]
        assert result.model_dump(by_alias=True, exclude_unset=True) == {
            "id": 9,
            "name": "Partner A",
            "gatewayPools": [{"id": 11, "name": "Pool A"}],
            "gatewayPoolIds": [11, 12],
        }


class TestGetEnterpriseProxyEnterprises:
    def test_proxy_id_is_inferred_for_msp_sessions(self):
        request = GetEnterpriseProxyEnterprises()
        assert request.enterprise_proxy_id is MISSING

    def test_accepts_documented_expansions(self):
        request = GetEnterpriseProxyEnterprises.model_validate(
            {"with": ["edgeCount", "serviceLicenses"]}
        )
        assert request.with_ == [
            GetEnterpriseProxyEnterprisesWith.EDGE_COUNT,
            GetEnterpriseProxyEnterprisesWith.SERVICE_LICENSES,
        ]
