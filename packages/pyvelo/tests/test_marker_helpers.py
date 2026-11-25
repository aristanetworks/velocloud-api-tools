"""Tests for marker introspection helpers."""

from typing import Annotated

import pyvelo.core
from pydantic import Field
from pyvelo.core import VcoModel
from pyvelo.core.fields import (
    EdgeLocal,
    KeyField,
    OverrideFlag,
    Resolvable,
    ServerOwned,
    get_key_spec,
    get_resolvable,
    has_override_flag,
    is_edge_local,
    is_server_owned,
)
from pyvelo.core.identity import SegmentIdentity


class Item(VcoModel):
    name: str


class MarkedModel(VcoModel):
    override: Annotated[bool, OverrideFlag] = False
    items: Annotated[list[Item], KeyField("name")] = Field(default_factory=list)
    cidr_ip: Annotated[str, EdgeLocal] = ""
    segment_id: Annotated[int, Resolvable(SegmentIdentity, lambda i: i.segment_id)] = 0
    stamp: Annotated[str, ServerOwned()] = ""
    plain: str = ""


class TestGetKeySpec:
    def test_returns_key_path_for_keyfield(self):
        fi = MarkedModel.model_fields["items"]
        spec = get_key_spec(fi)
        assert spec is not None
        assert spec.paths == ("name",)

    def test_returns_none_for_non_keyfield(self):
        fi = MarkedModel.model_fields["plain"]
        assert get_key_spec(fi) is None


class TestHasOverrideFlag:
    def test_true_for_override_field(self):
        fi = MarkedModel.model_fields["override"]
        assert has_override_flag(fi) is True

    def test_false_for_normal_field(self):
        fi = MarkedModel.model_fields["plain"]
        assert has_override_flag(fi) is False


class TestIsEdgeLocal:
    def test_true_for_edge_local_field(self):
        fi = MarkedModel.model_fields["cidr_ip"]
        assert is_edge_local(fi) is True

    def test_false_for_normal_field(self):
        fi = MarkedModel.model_fields["plain"]
        assert is_edge_local(fi) is False


class TestGetResolvable:
    def test_returns_info_for_resolvable_field(self):
        fi = MarkedModel.model_fields["segment_id"]
        r = get_resolvable(fi)
        assert r is not None
        assert r.identity_type is SegmentIdentity
        assert r.attribute == "segment_id"

    def test_returns_none_for_non_resolvable(self):
        fi = MarkedModel.model_fields["plain"]
        assert get_resolvable(fi) is None


def test_is_server_owned():
    assert is_server_owned(MarkedModel.model_fields["stamp"]) is True
    assert is_server_owned(MarkedModel.model_fields["plain"]) is False


def test_server_owned_is_exported():
    assert "ServerOwned" in pyvelo.core.__all__


def test_a_new_firewall_rule_has_no_id_and_a_new_qos_rule_has_one():
    import uuid

    from pydantic import MISSING
    from pyvelo.client.models.configuration.firewall.rules import FirewallOutboundRule
    from pyvelo.client.models.configuration.qos.rule import QosRule

    firewall_id = FirewallOutboundRule.model_fields["rule_logical_id"]
    assert is_server_owned(firewall_id) and firewall_id.default is MISSING
    qos_id = QosRule.model_fields["rule_logical_id"]
    assert not is_server_owned(qos_id) and qos_id.default_factory is uuid.uuid4
