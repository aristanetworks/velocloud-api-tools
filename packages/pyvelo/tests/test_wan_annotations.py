"""Tests for WAN data model annotations."""

from pyvelo.client.models.configuration.wan.root import WanData
from pyvelo.core.fields import get_key_spec, is_edge_local


class TestWanKeyFields:
    def test_links_keyed_by_name(self):
        spec = get_key_spec(WanData.model_fields["links"])
        assert spec is not None
        assert spec.paths == ("name",)

    def test_networks_keyed_by_name(self):
        spec = get_key_spec(WanData.model_fields["networks"])
        assert spec is not None
        assert spec.paths == ("name",)


class TestWanEdgeLocal:
    def test_links_edge_local(self):
        assert is_edge_local(WanData.model_fields["links"])

    def test_networks_edge_local(self):
        assert is_edge_local(WanData.model_fields["networks"])
