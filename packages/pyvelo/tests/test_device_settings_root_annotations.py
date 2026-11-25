"""Tests for device settings root data model annotations."""

from pyvelo.client.models.configuration.device_settings.root import (
    DeviceSettingsData,
    Ntp,
    NtpServer,
)
from pyvelo.core import KeySpec
from pyvelo.core.fields import get_key_spec, has_override_flag


class TestDeviceSettingsKeyFields:
    def test_segments_keyed_by_segment_name(self):
        assert get_key_spec(DeviceSettingsData.model_fields["segments"]) == KeySpec(
            ("segment.name",), alt="segment.segment_id"
        )

    def test_routed_interfaces_keyed_by_name(self):
        spec = get_key_spec(DeviceSettingsData.model_fields["routed_interfaces"])
        assert spec is not None
        assert spec.paths == ("name",)

    def test_ntp_servers_keyed_by_server(self):
        spec = get_key_spec(Ntp.model_fields["servers"])
        assert spec is not None
        assert spec.paths == ("server",)


class TestDeviceSettingsOverrideFlags:
    def test_ntp_override(self):
        assert has_override_flag(Ntp.model_fields["override"])

    def test_ntp_server_override(self):
        assert has_override_flag(NtpServer.model_fields["override"])
