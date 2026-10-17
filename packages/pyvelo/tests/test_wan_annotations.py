"""Tests for WAN data model annotations."""

from pyvelo.client.models.configuration.wan.root import WanData
from pyvelo.core.fields import get_key_field, is_edge_local


class TestWanKeyFields:
    def test_links_keyed_by_name(self):
        assert get_key_field(WanData.model_fields["links"]) == "name"

    def test_networks_keyed_by_name(self):
        assert get_key_field(WanData.model_fields["networks"]) == "name"


class TestWanEdgeLocal:
    def test_links_edge_local(self):
        assert is_edge_local(WanData.model_fields["links"])

    def test_networks_edge_local(self):
        assert is_edge_local(WanData.model_fields["networks"])
