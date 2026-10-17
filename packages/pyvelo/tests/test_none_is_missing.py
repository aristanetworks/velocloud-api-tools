"""Tests for NoneIsMissing validator."""

import json
from typing import Annotated

from pydantic_core import MISSING
from pyvelo.client.schemas.edge import GetEdgeConfigurationStack
from pyvelo.core import NoneIsMissing, VcoModel


class _SampleRequest(VcoModel):
    enterprise_id: Annotated[int | MISSING, NoneIsMissing] = MISSING
    name: str = "test"


class TestNoneIsMissing:
    def test_none_converts_to_missing(self):
        obj = _SampleRequest.model_validate({"enterprise_id": None})
        assert obj.enterprise_id is MISSING

    def test_int_preserved(self):
        obj = _SampleRequest(enterprise_id=42)
        assert obj.enterprise_id == 42

    def test_missing_preserved(self):
        obj = _SampleRequest()
        assert obj.enterprise_id is MISSING

    def test_serialization_excludes_none_converted(self):
        obj = _SampleRequest.model_validate({"enterprise_id": None})
        dumped = obj.model_dump(by_alias=True, exclude_unset=True)
        assert "enterpriseId" not in dumped

    def test_serialization_includes_int(self):
        obj = _SampleRequest(enterprise_id=42)
        dumped = obj.model_dump(by_alias=True, exclude_unset=True)
        assert dumped["enterpriseId"] == 42

    def test_json_excludes_none_converted(self):
        obj = _SampleRequest.model_validate({"enterprise_id": None})
        raw = obj.model_dump_json(by_alias=True, exclude_unset=True)
        parsed = json.loads(raw)
        assert "enterpriseId" not in parsed

    def test_applied_to_real_schemas(self):
        req = GetEdgeConfigurationStack.model_validate(
            {"edge_id": 1, "enterprise_id": None}
        )
        assert req.enterprise_id is MISSING
        dumped = req.model_dump(by_alias=True, exclude_unset=True)
        assert "enterpriseId" not in dumped

        req2 = GetEdgeConfigurationStack(edge_id=1, enterprise_id=5)
        assert req2.enterprise_id == 5
