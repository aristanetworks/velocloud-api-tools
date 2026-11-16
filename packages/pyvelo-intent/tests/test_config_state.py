"""Tests for ConfigState snapshot and diff detection."""

from datetime import UTC, datetime

from pyvelo.client.enums.configuration import ConfigurationModuleType
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
)
from pyvelo.config.resolver import ResolverContext
from pyvelo_intent.config.state import ConfigState, SaveResult, ValidationWarning


def _make_config(
    name: str, modules: list[AnyConfigurationModule] | None = None
) -> EdgeConfiguration | EnterpriseConfigurationProfile:
    """Build a minimal typed configuration for testing."""
    fw_data = FirewallData()
    fw_mod = FirewallConfigurationModule(
        name="firewall",
        data=fw_data,
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

    if name == "Edge Specific Profile":
        return EdgeConfiguration(
            name=name,
            modules=[fw_mod] if modules is None else modules,
        )
    return EnterpriseConfigurationProfile(
        name=name,
        modules=[fw_mod] if modules is None else modules,
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

    def test_keyfield_list_diffs_by_key_not_by_index(self):
        """Reordering a KeyField list should not show as a change."""
        from datetime import datetime

        from pyvelo.client.enums.configuration import ConfigurationModuleType
        from pyvelo.client.enums.enterprise import NetworkSegmentType
        from pyvelo.client.models.configuration.common import SegmentMetadata
        from pyvelo.client.models.configuration.firewall.root import FirewallData
        from pyvelo.client.models.configuration.firewall.segment import (
            FirewallSegment,
        )
        from pyvelo.client.schemas.configuration import (
            EdgeConfiguration,
            EnterpriseConfigurationProfile,
            FirewallConfigurationModule,
        )

        seg_a = FirewallSegment(
            segment=SegmentMetadata(
                name="A", segment_id=0, type_=NetworkSegmentType.REGULAR
            ),
            outbound=[],
        )
        seg_b = FirewallSegment(
            segment=SegmentMetadata(
                name="B", segment_id=1, type_=NetworkSegmentType.REGULAR
            ),
            outbound=[],
        )
        fw = FirewallConfigurationModule(
            name="firewall",
            data=FirewallData(segments=[seg_a, seg_b]),
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
        # Sanity: get_field_changes should not flag a change just from reorder
        changes = state.get_field_changes(module_name="firewall")
        assert all("segments" not in c.path for c in changes), (
            f"reordering produced spurious change: {changes}"
        )


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
