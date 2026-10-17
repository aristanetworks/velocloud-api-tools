"""Tests for newly created enums from schema enum migration."""

from pyvelo.client.enums.configuration import (
    ConfigurationScope,
    ConfigurationType,
)
from pyvelo.client.enums.edge import HaRole, SdwanPeerType, SortDirection
from pyvelo.client.enums.enterprise import (
    BgpFilterActionType,
    BgpFilterMatchType,
    BgpFilterPermitDeny,
    CustomAppStatus,
    NetworkSegmentType,
    SecurityObjectType,
)
from pyvelo.client.enums.metrics import (
    DestAttribute,
    DestMetric,
    PathSeriesViewBy,
    PathStatus,
    SdwanPeerPathMetric,
)
from pyvelo.client.enums.network import GetNetworkEnterprisesWith


class TestEdgeNewEnums:
    def test_ha_role_values(self):
        assert HaRole("ACTIVE") == "ACTIVE"
        assert HaRole("STANDBY") == "STANDBY"

    def test_sdwan_peer_type_values(self):
        assert SdwanPeerType("BRANCH") == "BRANCH"
        assert SdwanPeerType("HUB") == "HUB"
        assert SdwanPeerType("GATEWAY") == "GATEWAY"

    def test_sort_direction_values(self):
        assert SortDirection("ASC") == "ASC"
        assert SortDirection("DESC") == "DESC"


class TestMetricsNewEnums:
    def test_sdwan_peer_path_metric_has_known_values(self):
        assert SdwanPeerPathMetric("bytesRx") == "bytesRx"
        assert SdwanPeerPathMetric("jitterTx") == "jitterTx"
        assert len(SdwanPeerPathMetric) == 27

    def test_path_series_view_by(self):
        assert PathSeriesViewBy("path") == "path"
        assert PathSeriesViewBy("peer") == "peer"

    def test_path_status(self):
        assert PathStatus("STABLE") == "STABLE"
        assert PathStatus("DEAD") == "DEAD"

    def test_dest_metric(self):
        assert DestMetric("bytesRx") == "bytesRx"
        assert len(DestMetric) == 7

    def test_dest_attribute(self):
        assert DestAttribute("destDomain") == "destDomain"
        assert DestAttribute("destIp") == "destIp"
        assert DestAttribute("destFQDN") == "destFQDN"


class TestConfigurationNewEnums:
    def test_configuration_type(self):
        assert ConfigurationType("SEGMENT_BASED") == "SEGMENT_BASED"
        assert ConfigurationType("NETWORK_BASED") == "NETWORK_BASED"

    def test_configuration_scope(self):
        assert ConfigurationScope("ENTERPRISE") == "ENTERPRISE"


class TestEnterpriseNewEnums:
    def test_security_object_type(self):
        assert SecurityObjectType("securityServiceGroup") == "securityServiceGroup"
        assert SecurityObjectType("idps") == "idps"

    def test_bgp_filter_match_type(self):
        assert BgpFilterMatchType("COMMUNITY") == "COMMUNITY"
        assert BgpFilterMatchType("AS_PATH") == "AS_PATH"
        assert BgpFilterMatchType("PREFIX") == "PREFIX"

    def test_bgp_filter_action_type(self):
        assert BgpFilterActionType("NONE") == "NONE"
        assert BgpFilterActionType("LOCAL_PREF") == "LOCAL_PREF"

    def test_bgp_filter_permit_deny(self):
        assert BgpFilterPermitDeny("PERMIT") == "PERMIT"
        assert BgpFilterPermitDeny("DENY") == "DENY"

    def test_network_segment_type(self):
        assert NetworkSegmentType("REGULAR") == "REGULAR"
        assert NetworkSegmentType("CDE") == "CDE"
        assert NetworkSegmentType("PRIVATE") == "PRIVATE"

    def test_custom_app_status(self):
        assert CustomAppStatus("ACTIVE") == "ACTIVE"
        assert CustomAppStatus("INACTIVE") == "INACTIVE"


class TestNetworkEnums:
    def test_get_network_enterprises_with(self):
        assert GetNetworkEnterprisesWith("edges") == "edges"
        assert GetNetworkEnterprisesWith("edgeCount") == "edgeCount"
