import pytest
from pyvelo.client.exceptions import UnboundError
from pyvelo.client.models.configuration import (
    DeviceSettingsData,
    FirewallData,
    QosData,
)
from pyvelo.client.models.configuration.common import (
    profile_placeholder,
    profile_segment_id,
    profile_segment_name,
)
from pyvelo.core import ConfigContext
from pyvelo.core.layers import Edge

from ._type_support import require_present


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


def edge_fw() -> FirewallData[Edge]:
    return (
        FirewallData[Edge]
        .model_validate(
            {"segments": [{"segment": desc(0, "Global Segment"), "outbound": []}]}
        )
        .for_edge()
    )


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
    fw = FirewallData[Edge].model_validate(
        {"segments": [{"segment": desc(2, "Old Guest Name"), "outbound": []}]}
    )
    fw.bind_profile(PROFILE)
    assert fw.segment("Guest") is fw.segments[0]
    assert len(fw.segments) == 1


def test_profile_segment_is_found_ignoring_case():
    fw = edge_fw()
    fw.bind_profile(PROFILE)
    guest = fw.segment("guest")
    assert (guest.segment.name, guest.segment.segment_id) == ("Guest", 2)
    assert fw.segment("GUEST") is guest and len(fw.segments) == 2


def test_an_exact_profile_name_wins_over_one_that_differs_in_case():
    fw = edge_fw()
    fw.bind_profile(
        {"segments": [PROFILE["segments"][1], {"segment": desc(3, "guest")}]}
    )
    assert fw.segment("guest").segment.segment_id == 3


def test_a_name_the_profile_lacks_in_any_case_still_raises():
    fw = edge_fw()
    fw.bind_profile(PROFILE)
    with pytest.raises(ValueError, match="Profile segments: Global Segment, Guest"):
        fw.segment("Gest")


def test_profile_segment_name():
    segments = PROFILE["segments"]
    assert profile_segment_name(segments, "Guest") == "Guest"
    assert profile_segment_name(segments, "GUEST") == "Guest"
    assert profile_segment_name(segments, "2") == "Guest"
    assert profile_segment_name(segments, 2) == "Guest"
    assert profile_segment_name(segments, "Nope") is None
    assert [profile_segment_id(segments, s) for s in ("guest", "2", "Nope")] == [
        2,
        2,
        None,
    ]
    with pytest.raises(UnboundError):
        _ = profile_segment_name(None, "Guest")
    fw, qos = edge_fw(), QosData.model_validate({"segments": []})
    ds = DeviceSettingsData.model_validate(
        {"lan": {"networks": [], "interfaces": []}, "segments": []}
    )
    for data in (fw, qos, ds):
        data.bind_profile(PROFILE)
    assert [
        fw.profile_segment_name("guest"),
        qos.profile_segment_name(0),
        ds.profile_segment_name("Nope"),
        qos.profile_segment_id("Guest"),
    ] == ["Guest", "Global Segment", None, 2]
    with pytest.raises(UnboundError):
        _ = edge_fw().profile_segment_name("Guest")
    with pytest.raises(UnboundError):
        _ = edge_fw().profile_segment_id("Guest")


def test_qos_placeholder_has_empty_rules():
    qos = QosData.model_validate({"segments": []})
    qos.bind_profile(PROFILE)
    assert qos.segment("Guest").rules == []


def test_device_settings_placeholder_has_empty_static_routes():
    ds = DeviceSettingsData.model_validate(
        {"lan": {"networks": [], "interfaces": []}, "segments": []}
    ).for_edge()
    ds.bind_profile(
        {
            "segments": [
                {
                    "segment": desc(2, "Guest"),
                    "routes": {"static": [{"destination": "192.0.2.0"}]},
                }
            ]
        }
    )
    guest = ds.segment("Guest")
    assert ds.segment("Guest") is guest and ds.segments[-1] is guest
    assert (guest.segment.segment_id, require_present(guest.routes).static) == (2, [])
    assert guest.config_context is ConfigContext.EDGE


def test_a_placeholder_is_empty_in_each_module_and_has_what_the_orchestrator_needs():
    profile_segments = [
        {
            "segment": desc(2, "Guest"),
            "outbound": [{"name": "p"}],
            "rules": [{"name": "p"}],
            "routes": {"static": [{"destination": "192.0.2.0"}], "staticV6": []},
        }
    ]
    assert profile_placeholder(profile_segments, "Guest", "firewall") == {
        "segment": desc(2, "Guest"),
        "outbound": [],
    }
    assert profile_placeholder(profile_segments, "Guest", "QOS") == {
        "segment": desc(2, "Guest"),
        "rules": [],
    }
    # a deviceSettings entry without routes.static is refused as "static route is missing"
    assert profile_placeholder(profile_segments, "Guest", "deviceSettings") == {
        "segment": desc(2, "Guest"),
        "routes": {"static": []},
    }


def test_bind_tolerates_odd_profile_data():
    fw = edge_fw()
    fw.bind_profile({"segments": None})
    with pytest.raises(ValueError):
        fw.segment("Guest")
    assert hasattr(DeviceSettingsData, "segment") and hasattr(
        DeviceSettingsData, "bind_profile"
    )
