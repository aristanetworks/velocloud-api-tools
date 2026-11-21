"""Tests for ConfigPipeline.apply()."""

from datetime import UTC, datetime

import pytest
from pyvelo.client.enums.configuration import (
    ConfigurationModuleName,
    ConfigurationModuleType,
)
from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.models.configuration.common import SegmentMetadata
from pyvelo.client.models.configuration.device_settings.lan import (
    LanNetwork,
    LanSettings,
)
from pyvelo.client.models.configuration.device_settings.root import (
    DeviceSettingsData,
    Ntp,
)
from pyvelo.client.models.configuration.device_settings.segment import (
    DeviceSegment,
    DnsProvider,
    SegmentDns,
)
from pyvelo.client.models.configuration.firewall.segment import FirewallSegment
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    DeviceSettingsConfigurationModule,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    QosConfigurationModule,
)
from pyvelo.config.resolver import ResolverContext, ResolverRegistry
from pyvelo.core.identity import ResolutionError
from pyvelo.core.rebase import Key, Path
from pyvelo.testing import make_module_json, make_segment_resolver
from pyvelo_intent import PatchError, add, delete, move
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.config.state import ConfigState
from pyvelo_intent.testing import (
    make_firewall_module,
    make_firewall_segment,
)
from test_state_plan import (  # pyright: ignore[reportImplicitRelativeImport]
    desc,
)
from test_state_plan import (  # pyright: ignore[reportImplicitRelativeImport]
    make_state as make_raw_state,
)

_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _make_device_settings_module(
    mod_id: int, config_id: int
) -> DeviceSettingsConfigurationModule:
    return DeviceSettingsConfigurationModule(
        name="deviceSettings",
        data=DeviceSettingsData(lan=LanSettings(), segments=[]),
        created=_TS,
        modified=_TS,
        id_=mod_id,
        type_=ConfigurationModuleType.ENTERPRISE,
        description=None,
        configuration_id=config_id,
        enterprise_logical_id=None,
        schema_version=None,
        version=None,
        metadata=None,
    )


def _make_qos_module(mod_id: int, config_id: int) -> QosConfigurationModule:
    return QosConfigurationModule(
        name="QOS",
        data=QosData(),
        created=_TS,
        modified=_TS,
        id_=mod_id,
        type_=ConfigurationModuleType.ENTERPRISE,
        description=None,
        configuration_id=config_id,
        enterprise_logical_id=None,
        schema_version=None,
        version=None,
        metadata=None,
    )


def _make_state(
    edge_segments: list[FirewallSegment] | None = None,
    profile_segments: list[FirewallSegment] | None = None,
    *,
    include_device_settings: bool = False,
    include_qos: bool = False,
) -> ConfigState:
    edge_modules: list[AnyConfigurationModule] = [
        make_firewall_module(mod_id=1, config_id=10, segments=edge_segments)
    ]
    profile_modules: list[AnyConfigurationModule] = [
        make_firewall_module(mod_id=2, config_id=20, segments=profile_segments)
    ]
    if include_device_settings:
        edge_modules.append(_make_device_settings_module(mod_id=3, config_id=10))
        profile_modules.append(_make_device_settings_module(mod_id=4, config_id=20))
    if include_qos:
        edge_modules.append(_make_qos_module(mod_id=5, config_id=10))
        profile_modules.append(_make_qos_module(mod_id=6, config_id=20))
    edge_config = EdgeConfiguration(
        name="Edge Specific Profile",
        id_=10,
        modules=edge_modules,
    )
    profile_config = EnterpriseConfigurationProfile(
        name="Default Profile",
        id_=20,
        modules=profile_modules,
    )
    state = ConfigState(
        edge_config=edge_config,
        profile_config=profile_config,
        target_type="edge",
        resolver_context=ResolverContext(),
    )
    state.take_snapshot()
    return state


class TestResolveModuleName:
    @pytest.mark.asyncio
    async def test_firewall(self):
        pipeline = ConfigPipeline(client=None)
        state = _make_state()
        state = await pipeline.apply(
            state, module="firewall", patch={"firewall_enabled": False}
        )
        assert state.edge_config is not None
        assert state.edge_config.firewall.data.firewall_enabled is False

    @pytest.mark.asyncio
    async def test_device_settings_snake_case(self):
        pipeline = ConfigPipeline(client=None)
        state = _make_state(include_device_settings=True)
        state = await pipeline.apply(
            state, module="device_settings", patch={"ha": {"enabled": True}}
        )
        assert ConfigurationModuleName.DEVICE_SETTINGS in state.get_changed_modules()

    @pytest.mark.asyncio
    async def test_qos_uppercase(self):
        pipeline = ConfigPipeline(client=None)
        state = _make_state(include_qos=True)
        state = await pipeline.apply(
            state,
            module="QOS",
            patch={"qos_client_connector": {"data": {"enabled": True}}},
        )
        assert ConfigurationModuleName.QOS in state.get_changed_modules()

    @pytest.mark.asyncio
    async def test_unknown_raises(self):
        pipeline = ConfigPipeline(client=None)
        state = _make_state()
        with pytest.raises(ValueError, match="Unknown module"):
            _ = await pipeline.apply(state, module="nonexistent", patch={})


@pytest.mark.asyncio
async def test_apply_simple_patch():
    """Apply a simple scalar patch to firewall module."""
    pipeline = ConfigPipeline(client=None)
    state = _make_state()

    state = await pipeline.apply(
        state,
        module="firewall",
        patch={"firewall_enabled": False},
    )

    assert state.edge_config is not None
    assert state.edge_config.firewall.data.firewall_enabled is False


@pytest.mark.asyncio
async def test_apply_keyed_list_merge():
    """Apply a patch that merges into a KeyField list (segments by name)."""
    pipeline = ConfigPipeline(client=None)

    global_seg = make_firewall_segment("Global Segment", 0)
    state = _make_state(edge_segments=[global_seg], profile_segments=[global_seg])

    # Add an outbound rule to the Global Segment
    state = await pipeline.apply(
        state,
        module="firewall",
        patch={
            "segments": {
                "Global Segment": {
                    "outbound": {
                        "block-ssh": add(
                            {
                                "name": "block-ssh",
                                "action": {"allow_or_deny": "deny"},
                                "match": {
                                    "proto": 6,
                                    "dport_low": 22,
                                    "dport_high": 22,
                                },
                            }
                        )
                    }
                }
            }
        },
    )

    assert state.edge_config is not None
    fw_data = state.edge_config.firewall.data
    seg = fw_data.get_segment(name="Global Segment")
    assert len(seg.outbound) == 1
    assert seg.outbound[0].name == "block-ssh"
    assert seg.outbound[0].match.proto == 6


@pytest.mark.asyncio
async def test_apply_detects_change():
    """After apply, get_changed_modules should report the module as changed."""
    pipeline = ConfigPipeline(client=None)
    state = _make_state()

    assert state.get_changed_modules() == []

    state = await pipeline.apply(
        state,
        module="firewall",
        patch={"firewall_enabled": False},
    )

    assert "firewall" in state.get_changed_modules()


@pytest.mark.asyncio
async def test_apply_camel_case_module_name():
    """Public module names should resolve the canonical camelCase spelling."""
    pipeline = ConfigPipeline(client=None)
    state = _make_state(include_device_settings=True)
    state = await pipeline.apply(
        state,
        module="deviceSettings",
        patch={"ha": {"enabled": True}},
    )

    assert ConfigurationModuleName.DEVICE_SETTINGS in state.get_changed_modules()


# --- single-module patches on a profile target ------------------------------


def _profile_state(*modules: AnyConfigurationModule) -> ConfigState:
    return ConfigState(
        edge_config=None,
        profile_config=EnterpriseConfigurationProfile(
            name="Default Profile", id_=20, modules=list(modules)
        ),
        target_type="profile",
        resolver_context=ResolverContext(),
    )


def _global_segment() -> SegmentMetadata:
    return SegmentMetadata(
        name="Global Segment", segment_id=0, type_=NetworkSegmentType.REGULAR
    )


def _ds_module(
    *,
    ntp: Ntp | None = None,
    lan: LanSettings | None = None,
    segments: list[DeviceSegment] | None = None,
) -> DeviceSettingsConfigurationModule:
    module = _make_device_settings_module(mod_id=2, config_id=20)
    module.data = DeviceSettingsData(
        lan=lan or LanSettings(), segments=segments or [], ntp=ntp or Ntp()
    )
    return module


_BLOCK_SSH: dict[str, object] = {
    "name": "block-ssh",
    "match": {},
    "action": {"allow_or_deny": "deny"},
}


def _block_ssh_patch() -> dict[str, object]:
    return {
        "segments": {"Global Segment": {"outbound": {"block-ssh": add(_BLOCK_SSH)}}}
    }


@pytest.mark.asyncio
async def test_apply_ntp_enabled():
    state = _profile_state(_ds_module(ntp=Ntp(enabled=False)))
    state = await ConfigPipeline(client=None).apply(
        state, module="device_settings", patch={"ntp": {"enabled": True}}
    )
    ntp = state.profile.device_settings.ntp
    assert isinstance(ntp, Ntp)
    assert ntp.enabled is True


@pytest.mark.asyncio
async def test_apply_adds_a_firewall_rule_to_a_segment():
    segment = make_firewall_segment("Global Segment", 0)
    state = _profile_state(
        make_firewall_module(mod_id=1, config_id=20, segments=[segment])
    )
    state = await ConfigPipeline(client=None).apply(
        state, module="firewall", patch=_block_ssh_patch()
    )
    outbound = state.profile.firewall.segment("Global Segment").outbound
    assert [rule.name for rule in outbound] == ["block-ssh"]


@pytest.mark.asyncio
async def test_apply_to_a_segment_the_profile_lacks_changes_nothing():
    segment = make_firewall_segment("Global Segment", 0)
    state = _profile_state(
        make_firewall_module(mod_id=1, config_id=20, segments=[segment])
    )
    with pytest.raises(PatchError, match="not supported yet") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state,
            module="firewall",
            patch={"segments": {"Guest": add({"outbound": {}})}},
        )
    assert exc.value.path == Path.parse("segments[Guest]")
    assert [s.segment.name for s in state.profile.firewall.segments] == [
        "Global Segment"
    ]


@pytest.mark.asyncio
async def test_two_applies_in_a_row():
    segment = make_firewall_segment("Global Segment", 0)
    state = _profile_state(
        _ds_module(ntp=Ntp(enabled=False)),
        make_firewall_module(mod_id=1, config_id=20, segments=[segment]),
    )
    pipeline = ConfigPipeline(client=None)
    state = await pipeline.apply(
        state, module="device_settings", patch={"ntp": {"enabled": True}}
    )
    state = await pipeline.apply(state, module="firewall", patch=_block_ssh_patch())

    ntp = state.profile.device_settings.ntp
    assert isinstance(ntp, Ntp)
    assert ntp.enabled is True
    outbound = state.profile.firewall.segment("Global Segment").outbound
    assert len(outbound) == 1


@pytest.mark.asyncio
async def test_apply_dns():
    dns = SegmentDns(
        primary_provider=DnsProvider(ref="deviceSettings:dns:primaryProvider"),
        backup_provider=DnsProvider(ref="deviceSettings:dns:backupProvider"),
        private_providers=DnsProvider(ref="deviceSettings:dns:privateProviders"),
    )
    state = _profile_state(
        _ds_module(segments=[DeviceSegment(segment=_global_segment(), dns=dns)])
    )
    state = await ConfigPipeline(client=None).apply(
        state,
        module="device_settings",
        patch={"segments": {"Global Segment": {"dns": {"source_interface": "GE1"}}}},
    )
    segment = state.profile.device_settings.segment("Global Segment")
    assert isinstance(segment.dns, SegmentDns)
    assert segment.dns.source_interface == "GE1"


@pytest.mark.asyncio
async def test_apply_updates_a_vlan():
    network = LanNetwork(name="Corp", vlan_id=100, cidr_ip="10.0.1.0")
    state = _profile_state(_ds_module(lan=LanSettings(networks=[network])))
    state = await ConfigPipeline(client=None).apply(
        state,
        module="device_settings",
        patch={"lan": {"networks": {"100": {"advertise": False}}}},
    )
    (updated,) = state.profile.device_settings.lan.networks
    assert updated.advertise is False


# --- segments on an edge target (spec 5.10.3) --------------------------------
#
# The edge layer has Global Segment only; its profile has Global Segment and
# Guest (segmentId 2).

_DENY: dict[str, object] = {"action": {"allow_or_deny": "deny"}, "match": {}}


def _guest_rule_patch(selector: str = "Guest") -> dict[str, object]:
    return {"segments": {selector: {"outbound": {"block": add(_DENY)}}}}


def _edge_segment_names(state: ConfigState) -> list[str]:
    return [s.segment.name for s in state.edge.firewall.segments]


async def _segment_resolvers() -> ResolverRegistry:
    registry = ResolverRegistry()
    registry.register(
        await make_segment_resolver(
            [
                {"name": "Global Segment", "segment_id": 0},
                {"name": "Guest", "segment_id": 2},
            ]
        )
    )
    return registry


@pytest.mark.asyncio
async def test_change_of_an_inherited_segment_adds_the_placeholder():
    state = make_raw_state()
    _ = await ConfigPipeline(client=None).apply(
        state, module="firewall", patch=_guest_rule_patch()
    )
    guest = state.edge.firewall.get_segment(name="Guest")
    assert [r.name for r in guest.outbound] == ["block"]
    (firewall,) = state.plan().modules
    assert (firewall.operation, firewall.reconciled_segments) == ("update", ("Guest",))


@pytest.mark.asyncio
async def test_inherited_segment_by_another_case_and_by_id():
    for selector in ("guest", "2"):
        state = make_raw_state()
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=_guest_rule_patch(selector)
        )
        assert _edge_segment_names(state) == ["Global Segment", "Guest"]
        guest = state.edge.firewall.get_segment(name="Guest")
        assert guest.segment.segment_id == 2
        assert [r.name for r in guest.outbound] == ["block"]


@pytest.mark.asyncio
async def test_move_of_an_inherited_segment_adds_the_placeholder():
    state = make_raw_state()
    _ = await ConfigPipeline(client=None).apply(
        state, module="firewall", patch={"segments": {"Guest": move(first=True)}}
    )
    assert _edge_segment_names(state) == ["Guest", "Global Segment"]


@pytest.mark.asyncio
async def test_add_of_an_inherited_segment_raises():
    state = make_raw_state()
    with pytest.raises(PatchError, match="plain object") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch={"segments": {"Guest": add({})}}
        )
    assert exc.value.path == Path.parse("segments[Guest]")
    assert _edge_segment_names(state) == ["Global Segment"]


@pytest.mark.asyncio
async def test_plus_add_of_an_inherited_segment_raises():
    state = make_raw_state()
    patch: dict[str, object] = {"segments": {"+": add({"segment": {"name": "Guest"}})}}
    with pytest.raises(PatchError, match="plain object") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )
    assert exc.value.path == Path(("segments", Key("+")))
    assert _edge_segment_names(state) == ["Global Segment"]


@pytest.mark.asyncio
async def test_delete_of_an_inherited_segment_raises_and_makes_no_placeholder():
    state = make_raw_state()
    with pytest.raises(PatchError, match="no entry to delete") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch={"segments": {"Guest": delete()}}
        )
    assert exc.value.path == Path.parse("segments[Guest]")
    assert _edge_segment_names(state) == ["Global Segment"]


@pytest.mark.asyncio
async def test_segment_named_twice_raises():
    state = make_raw_state()
    patch: dict[str, object] = {
        "segments": {"Guest": {"outbound": {}}, "guest": {"outbound": {}}}
    }
    with pytest.raises(PatchError, match="Guest"):
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )
    assert _edge_segment_names(state) == ["Global Segment"]


@pytest.mark.asyncio
async def test_segment_neither_layer_has_is_left_to_merge_patch():
    patch: dict[str, object] = {"segments": {"Nope": {"outbound": {}}}}
    state = make_raw_state()
    with pytest.raises(PatchError, match="no item has the key") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )
    assert exc.value.path == Path.parse("segments[Nope]")
    assert _edge_segment_names(state) == ["Global Segment"]
    pipeline = ConfigPipeline(client=None, resolvers=await _segment_resolvers())
    with pytest.raises(ResolutionError, match="Nope"):
        _ = await pipeline.apply(state, module="firewall", patch=patch)
    assert _edge_segment_names(state) == ["Global Segment"]


@pytest.mark.asyncio
async def test_failed_apply_leaves_the_plan_as_it_was():
    state = make_raw_state()
    before = state.plan().render()
    patch: dict[str, object] = {
        "segments": {"Guest": {"outbound": {"block": add(_DENY)}, "no_such_key": 1}}
    }
    with pytest.raises(PatchError, match="no_such_key"):
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )
    assert state.plan().render() == before
    assert _edge_segment_names(state) == ["Global Segment"]


@pytest.mark.asyncio
async def test_failed_apply_removes_the_module_it_created():
    state = make_raw_state(edge_modules=[])
    before = state.plan().render()
    with pytest.raises(PatchError, match="no_such_key"):
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch={"no_such_key": 1}
        )
    assert state.find_module("firewall") is None
    assert state.plan().render() == before


@pytest.mark.asyncio
async def test_good_apply_on_an_edge_with_no_firewall_row_plans_an_insert():
    state = make_raw_state(edge_modules=[])
    _ = await ConfigPipeline(client=None).apply(
        state, module="firewall", patch={"firewall_enabled": False}
    )
    assert [(m.name, m.operation) for m in state.plan().modules] == [
        ("firewall", "insert")
    ]


# --- segments on a profile target ---------------------------------------------


def _segment_profile_state(*names: str) -> ConfigState:
    """A profile whose firewall, QoS and device settings each have segments *names*."""
    descriptors = [desc(2 * i, name) for i, name in enumerate(names)]
    rows = [
        make_module_json(
            "firewall",
            {"segments": [{"segment": d, "outbound": []} for d in descriptors]},
            module_id=1,
            configuration_id=20,
        ),
        make_module_json(
            "QOS",
            {"segments": [{"segment": d, "rules": []} for d in descriptors]},
            module_id=2,
            configuration_id=20,
        ),
        make_module_json(
            "deviceSettings",
            {
                "lan": {"networks": [], "interfaces": []},
                "segments": [{"segment": d} for d in descriptors],
            },
            module_id=3,
            configuration_id=20,
        ),
    ]
    return ConfigState(
        edge_config=None,
        profile_config=EnterpriseConfigurationProfile.model_validate(
            {"name": "P", "id": 20, "modules": rows}
        ),
        target_type="profile",
        resolver_context=ResolverContext(),
    )


def _profile_segment_names(state: ConfigState) -> list[list[str]]:
    profile = state.profile
    return [
        [s.segment.name for s in profile.firewall.segments],
        [s.segment.name for s in profile.qos.segments],
        [s.segment.name for s in profile.device_settings.segments],
    ]


@pytest.mark.asyncio
async def test_profile_target_refuses_segment_add_and_delete():
    state = _segment_profile_state("Global Segment")
    refused: list[tuple[dict[str, object], Path, str]] = [
        ({"Guest": add({})}, Path.parse("segments[Guest]"), "not supported yet"),
        (
            {"+": add({"segment": {"name": "Guest"}})},
            Path(("segments", Key("+"))),
            "not supported yet",
        ),
        (
            {"Global Segment": delete()},
            Path(("segments", Key("Global Segment"))),
            "not supported",
        ),
    ]
    for module in ("firewall", "qos", "device_settings"):
        for segments, where, message in refused:
            with pytest.raises(PatchError, match=message) as exc:
                _ = await ConfigPipeline(client=None).apply(
                    state, module=module, patch={"segments": segments}
                )
            assert exc.value.path == where
    assert _profile_segment_names(state) == [["Global Segment"]] * 3


@pytest.mark.asyncio
async def test_profile_target_changes_and_moves_a_segment():
    state = _segment_profile_state("Global Segment", "Guest")
    patch: dict[str, object] = {
        "segments": {
            "Global Segment": {"outbound": {"block": add(_DENY)}},
            "Guest": move(first=True),
        }
    }
    _ = await ConfigPipeline(client=None).apply(state, module="firewall", patch=patch)
    firewall = state.profile.firewall
    assert [s.segment.name for s in firewall.segments] == ["Guest", "Global Segment"]
    blocked = firewall.get_segment(name="Global Segment").outbound
    assert [r.name for r in blocked] == ["block"]
