import uuid

import pytest
from pydantic import ValidationError
from pyvelo.client.enums.edge import GetEdgeWith
from pyvelo.client.schemas.edge import GetEdge, GetEdgeResult

from ._type_support import require_present


class TestGetEdgeRequestApiShape:
    def test_id_is_not_required_when_using_supported_lookup_fields(self):
        req = GetEdge(logical_id=uuid.UUID("00000000-0000-0000-0000-000000000001"))

        dumped = req.model_dump(by_alias=True)

        assert "id" not in dumped
        assert dumped["logicalId"] == uuid.UUID("00000000-0000-0000-0000-000000000001")

    def test_name_lookup_serializes_without_id(self):
        req = GetEdge(name="branch-1")

        assert req.model_dump(by_alias=True) == {"name": "branch-1"}

    def test_accepts_upstream_get_edge_with_values(self):
        req = GetEdge.model_validate(
            {
                "id": 1,
                "with": [
                    "recentLinks",
                    "links",
                    "serviceGroups",
                    "site",
                    "enterprise",
                    "configuration",
                    "configurationWithModules",
                    "selfHealing",
                ],
            }
        )

        assert req.with_ == [
            GetEdgeWith.RECENT_LINKS,
            GetEdgeWith.LINKS,
            GetEdgeWith.SERVICE_GROUPS,
            GetEdgeWith.SITE,
            GetEdgeWith.ENTERPRISE,
            GetEdgeWith.CONFIGURATION,
            GetEdgeWith.CONFIGURATION_WITH_MODULES,
            GetEdgeWith.SELF_HEALING,
        ]

    def test_rejects_non_get_edge_with_value(self):
        with pytest.raises(ValidationError):
            _ = GetEdge.model_validate({"id": 1, "with": ["licenses"]})


class TestGetEdgeResultApiShape:
    def test_parses_optional_enterprise_and_self_healing_fields(self):
        result = GetEdgeResult.model_validate(
            {
                "id": 1,
                "name": "branch-1",
                "enterprise": {"id": 42, "name": "Acme"},
                "selfHealing": True,
            }
        )

        assert result.enterprise == {"id": 42, "name": "Acme"}
        assert result.self_healing is True

    def test_parses_none_ha_type(self):
        result = GetEdgeResult.model_validate(
            {
                "id": 1,
                "name": "branch-1",
                "ha": {"type": "NONE", "data": None},
            }
        )

        ha = require_present(result.ha)
        assert ha is not None
        assert ha.type_ == "NONE"
        assert require_present(ha.data) is None
