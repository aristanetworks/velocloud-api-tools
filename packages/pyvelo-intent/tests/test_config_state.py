"""Tests for ConfigState snapshot and diff detection."""

from datetime import UTC, datetime
from typing import cast

import pytest
from pydantic import MISSING
from pyvelo.client.enums.configuration import ConfigurationModuleType
from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.models.configuration.common import SegmentMetadata
from pyvelo.client.models.configuration.device_settings.lan import LanSettings
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.device_settings.segment import (
    DeviceSegment,
    SegmentRoutes,
    StaticRoute,
)
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.wan.root import WanData
from pyvelo.client.schemas.configuration import (
    DeviceSettingsConfigurationModule,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
    WanConfigurationModule,
)
from pyvelo.config.resolver import ResolverContext
from pyvelo.core import at
from pyvelo.core.layers import Edge, Profile
from pyvelo.core.rebase import ItemMove, ItemPlace, Key
from pyvelo_intent.config.state import ConfigState, SaveResult, ValidationWarning
from pyvelo_intent.testing import make_firewall_module


def _make_config(name: str) -> EdgeConfiguration | EnterpriseConfigurationProfile:
    """Build a minimal typed configuration for testing."""
    if name == "Edge Specific Profile":
        return EdgeConfiguration(
            name=name, modules=[make_firewall_module(mod_id=1, config_id=100)]
        )
    return EnterpriseConfigurationProfile(
        name=name,
        modules=[make_firewall_module(mod_id=1, config_id=100, layer=Profile)],
    )


class TestConfigState:
    def test_take_snapshot_stores_deep_copy(self):
        edge_config = _make_config("Edge Specific Profile")
        profile_config = _make_config("Default Profile")
        assert isinstance(edge_config, EdgeConfiguration)
        assert isinstance(profile_config, EnterpriseConfigurationProfile)

        state = ConfigState(
            edge_config=edge_config,
            profile_config=profile_config,
            target_type="edge",
            resolver_context=ResolverContext(),
        )
        state.take_snapshot()

        # Mutate the edge config
        edge_config.firewall.data.firewall_enabled = False

        # Snapshot should still have original value
        snap = state.snapshot_for_target()
        assert isinstance(snap, EdgeConfiguration)
        assert snap.firewall.data.firewall_enabled is True

    def test_get_changed_modules_detects_change(self):
        edge_config = _make_config("Edge Specific Profile")
        profile_config = _make_config("Default Profile")
        assert isinstance(edge_config, EdgeConfiguration)
        assert isinstance(profile_config, EnterpriseConfigurationProfile)

        state = ConfigState(
            edge_config=edge_config,
            profile_config=profile_config,
            target_type="edge",
            resolver_context=ResolverContext(),
        )
        state.take_snapshot()

        # Mutate the firewall module
        edge_config.firewall.data.firewall_enabled = False

        changed = state.get_changed_modules()
        assert "firewall" in changed

    def test_get_changed_modules_no_change(self):
        edge_config = _make_config("Edge Specific Profile")
        profile_config = _make_config("Default Profile")
        assert isinstance(edge_config, EdgeConfiguration)
        assert isinstance(profile_config, EnterpriseConfigurationProfile)

        state = ConfigState(
            edge_config=edge_config,
            profile_config=profile_config,
            target_type="edge",
            resolver_context=ResolverContext(),
        )
        state.take_snapshot()

        # No mutations
        changed = state.get_changed_modules()
        assert changed == []

    def test_profile_target_type_diffs_profile_config(self):
        profile_config = _make_config("Default Profile")
        assert isinstance(profile_config, EnterpriseConfigurationProfile)

        state = ConfigState(
            edge_config=None,
            profile_config=profile_config,
            target_type="profile",
            resolver_context=ResolverContext(),
        )
        state.take_snapshot()

        # Mutate profile config
        profile_config.firewall.data.firewall_enabled = False

        changed = state.get_changed_modules()
        assert "firewall" in changed


class TestGetFieldChanges:
    def _state_with_snapshot(self) -> tuple[ConfigState, EdgeConfiguration]:
        edge_config = _make_config("Edge Specific Profile")
        profile_config = _make_config("Default Profile")
        assert isinstance(edge_config, EdgeConfiguration)
        assert isinstance(profile_config, EnterpriseConfigurationProfile)
        state = ConfigState(
            edge_config=edge_config,
            profile_config=profile_config,
            target_type="edge",
            resolver_context=ResolverContext(),
        )
        state.take_snapshot()
        return state, edge_config

    def test_no_changes_returns_empty(self):
        state, _ = self._state_with_snapshot()
        assert state.get_field_changes() == []

    def test_scalar_change_emits_field_change(self):
        from pyvelo_intent.config.state import ConfigFieldChange

        state, edge_config = self._state_with_snapshot()
        edge_config.firewall.data.firewall_enabled = False
        changes = state.get_field_changes()
        assert any(
            isinstance(c, ConfigFieldChange)
            and c.module == "firewall"
            and c.path == "firewall_enabled"
            and c.before is True
            and c.after is False
            for c in changes
        )

    def test_filter_by_module_name(self):
        state, edge_config = self._state_with_snapshot()
        edge_config.firewall.data.firewall_enabled = False
        # Only ask about a different module - should be empty
        assert state.get_field_changes(module_name="qos") == []
        # Ask about the actual mutated module - should be non-empty
        assert state.get_field_changes(module_name="firewall")

    def test_a_reorder_of_a_keyed_list_is_one_order_change(self):
        """Reordering a KeyField list is one change at the list, as in the plan."""
        from datetime import datetime

        from pyvelo.client.enums.configuration import ConfigurationModuleType
        from pyvelo.client.enums.enterprise import NetworkSegmentType
        from pyvelo.client.models.configuration.common import SegmentMetadata
        from pyvelo.client.models.configuration.firewall.segment import (
            FirewallSegment,
        )
        from pyvelo.client.schemas.configuration import (
            EdgeConfiguration,
            EnterpriseConfigurationProfile,
        )

        seg_a = FirewallSegment[Edge](
            segment=SegmentMetadata(
                name="A", segment_id=0, type_=NetworkSegmentType.REGULAR
            ),
            outbound=[],
        )
        seg_b = FirewallSegment[Edge](
            segment=SegmentMetadata(
                name="B", segment_id=1, type_=NetworkSegmentType.REGULAR
            ),
            outbound=[],
        )
        fw = FirewallConfigurationModule[Edge](
            name="firewall",
            data=FirewallData[Edge](segments=[seg_a, seg_b]),
            created=datetime(2024, 1, 1, tzinfo=UTC),
            modified=datetime(2024, 1, 1, tzinfo=UTC),
            id_=1,
            type_=ConfigurationModuleType.ENTERPRISE,
            description=None,
            configuration_id=100,
            enterprise_logical_id=None,
            schema_version=None,
            version=None,
            metadata=None,
        )
        edge_config = EdgeConfiguration(name="Edge", modules=[fw])
        profile_config = EnterpriseConfigurationProfile(name="P", modules=[])
        state = ConfigState(
            edge_config=edge_config,
            profile_config=profile_config,
            target_type="edge",
            resolver_context=ResolverContext(),
        )
        state.take_snapshot()
        # Reorder segments — same content, reversed order
        edge_config.firewall.data.segments = [seg_b, seg_a]
        changes = state.get_field_changes(module_name="firewall")
        assert [(c.path, c.before, c.after) for c in changes] == [
            ("segments", ["A", "B"], ["B", "A"])
        ]
        assert changes[0].moves == (ItemMove(Key("B"), ItemPlace()),)
        (planned,) = state.plan().changed
        assert [c.path for c in planned.changes] == ["segments"]


def test_field_changes_address_a_composite_key():
    route = StaticRoute(
        destination="10.0.0.0",
        netmask="255.255.255.0",
        gateway="192.168.1.1",
        cidr_prefix=24,
        wan_interface="GE3",
    )
    module = DeviceSettingsConfigurationModule(
        name="deviceSettings",
        data=DeviceSettingsData(
            lan=LanSettings(),
            segments=[
                DeviceSegment(
                    segment=SegmentMetadata(
                        name="Global Segment",
                        segment_id=0,
                        type_=NetworkSegmentType.REGULAR,
                    ),
                    routes=SegmentRoutes(static=[route]),
                )
            ],
        ),
        created=datetime(2024, 1, 1, tzinfo=UTC),
        modified=datetime(2024, 1, 1, tzinfo=UTC),
        id_=2,
        type_=ConfigurationModuleType.ENTERPRISE,
        description=None,
        configuration_id=100,
        enterprise_logical_id=None,
        schema_version=None,
        version=None,
        metadata=None,
    )
    profile_config = EnterpriseConfigurationProfile(name="P", modules=[module])
    state = ConfigState(
        edge_config=None,
        profile_config=profile_config,
        target_type="profile",
        resolver_context=ResolverContext(),
    )
    state.take_snapshot()
    segment = profile_config.device_settings.data.segments[0]
    assert segment.routes is not MISSING
    segment.routes.static[0].cost = 5
    changes = state.get_field_changes(module_name="deviceSettings")
    assert [(c.path, c.before, c.after) for c in changes] == [
        (
            'segments["Global Segment"].routes.static["10.0.0.0",24,"192.168.1.1",GE3].cost',
            0,
            5,
        )
    ]


def _wan_state(links: list[dict[str, object]]) -> tuple[ConfigState, WanData]:
    data = WanData.model_validate({"links": links})
    module = WanConfigurationModule(
        data=data,
        created=datetime(2024, 1, 1, tzinfo=UTC),
        modified=datetime(2024, 1, 1, tzinfo=UTC),
        id_=3,
        type_=ConfigurationModuleType.ENTERPRISE,
        description=None,
        configuration_id=100,
        enterprise_logical_id=None,
        schema_version=None,
        version=None,
        metadata=None,
    )
    state = ConfigState(
        edge_config=None,
        profile_config=EnterpriseConfigurationProfile(name="P", modules=[module]),
        target_type="profile",
        resolver_context=ResolverContext(),
    )
    state.take_snapshot()
    return state, data


def test_field_changes_show_an_edit_to_a_keyless_item():
    # Neither link has an internalId, so neither has a key.
    state, data = _wan_state(
        [{"name": "A", "type": "WIRED"}, {"name": "B", "type": "WIRED"}]
    )
    _ = data.merge_patch({"links": {at(0): {"isp": "x"}}})
    # A link without a key cannot be told from the other: the plan writes the list whole.
    assert _isps(state) == ("links", [None, None], ["x", None])


def _isps(state: ConfigState) -> tuple[str, list[object], list[object]]:
    """The one WAN change of *state*: its path, and the ``isp`` of each link before and after."""
    (change,) = state.get_field_changes(module_name="WAN")
    before = cast(list[dict[str, object]], change.before)
    after = cast(list[dict[str, object]], change.after)
    return str(change.path), [x["isp"] for x in before], [x["isp"] for x in after]


def test_field_changes_show_an_edit_among_repeated_keys():
    link_id = "00000000-0000-0000-0000-000000000001"
    state, data = _wan_state(
        [
            {"internalId": link_id, "name": "A", "type": "WIRED"},
            {"internalId": link_id, "name": "B", "type": "WIRED"},
        ]
    )
    _ = data.merge_patch({"links": {at(0): {"isp": "x"}}})
    assert _isps(state) == ("links", [None, None], ["x", None])


def test_field_changes_take_a_module_by_either_name():
    link_id = "00000000-0000-0000-0000-000000000001"
    state, data = _wan_state([{"internalId": link_id, "name": "A", "type": "WIRED"}])
    _ = data.merge_patch({"links": {"A": {"isp": "x"}}})
    by_vco_name = state.get_field_changes(module_name="WAN")
    assert [(c.module, c.path) for c in by_vco_name] == [
        ("WAN", f"links[{link_id},IPv4].isp")
    ]
    assert state.get_field_changes(module_name="wan") == by_vco_name
    assert state.get_field_changes() == by_vco_name
    # A module the target does not have has no changes.
    assert state.get_field_changes(module_name="firewall") == []


def test_field_changes_of_a_name_that_is_no_module_raise():
    state, _ = _wan_state([])
    with pytest.raises(ValueError, match="Unknown module: 'no-such-module'"):
        _ = state.get_field_changes(module_name="no-such-module")


class TestSaveResult:
    def test_save_result_defaults(self):
        result = SaveResult()
        assert result.modules_updated == []
        assert result.modules_inserted == []
        assert result.warnings == []


class TestValidationWarning:
    def test_warning_defaults(self):
        w = ValidationWarning(path="firewall.segments", message="test", code="TEST")
        assert w.severity == "warning"
