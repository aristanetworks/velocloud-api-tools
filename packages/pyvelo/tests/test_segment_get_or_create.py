import pytest
from pyvelo.client.exceptions import UnboundError
from pyvelo.client.models.configuration import (
    DeviceSettingsData,
    FirewallData,
    QosData,
)
from pyvelo.core import ConfigContext


def desc(sid: int, name: str, **extra: object) -> dict[str, object]:
    return {
        "segmentId": sid,
        "name": name,
        "type": "REGULAR",
        "segmentLogicalId": f"00000000-0000-0000-0000-{sid:012d}",
        **extra,
    }


PROFILE = {
    "segments": [
        {
            "segment": desc(0, "Global Segment"),
            "outbound": [{"name": "p"}],
            "rules": [{"name": "p"}],
        },
        {"segment": desc(2, "Guest"), "outbound": [], "rules": []},
    ]
}


def edge_fw() -> FirewallData:
    return FirewallData.model_validate(
        {"segments": [{"segment": desc(0, "Global Segment"), "outbound": []}]}
    ).for_edge()


def test_existing_segment_is_returned_without_binding():
    fw = edge_fw()
    assert fw.segment("Global Segment") is fw.segments[0]


def test_unbound_missing_segment_raises():
    with pytest.raises(UnboundError, match="Guest"):
        edge_fw().segment("Guest")


def test_placeholder_is_created_once_and_inherits_everything():
    fw = edge_fw()
    fw.bind_profile(PROFILE)
    guest = fw.segment("Guest")
    assert fw.segment("Guest") is guest and fw.segments[-1] is guest
    assert (guest.segment.segment_id, guest.outbound) == (2, [])
    assert guest.config_context is ConfigContext.EDGE
    with pytest.raises(ValueError, match="Nope"):
        fw.segment("Nope")
    with pytest.raises(ValueError):
        fw.get_segment(name="Nope")  # get-or-raise is unchanged


def test_same_id_under_another_name_is_reused():
    fw = FirewallData.model_validate(
        {"segments": [{"segment": desc(2, "Old Guest Name"), "outbound": []}]}
    )
    fw.bind_profile(PROFILE)
    assert fw.segment("Guest") is fw.segments[0]
    assert len(fw.segments) == 1


def test_qos_placeholder_has_empty_rules():
    qos = QosData.model_validate({"segments": []})
    qos.bind_profile(PROFILE)
    assert qos.segment("Guest").rules == []


def test_bind_tolerates_odd_profile_data():
    fw = edge_fw()
    fw.bind_profile({"segments": None})
    with pytest.raises(ValueError):
        fw.segment("Guest")
    assert hasattr(DeviceSettingsData, "segment") and hasattr(
        DeviceSettingsData, "bind_profile"
    )
