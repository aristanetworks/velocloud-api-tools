"""Tests for ConfigPipeline.apply()."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, ClassVar, final, overload, override

import pytest
from pydantic import MISSING
from pyvelo.client.enums.configuration import (
    ConfigurationModuleName,
    ConfigurationModuleType,
)
from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.exceptions import LossyFieldError
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
    DnsBackupProvider,
    DnsPrimaryProvider,
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
from pyvelo.client.schemas.enterprise import (
    GetEnterpriseNetworkSegments,
    NetworkSegment,
)
from pyvelo.config import PatchError, add, delete, move
from pyvelo.config.pipeline import (
    ConfigPipeline,
    _prepare_segments,  # pyright: ignore[reportPrivateUsage]
)
from pyvelo.config.resolver import ResolverContext, ResolverRegistry
from pyvelo.config.resolvers.segment import SegmentResolver
from pyvelo.config.state import ConfigState
from pyvelo.core.identity import ResolutionError
from pyvelo.core.layers import Edge, Profile
from pyvelo.core.rebase import Key, Path
from pyvelo.testing import (
    MockVcoClient,
    make_firewall_module,
    make_firewall_segment,
    make_module_json,
    make_segment_resolver,
    network_segment_wire,
)
from test_state_plan import (  # pyright: ignore[reportImplicitRelativeImport]
    desc,
)
from test_state_plan import (  # pyright: ignore[reportImplicitRelativeImport]
    make_state as make_raw_state,
)

_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _make_device_settings_module(
    mod_id: int, config_id: int, data: DeviceSettingsData | None = None
) -> DeviceSettingsConfigurationModule:
    return DeviceSettingsConfigurationModule(
        name="deviceSettings",
        data=data or DeviceSettingsData(lan=LanSettings(), segments=[]),
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


@overload
def _make_qos_module(
    mod_id: int, config_id: int, layer: type[Edge] = ...
) -> QosConfigurationModule[Edge]: ...
@overload
def _make_qos_module(
    mod_id: int, config_id: int, layer: type[Profile]
) -> QosConfigurationModule[Profile]: ...
def _make_qos_module(
    mod_id: int, config_id: int, layer: type[Edge] | type[Profile] = Edge
) -> QosConfigurationModule[Edge] | QosConfigurationModule[Profile]:
    fields: dict[str, Any] = {
        "name": "QOS",
        "created": _TS,
        "modified": _TS,
        "id_": mod_id,
        "type_": ConfigurationModuleType.ENTERPRISE,
        "description": None,
        "configuration_id": config_id,
        "enterprise_logical_id": None,
        "schema_version": None,
        "version": None,
        "metadata": None,
    }
    if layer is Profile:
        return QosConfigurationModule[Profile](data=QosData[Profile](), **fields)
    return QosConfigurationModule[Edge](data=QosData[Edge](), **fields)


def _make_state(
    edge_segments: list[FirewallSegment[Edge]] | None = None,
    profile_segments: list[FirewallSegment[Profile]] | None = None,
    *,
    include_device_settings: bool = False,
    include_qos: bool = False,
) -> ConfigState:
    edge_modules: list[AnyConfigurationModule[Edge]] = [
        make_firewall_module(mod_id=1, config_id=10, segments=edge_segments)
    ]
    profile_modules: list[AnyConfigurationModule[Profile]] = [
        make_firewall_module(
            mod_id=2, config_id=20, segments=profile_segments, layer=Profile
        )
    ]
    if include_device_settings:
        edge_modules.append(_make_device_settings_module(mod_id=3, config_id=10))
        profile_modules.append(_make_device_settings_module(mod_id=4, config_id=20))
    if include_qos:
        edge_modules.append(_make_qos_module(mod_id=5, config_id=10))
        profile_modules.append(_make_qos_module(mod_id=6, config_id=20, layer=Profile))
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
    async def test_firewall(self) -> None:
        pipeline = ConfigPipeline(client=None)
        state = _make_state()
        state = await pipeline.apply(
            state, module="firewall", patch={"firewall_enabled": False}
        )
        assert state.edge_config is not None
        assert state.edge_config.firewall.data.firewall_enabled.local is False

    @pytest.mark.asyncio
    async def test_device_settings_snake_case(self) -> None:
        pipeline = ConfigPipeline(client=None)
        state = _make_state(include_device_settings=True)
        state = await pipeline.apply(
            state, module="device_settings", patch={"ha": {"enabled": True}}
        )
        assert ConfigurationModuleName.DEVICE_SETTINGS in state.get_changed_modules()

    @pytest.mark.asyncio
    async def test_qos_uppercase(self) -> None:
        pipeline = ConfigPipeline(client=None)
        state = _make_state(include_qos=True)
        state = await pipeline.apply(
            state,
            module="QOS",
            patch={"service_rate_limit": {"value": 50}},
        )
        assert ConfigurationModuleName.QOS in state.get_changed_modules()

    @pytest.mark.asyncio
    async def test_unknown_raises(self) -> None:
        pipeline = ConfigPipeline(client=None)
        state = _make_state()
        with pytest.raises(ValueError, match="Unknown module"):
            _ = await pipeline.apply(state, module="nonexistent", patch={})


@pytest.mark.asyncio
async def test_apply_simple_patch() -> None:
    """Apply a simple scalar patch to firewall module."""
    pipeline = ConfigPipeline(client=None)
    state = _make_state()

    state = await pipeline.apply(
        state,
        module="firewall",
        patch={"firewall_enabled": False},
    )

    assert state.edge_config is not None
    assert state.edge_config.firewall.data.firewall_enabled.local is False


@pytest.mark.asyncio
async def test_apply_keyed_list_merge() -> None:
    """Apply a patch that merges into a KeyField list (segments by name)."""
    pipeline = ConfigPipeline(client=None)

    state = _make_state(
        edge_segments=[make_firewall_segment("Global Segment", 0)],
        profile_segments=[make_firewall_segment("Global Segment", 0, layer=Profile)],
    )

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
async def test_apply_detects_change() -> None:
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
async def test_apply_camel_case_module_name() -> None:
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


def _profile_state(*modules: AnyConfigurationModule[Profile]) -> ConfigState:
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
    # The data is part of the module as read: a segment added after the read
    # would be an edit that adds a segment row, which needs its resolvers.
    return _make_device_settings_module(
        mod_id=2,
        config_id=20,
        data=DeviceSettingsData(
            lan=lan or LanSettings(), segments=segments or [], ntp=ntp or Ntp()
        ),
    )


_BLOCK_SSH: dict[str, object] = {
    "name": "block-ssh",
    "match": {},
    "action": {"allow_or_deny": "deny"},
}


def _block_ssh_patch() -> dict[str, object]:
    return {"block-ssh": add(_BLOCK_SSH)}


@pytest.mark.asyncio
async def test_apply_ntp_enabled() -> None:
    state = _profile_state(_ds_module(ntp=Ntp(enabled=False)))
    state = await ConfigPipeline(client=None).apply(
        state, module="device_settings", path="ntp", patch={"enabled": True}
    )
    ntp = state.profile.device_settings.ntp
    assert isinstance(ntp, Ntp)
    assert ntp.enabled is True


@pytest.mark.asyncio
async def test_apply_adds_a_firewall_rule_to_a_segment() -> None:
    segment = make_firewall_segment("Global Segment", 0, layer=Profile)
    state = _profile_state(
        make_firewall_module(mod_id=1, config_id=20, segments=[segment], layer=Profile)
    )
    state = await ConfigPipeline(client=None).apply(
        state,
        module="firewall",
        segment="Global Segment",
        path="outbound",
        patch=_block_ssh_patch(),
    )
    outbound = state.profile.firewall.segment("Global Segment").outbound
    assert [rule.name for rule in outbound] == ["block-ssh"]


@pytest.mark.asyncio
async def test_apply_to_a_segment_the_profile_lacks_changes_nothing() -> None:
    segment = make_firewall_segment("Global Segment", 0, layer=Profile)
    state = _profile_state(
        make_firewall_module(mod_id=1, config_id=20, segments=[segment], layer=Profile)
    )
    # A segment is assigned to the three modules of a profile; this one has the
    # firewall only, and inserting a module into a profile is not supported.
    with pytest.raises(PatchError, match="its three modules together") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state,
            module="firewall",
            segment="Guest",
            patch=add({"outbound": {}}),
        )
    assert exc.value.path == Path.parse("segments")
    assert exc.value.kind == "segment_rule"
    assert "no deviceSettings module" in exc.value.message
    assert [s.segment.name for s in state.profile.firewall.segments] == [
        "Global Segment"
    ]


@pytest.mark.asyncio
async def test_two_applies_in_a_row() -> None:
    segment = make_firewall_segment("Global Segment", 0, layer=Profile)
    state = _profile_state(
        _ds_module(ntp=Ntp(enabled=False)),
        make_firewall_module(mod_id=1, config_id=20, segments=[segment], layer=Profile),
    )
    pipeline = ConfigPipeline(client=None)
    state = await pipeline.apply(
        state, module="device_settings", path="ntp", patch={"enabled": True}
    )
    state = await pipeline.apply(
        state,
        module="firewall",
        segment="Global Segment",
        path="outbound",
        patch=_block_ssh_patch(),
    )

    ntp = state.profile.device_settings.ntp
    assert isinstance(ntp, Ntp)
    assert ntp.enabled is True
    outbound = state.profile.firewall.segment("Global Segment").outbound
    assert len(outbound) == 1


@pytest.mark.asyncio
async def test_apply_dns() -> None:
    dns = SegmentDns(
        primary_provider=DnsPrimaryProvider(ref="deviceSettings:dns:primaryProvider"),
        backup_provider=DnsBackupProvider(ref="deviceSettings:dns:backupProvider"),
        private_providers=DnsProvider(ref="deviceSettings:dns:privateProviders"),
    )
    state = _profile_state(
        _ds_module(segments=[DeviceSegment(segment=_global_segment(), dns=dns)])
    )
    state = await ConfigPipeline(client=None).apply(
        state,
        module="device_settings",
        segment="Global Segment",
        path="dns",
        patch={"source_interface": "GE1"},
    )
    segment = state.profile.device_settings.segment("Global Segment")
    assert isinstance(segment.dns, SegmentDns)
    assert segment.dns.source_interface == "GE1"


@pytest.mark.asyncio
async def test_apply_updates_a_vlan() -> None:
    network = LanNetwork(name="Corp", vlan_id=100, cidr_ip="10.0.1.0")
    state = _profile_state(_ds_module(lan=LanSettings(networks=[network])))
    state = await ConfigPipeline(client=None).apply(
        state,
        module="device_settings",
        path="lan.networks",
        patch={"100": {"advertise": False}},
    )
    (updated,) = state.profile.device_settings.lan.networks
    assert updated.advertise is False


# --- segments on an edge target ----------------------------------------------
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


async def _resolvers_with_lab() -> ResolverRegistry:
    """Global Segment, Guest and Lab, each with a logical ID of its own."""
    registry = ResolverRegistry()
    registry.register(
        await make_segment_resolver(
            [
                {"name": name, "segment_id": n, "segment_logical_id": uuid.UUID(int=n)}
                for name, n in (("Global Segment", 0), ("Guest", 2), ("Lab", 7))
            ]
        )
    )
    return registry


@pytest.mark.asyncio
async def test_change_of_an_inherited_segment_adds_the_placeholder() -> None:
    state = make_raw_state()
    _ = await ConfigPipeline(client=None).apply(
        state, module="firewall", patch=_guest_rule_patch()
    )
    guest = state.edge.firewall.get_segment(name="Guest")
    assert [r.name for r in guest.outbound] == ["block"]
    (firewall,) = state.plan().modules
    assert (firewall.operation, firewall.reconciled_segments) == ("update", ("Guest",))


@pytest.mark.asyncio
async def test_inherited_segment_by_another_case_and_by_id() -> None:
    for selector in ("guest", "2"):
        state = make_raw_state()
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=_guest_rule_patch(selector)
        )
        assert _edge_segment_names(state) == ["Global Segment", "Guest"]
        guest = state.edge.firewall.get_segment(name="Guest")
        assert guest.segment.segment_id == 2
        assert [r.name for r in guest.outbound] == ["block"]


def _own_guest_state() -> ConfigState:
    """The edge layer has Global Segment and Guest, both its own as read."""
    raw = {
        "segments": [
            {"segment": desc(0, "Global Segment"), "outbound": []},
            {"segment": desc(2, "Guest"), "outbound": []},
        ]
    }
    row = make_module_json(
        "firewall", raw, module_id=1, configuration_id=10, version="e1"
    )
    return make_raw_state(edge_modules=[row])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry",
    [
        pytest.param(move(first=True), id="move"),
        pytest.param({"_position": {"after": "Global Segment"}}, id="move-as-json"),
        pytest.param(
            {"outbound": {"block": add(_DENY)}, "_position": "first"},
            id="change-with-a-place",
        ),
    ],
)
async def test_a_place_for_an_inherited_segment_is_refused(
    entry: dict[str, object],
) -> None:
    state = make_raw_state()
    before = state.plan().render()
    with pytest.raises(PatchError, match="inherits segment 'Guest'") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch={"segments": {"Guest": entry}}
        )
    assert exc.value.path == Path.parse("segments[Guest]")
    assert exc.value.kind == "segment_rule"
    assert exc.value.message == (
        "the edge inherits segment 'Guest' from its profile and had no entry of "
        "its own for it when it was read. A save writes such a segment after the "
        "edge's own, in the profile's order, so it cannot be given a place. For a "
        'move, such as {"_position": "first"} (`move(first=True)`), drop the '
        'entry. For a change, drop "_position" and keep the fields'
    )
    assert _edge_segment_names(state) == ["Global Segment"]  # no placeholder
    assert state.plan().render() == before


@pytest.mark.asyncio
async def test_a_placeholder_an_earlier_patch_added_is_still_inherited() -> None:
    state = make_raw_state()
    pipeline = ConfigPipeline(client=None)
    _ = await pipeline.apply(state, module="firewall", patch=_guest_rule_patch())
    assert _edge_segment_names(state) == ["Global Segment", "Guest"]
    before = state.plan().render()
    for selector in ("Guest", "guest", "2", "#1"):
        with pytest.raises(PatchError, match="inherits segment 'Guest'"):
            _ = await pipeline.apply(
                state,
                module="firewall",
                patch={"segments": {selector: move(first=True)}},
            )
    assert _edge_segment_names(state) == ["Global Segment", "Guest"]
    assert state.plan().render() == before


@pytest.mark.asyncio
async def test_no_segment_of_a_module_the_state_created_takes_a_place() -> None:
    state = make_raw_state(edge_modules=[])
    with pytest.raises(PatchError, match="inherits segment 'Global Segment'"):
        _ = await ConfigPipeline(client=None).apply(
            state,
            module="firewall",
            patch={"segments": {"Global Segment": move(last=True)}},
        )
    assert state.find_module("firewall") is None


@pytest.mark.asyncio
async def test_a_place_before_or_after_an_inherited_segment_is_refused() -> None:
    after_guest: dict[str, object] = {
        "segments": {"Global Segment": move(after="Guest")}
    }
    with_a_change: dict[str, object] = {
        "segments": {
            "Guest": {"outbound": {"block": add(_DENY)}},
            "Global Segment": move(after="Guest"),
        }
    }
    pipeline = ConfigPipeline(client=None)
    for patch in (after_guest, with_a_change):
        state = make_raw_state()
        with pytest.raises(PatchError, match="given by segment 'Guest'") as exc:
            _ = await pipeline.apply(state, module="firewall", patch=patch)
        assert exc.value.path == Path(("segments", Key("Global Segment")))
        assert exc.value.kind == "segment_rule"
        assert _edge_segment_names(state) == ["Global Segment"]

    state = make_raw_state()
    _ = await pipeline.apply(state, module="firewall", patch=_guest_rule_patch())
    with pytest.raises(PatchError) as exc:
        _ = await pipeline.apply(state, module="firewall", patch=after_guest)
    assert exc.value.message == (
        "the place is given by segment 'Guest', which the edge inherits from its "
        "profile and had no entry of its own for when it was read. A save writes "
        "such a segment after the edge's own, in the profile's order, so a place "
        'before or after it cannot be stored. Place the segment "first" or "last" '
        '({"_position": "first"} (`move(first=True)`)), or before or after one of '
        'the edge\'s own segments ({"_position": {"before": "<own segment>"}} '
        '(`move(before="<own segment>")`))'
    )
    assert exc.value.kind == "segment_rule"
    assert _edge_segment_names(state) == ["Global Segment", "Guest"]


@pytest.mark.asyncio
async def test_a_move_among_the_edges_own_segments_is_written() -> None:
    state = _own_guest_state()
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state,
        module="firewall",
        patch={"segments": {"Guest": move(before="Global Segment")}},
    )
    assert _edge_segment_names(state) == ["Guest", "Global Segment"]
    _ = await pipeline.save(state)
    stored = client.store.rows[1]["data"]["segments"]
    assert [entry["segment"]["name"] for entry in stored] == ["Guest", "Global Segment"]


@pytest.mark.asyncio
async def test_once_a_save_wrote_its_entry_the_segment_is_the_edges_own() -> None:
    state = make_raw_state()
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(state, module="firewall", patch=_guest_rule_patch())
    _ = await pipeline.save(state)

    _ = await pipeline.apply(
        state, module="firewall", patch={"segments": {"Guest": move(first=True)}}
    )

    assert _edge_segment_names(state) == ["Guest", "Global Segment"]
    assert [m.name for m in state.plan().changed] == ["firewall"]


@pytest.mark.asyncio
async def test_add_of_an_inherited_segment_raises() -> None:
    state = make_raw_state()
    with pytest.raises(PatchError, match="plain object") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch={"segments": {"Guest": add({})}}
        )
    assert exc.value.path == Path.parse("segments[Guest]")
    assert exc.value.kind == "segment_rule"
    assert _edge_segment_names(state) == ["Global Segment"]


@pytest.mark.asyncio
async def test_plus_add_of_an_inherited_segment_raises() -> None:
    state = make_raw_state()
    patch: dict[str, object] = {"segments": {"+": add({"segment": {"name": "Guest"}})}}
    with pytest.raises(PatchError, match="plain object") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )
    assert exc.value.path == Path(("segments", Key("+")))
    assert exc.value.kind == "segment_rule"
    assert _edge_segment_names(state) == ["Global Segment"]


@pytest.mark.asyncio
@pytest.mark.parametrize("module", ["deviceSettings", "QOS", "firewall"])
async def test_add_of_a_segment_the_profile_lacks_raises(module: str) -> None:
    # An edge has no empty device settings module to create, so all three exist.
    state = _make_state(include_device_settings=True, include_qos=True)
    before = state.plan()
    with pytest.raises(PatchError, match="the edge's profile has no segment") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module=module, patch={"segments": {"Lab": add({})}}
        )
    assert exc.value.path == Path.parse("segments[Lab]")
    assert exc.value.kind == "segment_rule"
    assert '{"segments": {"Lab": {"_op": "add"}}}' in exc.value.message
    assert state.plan() == before
    assert _edge_segment_names(state) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("module", ["QOS", "firewall"])
async def test_refused_add_of_a_segment_the_profile_lacks_creates_no_module(
    module: str,
) -> None:
    state = make_raw_state(edge_modules=[])
    before = state.plan()
    with pytest.raises(PatchError, match="the edge's profile has no segment"):
        _ = await ConfigPipeline(client=None).apply(
            state, module=module, patch={"segments": {"Lab": add({})}}
        )
    assert state.plan() == before
    assert state.plan().modules == ()


@pytest.mark.asyncio
async def test_plus_add_of_a_segment_the_profile_lacks_raises() -> None:
    state = make_raw_state()
    patch: dict[str, object] = {"segments": {"+": add({"segment": {"name": "Lab"}})}}
    with pytest.raises(PatchError, match="the edge's profile has no segment") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )
    assert exc.value.path == Path(("segments", Key("+")))
    assert exc.value.kind == "segment_rule"
    assert _edge_segment_names(state) == ["Global Segment"]


@pytest.mark.asyncio
async def test_add_of_a_segment_the_profile_lacks_raises_with_resolvers_loaded() -> (
    None
):
    # With a resolver that knows Lab, merge_patch would fill its IDs and save it.
    state = make_raw_state()
    state.resolvers = await _resolvers_with_lab()
    before = state.plan()
    with pytest.raises(PatchError, match="the edge's profile has no segment") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch={"segments": {"Lab": add({})}}
        )
    assert exc.value.kind == "segment_rule"
    assert state.plan() == before
    assert _edge_segment_names(state) == ["Global Segment"]


@pytest.mark.asyncio
async def test_plus_add_by_segment_id_of_a_segment_the_profile_lacks_raises() -> None:
    state = make_raw_state()
    state.resolvers = await _resolvers_with_lab()
    before = state.plan()
    patch: dict[str, object] = {"segments": {"+": add({"segment": {"segmentId": 7}})}}
    with pytest.raises(PatchError, match="the edge's profile has no segment") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )
    assert exc.value.path == Path(("segments", Key("+")))
    assert exc.value.kind == "segment_rule"
    assert '{"segments": {"Lab": {"_op": "add"}}}' in exc.value.message
    assert state.plan() == before
    assert _edge_segment_names(state) == ["Global Segment"]


@pytest.mark.parametrize("segment_id", ["7", 7.0])
@pytest.mark.asyncio
async def test_plus_add_by_a_segment_id_merge_patch_reads_as_an_int_raises(
    segment_id: object,
) -> None:
    # merge_patch reads "7" and 7.0 as 7, and the resolver would fill in Lab.
    state = make_raw_state()
    state.resolvers = await _resolvers_with_lab()
    before = state.plan()
    patch: dict[str, object] = {
        "segments": {"+": add({"segment": {"segmentId": segment_id}})}
    }
    with pytest.raises(PatchError, match="the edge's profile has no segment") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )
    assert exc.value.kind == "segment_rule"
    assert '{"segments": {"Lab": {"_op": "add"}}}' in exc.value.message
    assert state.plan() == before
    assert _edge_segment_names(state) == ["Global Segment"]


@pytest.mark.asyncio
async def test_plus_add_by_a_segment_id_string_of_an_inherited_segment_raises() -> None:
    state = make_raw_state()
    state.resolvers = await _resolvers_with_lab()
    before = state.plan()
    patch: dict[str, object] = {"segments": {"+": add({"segment": {"segmentId": "2"}})}}
    with pytest.raises(PatchError, match="by inheritance") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )
    assert exc.value.kind == "segment_rule"
    assert state.plan() == before
    assert _edge_segment_names(state) == ["Global Segment"]


@pytest.mark.asyncio
async def test_plus_add_by_a_segment_id_no_segment_has_is_left_to_merge_patch() -> None:
    # The ID names no segment, so there is no profile patch to advise.
    state = make_raw_state()
    state.resolvers = await _resolvers_with_lab()
    before = state.plan()
    patch: dict[str, object] = {"segments": {"+": add({"segment": {"segmentId": 99}})}}
    with pytest.raises(ResolutionError, match=r"No segment has the value 99\.") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )
    assert not isinstance(exc.value, PatchError)
    assert state.plan() == before
    assert _edge_segment_names(state) == ["Global Segment"]


@pytest.mark.asyncio
async def test_plus_add_by_a_segment_id_nothing_has_needs_a_name_with_no_resolver() -> (
    None
):
    # With nothing to read the ID by, merge_patch asks for the name.
    state = make_raw_state()
    before = state.plan()
    patch: dict[str, object] = {"segments": {"+": add({"segment": {"segmentId": 7}})}}
    with pytest.raises(PatchError, match=r"segment\.name: Field required") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )
    assert exc.value.kind == "invalid_value"
    assert state.plan() == before


@pytest.mark.parametrize("segment_id", [2, "2", 2.0])
@pytest.mark.asyncio
async def test_plus_add_by_segment_id_of_an_inherited_segment_with_no_resolver(
    segment_id: object,
) -> None:
    # The profile's Guest has segmentId 2, which names it with no resolver too.
    state = make_raw_state()
    before = state.plan()
    patch: dict[str, object] = {
        "segments": {"+": add({"segment": {"segmentId": segment_id}})}
    }
    with pytest.raises(PatchError, match="by inheritance") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )
    assert exc.value.kind == "segment_rule"
    assert state.plan() == before


@final
class _SegmentsLoadedOnDemand(SegmentResolver):
    """A segment resolver that, like the services one, answers once loaded."""

    requires_load: ClassVar[bool] = True


class _ClientWithLab(MockVcoClient):
    """A mock whose enterprise also has Guest (2) and Lab (7)."""

    @override
    async def get_enterprise_network_segments(
        self, request: GetEnterpriseNetworkSegments
    ) -> Sequence[NetworkSegment]:
        answered = await super().get_enterprise_network_segments(request)
        return [
            *answered,
            *(
                NetworkSegment.model_validate(network_segment_wire(name, n))
                for name, n in (("Guest", 2), ("Lab", 7))
            ),
        ]


@pytest.mark.asyncio
async def test_plus_add_by_segment_id_loads_the_segment_resolver_it_needs() -> None:
    client = _ClientWithLab()
    state = make_raw_state()
    state.resolvers.register(_SegmentsLoadedOnDemand(client))
    before = state.plan()
    patch: dict[str, object] = {"segments": {"+": add({"segment": {"segmentId": 7}})}}
    with pytest.raises(PatchError, match="the edge's profile has no segment") as exc:
        _ = await ConfigPipeline(client).apply(state, module="firewall", patch=patch)
    assert exc.value.kind == "segment_rule"
    assert '{"segments": {"Lab": {"_op": "add"}}}' in exc.value.message
    assert [c for c, _ in client.calls] == ["get_enterprise_network_segments"]
    assert state.plan() == before


@pytest.mark.asyncio
async def test_plus_add_by_segment_id_of_an_inherited_segment_raises() -> None:
    state = make_raw_state()
    state.resolvers = await _resolvers_with_lab()
    before = state.plan()
    patch: dict[str, object] = {"segments": {"+": add({"segment": {"segmentId": 2}})}}
    with pytest.raises(PatchError, match="plain object") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )
    assert exc.value.path == Path(("segments", Key("+")))
    assert exc.value.kind == "segment_rule"
    assert state.plan() == before
    assert _edge_segment_names(state) == ["Global Segment"]


@pytest.mark.asyncio
async def test_plus_add_by_logical_id_of_a_segment_the_profile_lacks() -> None:
    state = make_raw_state()
    state.resolvers = await _resolvers_with_lab()
    before = state.plan()
    patch: dict[str, object] = {
        "segments": {"+": add({"segment": {"segmentLogicalId": str(uuid.UUID(int=7))}})}
    }
    with pytest.raises(PatchError, match="the edge's profile has no segment") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )
    assert exc.value.kind == "segment_rule"
    assert state.plan() == before


def test_add_on_edge_data_bound_to_no_profile_is_left_to_merge_patch() -> None:
    data = make_firewall_module(mod_id=1, config_id=10).data
    patch: dict[str, object] = {"segments": {"Lab": add({})}}
    prepared = _prepare_segments(data, patch, None, frozenset())
    assert prepared == patch


@pytest.mark.asyncio
async def test_delete_of_an_inherited_segment_raises_and_makes_no_placeholder() -> None:
    state = make_raw_state()
    with pytest.raises(PatchError, match="no entry to delete") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch={"segments": {"Guest": delete()}}
        )
    assert exc.value.path == Path.parse("segments[Guest]")
    assert exc.value.kind == "segment_rule"
    assert _edge_segment_names(state) == ["Global Segment"]


@pytest.mark.asyncio
async def test_segment_named_twice_raises() -> None:
    state = make_raw_state()
    patch: dict[str, object] = {
        "segments": {"Guest": {"outbound": {}}, "guest": {"outbound": {}}}
    }
    with pytest.raises(PatchError, match="Guest") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )
    assert exc.value.kind == "duplicate"
    assert _edge_segment_names(state) == ["Global Segment"]


@pytest.mark.asyncio
async def test_segment_neither_layer_has_is_left_to_merge_patch() -> None:
    patch: dict[str, object] = {"segments": {"Nope": {"outbound": {}}}}
    state = make_raw_state()
    with pytest.raises(PatchError, match="no item has the key") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )
    assert exc.value.path == Path.parse("segments[Nope]")
    assert exc.value.kind == "no_item"
    assert _edge_segment_names(state) == ["Global Segment"]
    state.resolvers = await _segment_resolvers()
    with pytest.raises(ResolutionError, match="Nope"):
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )
    assert _edge_segment_names(state) == ["Global Segment"]


@pytest.mark.asyncio
async def test_failed_apply_leaves_the_plan_as_it_was() -> None:
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
async def test_failed_apply_removes_the_module_it_created() -> None:
    state = make_raw_state(edge_modules=[])
    before = state.plan().render()
    with pytest.raises(PatchError, match="no_such_key"):
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch={"no_such_key": 1}
        )
    assert state.find_module("firewall") is None
    assert state.plan().render() == before


@pytest.mark.asyncio
async def test_good_apply_on_an_edge_with_no_firewall_row_plans_an_insert() -> None:
    state = make_raw_state(edge_modules=[])
    _ = await ConfigPipeline(client=None).apply(
        state, module="firewall", patch={"firewall_enabled": False}
    )
    assert [(m.name, m.operation) for m in state.plan().modules] == [
        ("firewall", "insert")
    ]


@pytest.mark.asyncio
async def test_failed_apply_restores_an_unset_modules_list() -> None:
    state = ConfigState(
        edge_config=EdgeConfiguration(name="Edge Specific Profile", id_=10),
        profile_config=make_raw_state().profile_config,
        target_type="edge",
        resolver_context=ResolverContext(),
    )
    edge_config = state.edge_config
    assert edge_config is not None
    fields_set, dump = set(edge_config.model_fields_set), edge_config.model_dump()
    with pytest.raises(PatchError, match="no_such_key"):
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch={"no_such_key": 1}
        )
    assert edge_config.modules is MISSING
    assert edge_config.model_fields_set == fields_set
    assert edge_config.model_dump() == dump


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_dropping_every_created_module_unsets_the_modules_list(
    order: tuple[int, int],
) -> None:
    state = ConfigState(
        edge_config=EdgeConfiguration(name="Edge Specific Profile", id_=10),
        profile_config=make_raw_state().profile_config,
        target_type="edge",
        resolver_context=ResolverContext(),
    )
    edge_config = state.edge_config
    assert edge_config is not None
    fields_set, dump = set(edge_config.model_fields_set), edge_config.model_dump()
    created = [state.module("firewall", create=True), state.module("QOS", create=True)]

    state.drop_module(created[order[0]])
    assert edge_config.modules is not MISSING
    assert [m is created[order[1]] for m in edge_config.modules] == [True]
    state.drop_module(created[order[1]])

    assert edge_config.modules is MISSING
    assert edge_config.model_fields_set == fields_set
    assert edge_config.model_dump() == dump
    # The record is made again by the next creation.
    state.drop_module(state.module("firewall", create=True))
    assert edge_config.modules is MISSING


def test_drop_module_refuses_a_module_that_is_not_new() -> None:
    state = make_raw_state()
    firewall = state.find_module("firewall")
    assert firewall is not None
    with pytest.raises(ValueError, match="firewall"):
        state.drop_module(firewall)
    assert state.find_module("firewall") is firewall


@pytest.mark.asyncio
async def test_selector_in_another_case_names_the_edge_entry() -> None:
    state = make_raw_state()
    _ = await ConfigPipeline(client=None).apply(
        state, module="firewall", patch={"segments": {"global segment": delete()}}
    )
    assert _edge_segment_names(state) == []


def _renamed_guest_state() -> ConfigState:
    """The edge layer has the profile's Guest (segmentId 2) as ``Guest-old``."""
    raw = {
        "segments": [
            {"segment": desc(0, "Global Segment"), "outbound": []},
            {"segment": desc(2, "Guest-old"), "outbound": []},
        ]
    }
    row = make_module_json("firewall", raw, module_id=1, configuration_id=10)
    return make_raw_state(edge_modules=[row])


@pytest.mark.asyncio
async def test_edge_entry_under_another_name_is_the_profiles_segment() -> None:
    pipeline = ConfigPipeline(client=None)
    state = _renamed_guest_state()
    _ = await pipeline.apply(state, module="firewall", patch=_guest_rule_patch())
    assert _edge_segment_names(state) == ["Global Segment", "Guest-old"]
    renamed = state.edge.firewall.get_segment(name="Guest-old")
    assert [r.name for r in renamed.outbound] == ["block"]

    state = _renamed_guest_state()
    _ = await pipeline.apply(
        state, module="firewall", patch={"segments": {"Guest": delete()}}
    )
    assert _edge_segment_names(state) == ["Global Segment"]

    adds: list[dict[str, object]] = [
        {"segments": {"Guest": add({})}},
        {"segments": {"+": add({"segment": {"name": "Guest"}})}},
    ]
    for patch in adds:
        state = _renamed_guest_state()
        with pytest.raises(PatchError) as exc:
            _ = await pipeline.apply(state, module="firewall", patch=patch)
        assert "Guest-old" in str(exc.value)
        assert "no entry" not in str(exc.value)
        assert "inheritance" not in str(exc.value)
        assert exc.value.kind == "exists"
        assert _edge_segment_names(state) == ["Global Segment", "Guest-old"]


@pytest.mark.asyncio
async def test_an_empty_object_for_an_inherited_segment_adds_no_placeholder() -> None:
    state = make_raw_state()
    before = state.plan().render()
    pipeline = ConfigPipeline(client=None)
    for selector in ("Guest", "guest", "2", "Global Segment"):
        _ = await pipeline.apply(
            state, module="firewall", patch={"segments": {selector: {}}}
        )
        assert _edge_segment_names(state) == ["Global Segment"]
    _ = await pipeline.apply(state, module="firewall", segment="Guest", patch={})
    assert _edge_segment_names(state) == ["Global Segment"]
    assert state.plan().render() == before


@pytest.mark.asyncio
async def test_an_empty_object_counts_as_a_selector_of_its_segment() -> None:
    state = make_raw_state()
    patch: dict[str, object] = {
        "segments": {"Guest": {}, "2": {"outbound": {"block": add(_DENY)}}}
    }
    with pytest.raises(PatchError, match="one selector per segment"):
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )
    assert _edge_segment_names(state) == ["Global Segment"]


@pytest.mark.asyncio
async def test_an_empty_object_for_a_segment_neither_layer_has_raises() -> None:
    state = make_raw_state()
    with pytest.raises(PatchError, match="no item has the key"):
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch={"segments": {"Nope": {}}}
        )


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
        ).for_profile(),
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
@pytest.mark.parametrize("module", ["firewall", "qos", "device_settings"])
async def test_profile_target_refuses_a_segment_delete(module: str) -> None:
    # An add assigns the segment (test_profile_segment_patches.py). A delete of
    # a segment that all three modules have removes it from all three.
    state = _segment_profile_state("Global Segment")
    with pytest.raises(PatchError, match="it removes Global Segment") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module=module, patch={"segments": {"Global Segment": delete()}}
        )
    assert exc.value.path == Path(("segments",))
    assert exc.value.kind == "segment_rule"
    assert "removing it from all three is not supported" in exc.value.message
    assert '{"segments": {"Global Segment": {"_op": "delete"}}}' in exc.value.message
    assert _profile_segment_names(state) == [["Global Segment"]] * 3


@pytest.mark.asyncio
async def test_profile_target_changes_and_moves_a_segment() -> None:
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


_REDESCRIBED = "a patch cannot change which segments a profile has"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "detail"),
    [
        pytest.param(
            {"patch": {"segments": []}},
            "it removes Global Segment, Guest:",
            id="empty-list",
        ),
        pytest.param(
            {"path": "segments", "patch": []},
            "it removes Global Segment, Guest:",
            id="empty-list-by-path",
        ),
        pytest.param(
            {
                "patch": {
                    "segments": [
                        {"segment": desc(2, "Guest"), "outbound": [_BLOCK_SSH]}
                    ]
                }
            },
            "it removes Global Segment:",
            id="one-segment-as-a-list",
        ),
        pytest.param(
            {
                "patch": {
                    "segments": {
                        "Guest": {"segment": {"segment_id": 4, "name": "Voice"}}
                    }
                }
            },
            "it adds Voice with no assignment",
            id="descriptor-rewritten",
        ),
        pytest.param(
            {"patch": {"segments": {"Guest": {"segment": {"segment_id": 7}}}}},
            "it re-describes Guest:",
            id="descriptor-id-changed",
        ),
        pytest.param(
            {"patch": {"segments": {"Guest": {"segment": {"type": "CDE"}}}}},
            "it re-describes Guest:",
            id="descriptor-type-changed",
        ),
        pytest.param(
            {
                "patch": {
                    "segments": {
                        "Guest": {
                            "segment": {
                                "segment_logical_id": (
                                    "00000000-0000-4000-8000-000000000001"
                                )
                            }
                        }
                    }
                }
            },
            "it re-describes Guest:",
            id="descriptor-logical-id-changed",
        ),
    ],
)
async def test_profile_target_keeps_its_segments_whatever_the_patch_form(
    call: dict[str, object], detail: str
) -> None:
    state = _segment_profile_state("Global Segment", "Guest")
    state.take_snapshot()
    before = state.plan().render()
    with pytest.raises(PatchError, match=_REDESCRIBED) as exc:
        _ = await ConfigPipeline(client=None).apply(state, module="firewall", **call)  # pyright: ignore[reportArgumentType]
    assert exc.value.path == Path(("segments",))
    assert exc.value.kind == "segment_rule"
    assert detail in exc.value.message
    assert _profile_segment_names(state) == [["Global Segment", "Guest"]] * 3
    assert state.plan().render() == before


@pytest.mark.asyncio
async def test_profile_target_refuses_a_descriptor_rewritten_into_anothers_id() -> None:
    state = _segment_profile_state("Global Segment", "Guest")
    with pytest.raises(PatchError):
        _ = await ConfigPipeline(client=None).apply(
            state,
            module="firewall",
            patch={"segments": {"Guest": {"segment": {"segment_id": 0}}}},
        )
    assert _profile_segment_names(state) == [["Global Segment", "Guest"]] * 3


@pytest.mark.asyncio
@pytest.mark.parametrize("module", ["firewall", "qos", "device_settings"])
async def test_profile_target_refuses_an_empty_segments_list_in_each_module(
    module: str,
) -> None:
    state = _segment_profile_state("Global Segment", "Guest")
    with pytest.raises(PatchError, match=_REDESCRIBED):
        _ = await ConfigPipeline(client=None).apply(
            state, module=module, patch={"segments": []}
        )
    assert _profile_segment_names(state) == [["Global Segment", "Guest"]] * 3


@pytest.mark.asyncio
async def test_profile_target_takes_a_list_that_keeps_every_descriptor() -> None:
    state = _segment_profile_state("Global Segment", "Guest")
    segments: list[dict[str, object]] = [
        {"segment": desc(2, "Guest"), "outbound": [_BLOCK_SSH]},
        {"segment": desc(0, "Global Segment"), "outbound": []},
    ]
    _ = await ConfigPipeline(client=None).apply(
        state, module="firewall", patch={"segments": segments}
    )
    firewall = state.profile.firewall
    assert [s.segment.name for s in firewall.segments] == ["Guest", "Global Segment"]
    assert [r.name for r in firewall.get_segment(name="Guest").outbound] == [
        "block-ssh"
    ]


@pytest.mark.asyncio
async def test_edge_target_still_takes_a_list_for_segments() -> None:
    state = make_raw_state()
    _ = await ConfigPipeline(client=None).apply(
        state, module="firewall", patch={"segments": []}
    )
    assert _edge_segment_names(state) == []


# --- the address ---------------------------------------------------------------

_BLOCK: dict[str, object] = {"block": add(_DENY)}

# (id, call keywords, the same call nested, whether the call changes the plan)
_ADDRESSED: list[tuple[str, dict[str, object], dict[str, object], bool]] = [
    (
        "path-only",
        {"path": "firewall_enabled", "patch": False},
        {"firewall_enabled": False},
        True,
    ),
    (
        "segment-only",
        {"segment": "Guest", "patch": {"outbound": _BLOCK}},
        {"segments": {"Guest": {"outbound": _BLOCK}}},
        True,
    ),
    (
        "both",
        {"segment": "Guest", "path": "outbound", "patch": _BLOCK},
        {"segments": {"Guest": {"outbound": _BLOCK}}},
        True,
    ),
    (
        "path-through-a-sub-model-that-is-unset",
        {"path": "stateful_firewall_settings.udp_flow_timeout", "patch": 100},
        {"stateful_firewall_settings": {"udp_flow_timeout": 100}},
        True,
    ),
    (
        "parts-given-by-alias",
        {"path": "statefulFirewallSettings.udpFlowTimeout", "patch": 100},
        {"stateful_firewall_settings": {"udp_flow_timeout": 100}},
        True,
    ),
    (
        "none",
        {"path": "network_protection_settings", "patch": None},
        {"network_protection_settings": None},
        False,
    ),
    (
        "scalar",
        {
            "segment": "Global Segment",
            "path": "firewall_logging_enabled",
            "patch": True,
        },
        {"segments": {"Global Segment": {"firewall_logging_enabled": True}}},
        True,
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("addressed", "nested", "changes"),
    [pytest.param(a, n, c, id=i) for i, a, n, c in _ADDRESSED],
)
async def test_addressed_call_equals_its_nested_form(
    addressed: dict[str, object], nested: dict[str, object], *, changes: bool
) -> None:
    pipeline = ConfigPipeline(client=None)
    by_address, by_patch = make_raw_state(), make_raw_state()
    _ = await pipeline.apply(by_address, module="firewall", **addressed)  # pyright: ignore[reportArgumentType]
    _ = await pipeline.apply(by_patch, module="firewall", patch=nested)
    assert by_address.plan().render() == by_patch.plan().render()
    assert bool(by_address.plan().changed) is changes


@pytest.mark.asyncio
async def test_addressed_apply_on_an_edge_with_no_firewall_row_plans_an_insert() -> (
    None
):
    state = make_raw_state(edge_modules=[])
    _ = await ConfigPipeline(client=None).apply(
        state,
        module="firewall",
        segment="Global Segment",
        path="outbound",
        patch=_BLOCK,
    )
    assert [(m.name, m.operation) for m in state.plan().modules] == [
        ("firewall", "insert")
    ]


@pytest.mark.asyncio
async def test_addressed_apply_adds_the_placeholder() -> None:
    state = make_raw_state()
    _ = await ConfigPipeline(client=None).apply(
        state, module="firewall", segment="Guest", path="outbound", patch=_BLOCK
    )
    assert _edge_segment_names(state) == ["Global Segment", "Guest"]


@pytest.mark.asyncio
async def test_bad_address_raises_and_changes_nothing() -> None:
    state = make_raw_state()
    before = state.plan().render()
    pipeline = ConfigPipeline(client=None)
    with pytest.raises(PatchError, match="no field 'nope'") as exc:
        _ = await pipeline.apply(state, module="firewall", path="nope", patch=1)
    assert exc.value.path == Path.parse("nope")
    assert exc.value.kind == "address"
    assert state.plan().render() == before
    assert _edge_segment_names(state) == ["Global Segment"]


@pytest.mark.asyncio
async def test_segment_for_a_module_with_no_segments_raises() -> None:
    wan = make_module_json("WAN", {}, module_id=7, configuration_id=20)
    state = ConfigState(
        edge_config=None,
        profile_config=EnterpriseConfigurationProfile.model_validate(
            {"name": "P", "id": 20, "modules": [wan]}
        ),
        target_type="profile",
        resolver_context=ResolverContext(),
    )
    with pytest.raises(PatchError, match="WanData has no segments field") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="wan", segment="Global Segment", patch={}
        )
    assert exc.value.path == Path(("segments",))


@pytest.mark.asyncio
async def test_bad_address_removes_the_module_it_created() -> None:
    state = make_raw_state(edge_modules=[])
    before = state.plan().render()
    with pytest.raises(PatchError, match="no field 'nope'"):
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", path="nope", patch=1
        )
    assert state.find_module("firewall") is None
    assert state.plan().render() == before


@pytest.mark.asyncio
async def test_non_mapping_patch_without_an_address_raises_type_error() -> None:
    state = make_raw_state()
    with pytest.raises(TypeError):
        _ = await ConfigPipeline(client=None).apply(state, module="firewall", patch=3)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("patch", "field"),
    [({"inbound": []}, "inbound"), ({"inbound_v6": []}, "inbound_v6")],
)
async def test_apply_refuses_a_default_for_a_key_the_edge_layer_lacks(
    patch: dict[str, object], field: str
) -> None:
    state = make_raw_state()
    with pytest.raises(LossyFieldError) as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )
    assert exc.value.fields == [field]
    assert state.plan().changed == ()
    assert field not in state.edge.firewall.model_fields_set
    # The state still takes edits and plans them.
    _ = await ConfigPipeline(client=None).apply(
        state, module="firewall", patch={"firewall_enabled": False}
    )
    assert [m.name for m in state.plan().changed] == ["firewall"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("patch", "paths"),
    [
        ({"firewall_enabled": True}, ["firewall_enabled"]),
        # A group is made local whole, the other members copied from the profile
        # (here the display default: the profile stores neither).
        ({"atp_enabled": False}, ["atp_enabled", "securityFeatures"]),
        (
            {"stateful_firewall_enabled": True},
            [
                "stateful_firewall_enabled",
                "statefulFirewallSettings",
                "statefulFirewallSettingsV6",
            ],
        ),
    ],
)
async def test_apply_of_a_value_for_an_inheritable_key_the_edge_lacks_makes_it_local(
    patch: dict[str, object], paths: list[str]
) -> None:
    state = make_raw_state()
    _ = await ConfigPipeline(client=None).apply(state, module="firewall", patch=patch)
    (plan,) = state.plan().changed
    assert [str(c.path) for c in plan.changes] == paths


@pytest.mark.asyncio
async def test_apply_refuses_an_edit_to_a_section_the_edge_layer_does_not_write() -> (
    None
):
    raw: dict[str, object] = {
        "lan": {"networks": [], "interfaces": []},
        "segments": [{"segment": desc(0, "Global Segment")}],
        "ntp": {"override": True, "enabled": False, "servers": []},
        "snmp": {"port": 161, "snmpv2c": {"enabled": True, "allowedIp": ["10.0.0.9"]}},
    }
    row = make_module_json(
        "deviceSettings", raw, module_id=7, configuration_id=10, version="d1"
    )
    state = make_raw_state(edge_modules=[row])
    pipeline = ConfigPipeline(client=None)
    with pytest.raises(LossyFieldError) as exc:
        _ = await pipeline.apply(
            state, module="device_settings", patch={"ntp": {"enabled": True}}
        )
    assert exc.value.fields == ["ntp"]
    with pytest.raises(LossyFieldError) as exc:
        _ = await pipeline.apply(
            state,
            module="device_settings",
            path="snmp.v2c.allowed_ips",
            patch={"10.0.0.1": add()},
        )
    assert exc.value.fields == ["snmp"]
    assert state.plan().changed == ()


@pytest.mark.asyncio
async def test_a_refused_edit_removes_the_module_apply_created() -> None:
    state = make_raw_state(edge_modules=[])
    with pytest.raises(LossyFieldError) as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch={"inbound": []}
        )
    assert exc.value.fields == ["inbound"]
    assert state.find_module("firewall") is None
    assert state.plan().modules == ()


@pytest.mark.asyncio
async def test_the_same_patches_apply_on_a_profile() -> None:
    state = _segment_profile_state("Global Segment")
    state.take_snapshot()
    _ = await ConfigPipeline(client=None).apply(
        state, module="firewall", patch={"firewall_enabled": False, "atp_enabled": True}
    )
    assert [m.name for m in state.plan().changed] == ["firewall"]


def test_lossy_error_says_why_and_what_to_do() -> None:
    state = make_raw_state()
    state.edge.firewall.inbound = []
    with pytest.raises(LossyFieldError) as exc:
        _ = state.plan()
    assert str(exc.value) == (
        "firewall: a save cannot write these edits: inbound. A save sends "
        "what differs from the module as read, and at these fields nothing would. "
        "Either this library does not write the field at this configuration level "
        "(some sections are written for a profile only, or for an edge only: make "
        "the edit at the level that writes it), or the field was absent as read "
        "and now holds its default, the value it already read as (leave it out, "
        "or set another value)."
    )


def _control_plane_state() -> ConfigState:
    control = make_module_json(
        "controlPlane",
        {"gatewaySelection": {"primary": "gw1", "secondary": "gw2", "mode": "dynamic"}},
        module_id=8,
        configuration_id=20,
    )
    firewall = make_module_json(
        "firewall",
        {"segments": [{"segment": desc(0, "Global Segment"), "outbound": []}]},
        module_id=1,
        configuration_id=20,
    )
    state = ConfigState(
        edge_config=None,
        profile_config=EnterpriseConfigurationProfile.model_validate(
            {"name": "P", "id": 20, "modules": [firewall, control]}
        ),
        target_type="profile",
        resolver_context=ResolverContext(),
    )
    state.take_snapshot()
    return state


@pytest.mark.asyncio
async def test_apply_refuses_a_module_a_save_does_not_write() -> None:
    state = _control_plane_state()
    with pytest.raises(PatchError, match="controlPlane: a save does not write") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state,
            module="control_plane",
            patch={"gateway_selection": {"mode": "static"}},
        )
    assert exc.value.path == Path(())
    assert exc.value.kind == "address"
    assert exc.value.message.endswith(
        "so a patch to it is not applied. Make the change in the VCO"
    )
    assert state.plan().changed == ()
    assert state.profile_config.control_plane.data.gateway_selection.mode == "dynamic"


@pytest.mark.asyncio
async def test_dry_run_refuses_what_the_save_refuses() -> None:
    state = _control_plane_state()
    state.profile_config.control_plane.data.gateway_selection.mode = "static"
    state.profile.firewall.firewall_enabled = False
    assert [m.name for m in state.plan().changed] == ["firewall", "controlPlane"]
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)
    for dry_run in (True, False):
        with pytest.raises(TypeError, match="ControlPlaneConfigurationModule") as exc:
            _ = await pipeline.save(state, dry_run=dry_run)
        assert str(exc.value).startswith("controlPlane: this state has an edit to a")
    assert client.store.requests == []


@pytest.mark.asyncio
async def test_plus_add_of_a_segment_the_edge_has_under_another_name_says_what_to_do() -> (  # noqa: E501 (a test's name)
    None
):
    state = _renamed_guest_state()
    patch: dict[str, object] = {"segments": {"+": add({"segment": {"name": "Guest"}})}}
    with pytest.raises(
        PatchError, match="already in the edge layer, as 'Guest-old'"
    ) as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )
    assert "plain object" in exc.value.message
    assert "Guest-old" in exc.value.message


@pytest.mark.asyncio
async def test_delete_of_an_inherited_segment_says_what_to_do() -> None:
    state = make_raw_state()
    with pytest.raises(PatchError, match="no entry to delete") as exc:
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch={"segments": {"Guest": delete()}}
        )
    assert "leave" in exc.value.message
