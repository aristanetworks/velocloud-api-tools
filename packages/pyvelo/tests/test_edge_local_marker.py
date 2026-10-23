"""Tests for the EdgeLocal Annotated marker."""

from typing import Annotated

from pyvelo.core import VcoModel
from pyvelo.core.fields import EdgeLocal, is_edge_local


class Network(VcoModel):
    cidr_ip: Annotated[str, EdgeLocal] = ""
    cidr_prefix: Annotated[int, EdgeLocal] = 24
    advertise: bool = True


class TestEdgeLocalMarker:
    def test_processed_marker_in_metadata(self):
        fi = Network.model_fields["cidr_ip"]
        assert is_edge_local(fi)

    def test_non_edge_local_has_no_marker(self):
        fi = Network.model_fields["advertise"]
        assert not is_edge_local(fi)

    def test_model_validates_normally(self):
        m = Network(cidr_ip="10.0.0.1", cidr_prefix=24, advertise=False)
        assert m.cidr_ip == "10.0.0.1"
