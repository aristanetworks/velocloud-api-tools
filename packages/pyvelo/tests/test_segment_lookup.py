"""Tests for segment lookup on data models."""

from uuid import UUID

import pytest
from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.models.configuration.device_settings.lan import LanSettings
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
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


def _make_device_data() -> DeviceSettingsData:
    return DeviceSettingsData(
        lan=LanSettings(interfaces=[], networks=[]), segments=_make_device_segments()
    )


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


class TestDeviceSettingsDataGetSegment:
    def test_lookup_by_name_and_id(self):
        data = _make_device_data()

        by_name = data.get_segment(name="Engineering")
        by_id = data.get_segment(segment_id=0)

        assert by_name.segment.segment_id == 1
        assert by_id.segment.name == "Global Segment"

    def test_requires_name_or_segment_id(self):
        data = _make_device_data()
        with pytest.raises(ValueError, match="Provide name or segment_id"):
            _ = data.get_segment()

    def test_not_found(self):
        data = _make_device_data()
        with pytest.raises(ValueError, match="Segment not found"):
            _ = data.get_segment(name="Missing")


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
            _ = data.get_segment(name="Missing")

    def test_requires_name_or_segment_id(self):
        data = QosData(segments=_make_qos_segments())
        with pytest.raises(ValueError, match="Provide name or segment_id"):
            _ = data.get_segment()


class TestFirewallDataGetSegment:
    def test_lookup_by_name(self):
        data = FirewallData(segments=_make_fw_segments())
        seg = data.get_segment(name="Global Segment")
        assert seg.segment.segment_id == 0

    def test_not_found(self):
        data = FirewallData(segments=_make_fw_segments())
        with pytest.raises(ValueError, match="Segment not found"):
            _ = data.get_segment(name="Missing")
