"""Tests for segment lookup on data models."""

from uuid import UUID

import pytest
from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.models.configuration.common import _find_segment
from pyvelo.client.models.configuration.device_settings.segment import (
    DeviceSegment,
    SegmentMetadata,
)
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.firewall.segment import (
    FirewallSegment,
)
from pyvelo.client.models.configuration.firewall.segment import (
    SegmentMetadata as FwSegmentMetadata,
)
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.qos.segment import (
    QosSegment,
)
from pyvelo.client.models.configuration.qos.segment import (
    SegmentMetadata as QosSegmentMetadata,
)


def _make_device_segments() -> list[DeviceSegment]:
    return [
        DeviceSegment(
            segment=SegmentMetadata(
                segment_id=0, name="Global Segment", type_=NetworkSegmentType.REGULAR
            )
        ),
        DeviceSegment(
            segment=SegmentMetadata(
                segment_id=1, name="Engineering", type_=NetworkSegmentType.REGULAR
            )
        ),
    ]


def _make_qos_segments() -> list[QosSegment]:
    return [
        QosSegment(
            segment=QosSegmentMetadata(
                segment_id=0,
                name="Global Segment",
                type_=NetworkSegmentType.REGULAR,
                segment_logical_id=UUID("00000000-0000-0000-0000-000000000000"),
            )
        ),
        QosSegment(
            segment=QosSegmentMetadata(
                segment_id=2,
                name="Sales",
                type_=NetworkSegmentType.REGULAR,
                segment_logical_id=UUID("00000000-0000-0000-0000-000000000001"),
            )
        ),
    ]


def _make_fw_segments() -> list[FirewallSegment]:
    return [
        FirewallSegment(
            segment=FwSegmentMetadata(
                segment_id=0, name="Global Segment", type_=NetworkSegmentType.REGULAR
            )
        ),
    ]


class TestFindSegmentHelper:
    def test_find_by_name(self):
        segs = _make_device_segments()
        result = _find_segment(segs, name="Engineering")
        assert result.segment.segment_id == 1

    def test_find_by_id(self):
        segs = _make_device_segments()
        result = _find_segment(segs, segment_id=0)
        assert result.segment.name == "Global Segment"

    def test_not_found_raises(self):
        segs = _make_device_segments()
        with pytest.raises(ValueError, match="Segment not found"):
            _find_segment(segs, name="Nonexistent")

    def test_no_criteria_raises(self):
        segs = _make_device_segments()
        with pytest.raises(ValueError, match="Provide name or segment_id"):
            _find_segment(segs)


class TestQosDataGetSegment:
    def test_lookup_by_name(self):
        data = QosData(segments=_make_qos_segments())
        seg = data.get_segment(name="Sales")
        assert seg.segment.segment_id == 2

    def test_lookup_by_id(self):
        data = QosData(segments=_make_qos_segments())
        seg = data.get_segment(segment_id=0)
        assert seg.segment.name == "Global Segment"

    def test_not_found(self):
        data = QosData(segments=_make_qos_segments())
        with pytest.raises(ValueError, match="Segment not found"):
            data.get_segment(name="Missing")


class TestFirewallDataGetSegment:
    def test_lookup_by_name(self):
        data = FirewallData(segments=_make_fw_segments())
        seg = data.get_segment(name="Global Segment")
        assert seg.segment.segment_id == 0

    def test_not_found(self):
        data = FirewallData(segments=_make_fw_segments())
        with pytest.raises(ValueError, match="Segment not found"):
            data.get_segment(name="Missing")
