"""Live tests of ``ConfigPipeline``: load, apply, save, against a real Orchestrator.

A save sends each changed module's raw data as read plus only the caller's
changes, under the version it was read at; it reconciles the segments an edge
module lacks, inserts a missing edge firewall or QoS module, and refuses a stale
write. These tests prove that on a lab: they edit through the pipeline, save,
read the module back and compare it with what was there, then put everything
back.

Every test that writes does so inside ``restoring`` (see
``_intent_live_support``), which restores the module and checks that it is
restored whether or not the test passes. A test that finds no suitable target
skips. Edits are synthetic and inert: a copy of a rule after the rule it copies,
a QoE threshold, a link description, a LAN network's OSPF cost, a syslog flag.

Server-managed keys seen in what the Orchestrator stores (the only ones allowed):

* ``ruleLogicalId``: assigned to a rule that was added without one.
* ``routeMapsVersion``: added to a deviceSettings segment that lacks it (see
  ``_intent_live_support.SERVER_ADDED_KEYS``).
* refs: the Orchestrator re-derives a deviceSettings module's refs from its data
  when the data changes and drops one whose object is gone; a firewall or WAN
  module's refs stay as they were. The tests never write refs and restore data
  only.

What an edge inherits: ``edge/getEdgeConfigurationModules`` returns the module as
the Orchestrator composes it from the profile's and the edge's. The
reconciliation tests compare it before and after a write.

Requires VCO_HOST and VCO_TOKEN (VCO_ENTERPRISE_ID if the token needs one).

Run:
    uv run --env-file .env pytest -m integration packages/pyvelo-intent/tests/integration
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import pytest
from _intent_live_support import (  # pyright: ignore[reportImplicitRelativeImport]
    EdgeLayout,
    Lab,
    check,
    delete_module,
    describe,
    effective_data,
    plain,
    plain_changes,
    redact,
    restoring,
    synthetic_name,
)
from pydantic import MISSING
from pyvelo.client import ConfigConflictError, VcoClient, VcoOperationError
from pyvelo.client.enums.configuration import ConfigurationModuleName as Name
from pyvelo.client.models.configuration import (
    DeviceSettingsData,
    FirewallData,
    QosData,
    WanData,
)
from pyvelo.client.models.configuration.qos.qoe import QoeSettings
from pyvelo.client.models.configuration.qos.root import ServiceRateLimit
from pyvelo.client.module_write import ModulePlan
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    DeviceSettingsConfigurationModule,
    FirewallConfigurationModule,
    InsertConfigurationModule,
    QosConfigurationModule,
    UpdateConfigurationModule,
    WanConfigurationModule,
)
from pyvelo.core import VcoModel
from pyvelo.core.rebase import Key, Path, RawChange, raw_changes
from pyvelo_intent import ConfigPipeline, ConfigState, StateSpentError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

SERVER_ASSIGNED_KEYS = frozenset({"ruleLogicalId"})
"""Keys the Orchestrator adds to a new item; an added item is compared without them."""


@dataclass
class Writes:
    """The write requests a test sent through the client."""

    updates: list[UpdateConfigurationModule]
    inserts: list[InsertConfigurationModule]


@pytest.fixture
def writes(vco_client: VcoClient, monkeypatch: pytest.MonkeyPatch) -> Writes:
    record = Writes(updates=[], inserts=[])
    update = vco_client.update_configuration_module
    insert = vco_client.insert_configuration_module

    async def spy_update(request: UpdateConfigurationModule) -> None:
        record.updates.append(request)
        await update(request)

    async def spy_insert(request: InsertConfigurationModule) -> Any:
        record.inserts.append(request)
        return await insert(request)

    monkeypatch.setattr(vco_client, "update_configuration_module", spy_update)
    monkeypatch.setattr(vco_client, "insert_configuration_module", spy_insert)
    return record


# ---------------------------------------------------------------------------
# Shared checks
# ---------------------------------------------------------------------------


def _same_value(a: object, b: object) -> bool:
    """Equal, but for the keys the Orchestrator assigns to an item it was given."""

    def bare(value: object) -> object:
        if isinstance(value, dict):
            return {
                k: v
                for k, v in value.items()  # pyright: ignore[reportUnknownVariableType]
                if k not in SERVER_ASSIGNED_KEYS
            }
        return value

    return bare(a) == bare(b)


def _check_paths(
    label: str, what: str, changes: Sequence[RawChange], expected: Sequence[Path]
) -> None:
    got = sorted(str(c.path) for c in changes)
    want = sorted(str(p) for p in expected)
    check(
        got == want,
        f"{label}: {what} lists {describe(changes)}; expected "
        f"{', '.join(redact(p) for p in expected)}",
    )


def _check_stored_as_sent(
    label: str, stored: Sequence[RawChange], plan: ModulePlan
) -> None:
    sent = {str(c.path): c for c in plan.changes}
    for change in stored:
        planned = sent.get(str(change.path))
        check(
            planned is not None
            and _same_value(change.before, planned.before)
            and _same_value(change.after, planned.after),
            f"{label}: {redact(change.path)} was stored other than as sent",
        )


def _planned(state: ConfigState, name: Name) -> ModulePlan:
    return next(m for m in state.plan().modules if m.name == name.value)


def _json_object(value: object) -> dict[str, Any]:
    """A deep copy of a JSON object, for a test to read or change freely."""
    assert isinstance(value, dict)
    return copy.deepcopy(value)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]


def _raw(module: AnyConfigurationModule) -> dict[str, Any]:
    return _json_object(module.raw_data)


# ---------------------------------------------------------------------------
# 1. No edit, nothing planned, nothing sent
# ---------------------------------------------------------------------------


def _unedited(where: str, state: ConfigState, failures: list[str]) -> int:
    config = state.target_config
    modules = [] if config.modules is MISSING else config.modules
    plan = state.plan()
    for module, module_plan in zip(modules, plan.modules, strict=True):
        if module_plan.operation != "none" or module_plan.changes:
            failures.append(
                f"{where} {module.name}: planned {module_plan.operation} "
                f"({describe(module_plan.changes)})"
            )
        elif module_plan.data != module.raw_data:
            failures.append(f"{where} {module.name}: plan.data differs from raw_data")
    return len(modules)


async def test_no_edit_plans_nothing_and_sends_nothing(
    lab: Lab, pipeline: ConfigPipeline, writes: Writes
) -> None:
    failures: list[str] = []
    checked = 0
    for layout in lab.layouts:
        state = await pipeline.load(layout.edge_id, enterprise_id=lab.enterprise_id)
        checked += _unedited(f"edge #{layout.index}", state, failures)
        result = await pipeline.save(state)
        if result.modules_updated or result.modules_inserted or state.spent:
            failures.append(f"edge #{layout.index}: save with no edit wrote")
    for position, profile_id in enumerate(lab.profile_ids):
        state = await pipeline.load_profile(profile_id, enterprise_id=lab.enterprise_id)
        checked += _unedited(f"profile #{position}", state, failures)
        result = await pipeline.save(state)
        if result.modules_updated or result.modules_inserted or state.spent:
            failures.append(f"profile #{position}: save with no edit wrote")
    if not checked:
        pytest.skip("the Orchestrator has no edge or profile configuration")
    if writes.updates or writes.inserts:
        failures.append(
            f"{len(writes.updates)} update(s) and {len(writes.inserts)} insert(s) were sent"
        )
    check(
        not failures,
        f"{len(failures)} problem(s) across {checked} modules with no edit:\n"
        + "\n".join(failures),
    )


# ---------------------------------------------------------------------------
# 4. Profile level: load_profile, apply / apply_aspect, save
# ---------------------------------------------------------------------------


def _profile_rule_source(module: AnyConfigurationModule) -> bool:
    return isinstance(module, FirewallConfigurationModule) and any(
        s.outbound for s in module.data.segments
    )


async def test_profile_rule_added_through_an_aspect_reads_back_as_that_rule_only(
    lab: Lab, pipeline: ConfigPipeline
) -> None:
    profile_id = await lab.pick_writable_profile(
        Name.FIREWALL, _profile_rule_source, "with a rule to copy"
    )
    site = lab.profile_site(profile_id, Name.FIREWALL)
    async with restoring(site) as original:
        assert original is not None
        state = await pipeline.load_profile(profile_id, enterprise_id=lab.enterprise_id)
        module = state.module("firewall")
        assert isinstance(module, FirewallConfigurationModule)
        segment = next(s.segment.name for s in module.data.segments if s.outbound)
        rules = pipeline.read_aspect(state, "firewall_rules", segment=segment)
        assert isinstance(rules, list) and rules
        name = synthetic_name()
        # A copy of the last rule, after it: the rule it copies matches first.
        added: dict[str, Any] = {**rules[-1], "name": name}
        added.pop("rule_logical_id", None)
        _ = await pipeline.apply_aspect(
            state, "firewall_rules", {name: added}, segment=segment
        )

        expected = [Path(("segments", Key(segment), "outbound", Key(name)))]
        plan = _planned(state, Name.FIREWALL)
        check(plan.operation == "update", f"planned {plan.operation}, expected update")
        _check_paths("aspect edit", "the plan", plan.changes, expected)

        result = await pipeline.save(state)
        check(
            result.modules_updated == [Name.FIREWALL] and not result.modules_inserted,
            f"saved {result.modules_updated} / inserted {result.modules_inserted}",
        )
        check(state.spent, "the state is not spent after a save that wrote")

        after = await site.read_present()
        stored = plain_changes(original.data, after.raw_data, FirewallData)
        _check_paths("aspect edit", "the profile read back", stored, expected)
        _check_stored_as_sent("aspect edit", stored, plan)


def _profile_has_qoe(module: AnyConfigurationModule) -> bool:
    return isinstance(module, QosConfigurationModule) and isinstance(
        module.data.qoe, QoeSettings
    )


async def test_profile_scalar_edit_through_apply_reads_back_as_that_edit_only(
    lab: Lab, pipeline: ConfigPipeline
) -> None:
    profile_id = await lab.pick_writable_profile(
        Name.QOS, _profile_has_qoe, "with QoE thresholds"
    )
    site = lab.profile_site(profile_id, Name.QOS)
    async with restoring(site) as original:
        assert original is not None
        state = await pipeline.load_profile(profile_id, enterprise_id=lab.enterprise_id)
        module = state.module("QOS")
        assert isinstance(module, QosConfigurationModule)
        assert isinstance(module.data.qoe, QoeSettings)
        yellow = module.data.qoe.voice.latency.yellow  # a reporting threshold
        _ = await pipeline.apply(
            state,
            module="QOS",
            patch={"qoe": {"voice": {"latency": {"yellow": yellow + 1}}}},
        )

        expected = [Path(("qoe", "voice", "latency", "yellow"))]
        plan = _planned(state, Name.QOS)
        _check_paths("profile edit", "the plan", plan.changes, expected)
        result = await pipeline.save(state)
        check(result.modules_updated == [Name.QOS], f"saved {result.modules_updated}")

        after = await site.read_present()
        stored = plain_changes(original.data, after.raw_data, QosData)
        _check_paths("profile edit", "the profile read back", stored, expected)
        _check_stored_as_sent("profile edit", stored, plan)


# ---------------------------------------------------------------------------
# 5. Reconciliation of the segments an edge module lacks
# ---------------------------------------------------------------------------


def _segment_id(entry: object) -> object:
    if isinstance(entry, dict):
        descriptor = entry.get("segment")  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
        if isinstance(descriptor, dict):
            return descriptor.get("segmentId")  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
    return None


def _lacking(edge: dict[str, Any], profile: dict[str, Any]) -> list[dict[str, Any]]:
    """The profile's segment entries that the edge's data has no entry for."""
    have = [_segment_id(s) for s in edge.get("segments") or []]
    return [s for s in profile.get("segments") or [] if _segment_id(s) not in have]


@dataclass(frozen=True)
class Reconcile:
    id: str
    name: Name
    model: type[VcoModel]
    lists: tuple[str, ...]
    """The lists a placeholder segment entry has, empty."""
    edit: Callable[
        [ConfigPipeline, ConfigState, dict[str, Any]],
        Awaitable[tuple[list[Path], tuple[str, ...]]],
    ]
    """Makes an unrelated edit through the pipeline, given the edge's raw data.
    Returns the paths it changes, and where the effective configuration may
    change because of it."""


async def _syslog_edit(
    pipeline: ConfigPipeline, state: ConfigState, raw: dict[str, Any]
) -> tuple[list[Path], tuple[str, ...]]:
    current = raw.get("syslog_forwarding")  # forward logs; none are collected
    flag = not current if isinstance(current, bool) else True
    _ = await pipeline.apply(
        state, module="firewall", patch={"syslog_forwarding": flag}
    )
    return [Path(("syslog_forwarding",))], ("syslog_forwarding",)


async def _cost_edit(
    _: ConfigPipeline, state: ConfigState, raw: dict[str, Any]
) -> tuple[list[Path], tuple[str, ...]]:
    vlan = raw["lan"]["networks"][0]["vlanId"]
    # Assigned on the typed data, so that no override flag is set along with it.
    network = next(
        n for n in state.edge.device_settings.lan.networks if n.vlan_id == vlan
    )
    network.cost += 1  # OSPF cost
    return [Path(("lan", "networks", Key(vlan), "cost"))], ("lan", "networks")


FIREWALL_CASE = Reconcile(
    "firewall", Name.FIREWALL, FirewallData, ("outbound",), _syslog_edit
)
DEVICE_SETTINGS_CASE = Reconcile(
    "device-settings", Name.DEVICE_SETTINGS, DeviceSettingsData, (), _cost_edit
)


async def _lacking_layout(lab: Lab, case: Reconcile, *, writable: bool) -> EdgeLayout:
    """An edge whose *case* module lacks a segment its profile has, or skip."""
    candidates: list[EdgeLayout] = []
    for layout in lab.layouts:
        edge_module = await lab.edge_site(layout, case.name).read()
        profile_module = await lab.profile_site(layout.profile_id, case.name).read()
        if edge_module is None or profile_module is None:
            continue
        if _lacking(_raw(edge_module), _raw(profile_module)):
            candidates.append(layout)
    if not candidates:
        pytest.skip(f"no edge {case.name.value} module lacks a segment its profile has")
    if not writable:
        return candidates[0]
    layout = await lab.first_writable(candidates, case.name)
    if layout is None:
        pytest.skip(
            f"no edge {case.name.value} module that lacks a segment takes a write"
        )
    return layout


async def _reconcile(
    lab: Lab, pipeline: ConfigPipeline, case: Reconcile, layout: EdgeLayout
) -> None:
    """An unrelated edit to an edge module that lacks profile segments.

    The write must carry the edit and one empty placeholder per missing segment,
    nothing else; the profile must not change; and what the edge inherits, as the
    Orchestrator composes it, must change only by the edit.
    """
    site = lab.edge_site(layout, case.name)
    profile_site = lab.profile_site(layout.profile_id, case.name)
    async with restoring(site) as original:
        assert original is not None
        before = _json_object(plain(original.data))
        profile_before = _raw(await profile_site.read_present())
        lacking = _lacking(before, profile_before)
        effective_before = await effective_data(
            lab.client, layout.edge_id, case.name, lab.enterprise_id
        )

        state = await pipeline.load(layout.edge_id, enterprise_id=lab.enterprise_id)
        expected, allowed = await case.edit(pipeline, state, before)
        plan = _planned(state, case.name)
        check(plan.operation == "update", f"planned {plan.operation}, expected update")
        check(
            len(plan.reconciled_segments) == len(lacking),
            f"the plan reconciles {len(plan.reconciled_segments)} segment(s); "
            f"the edge lacks {len(lacking)}",
        )
        _check_paths("edit", "the plan", plan.changes, expected)
        _ = await pipeline.save(state)

        after = _json_object(plain((await site.read_present()).raw_data))
        stored = raw_changes(before, after, case.model)
        outside = [c for c in stored if c.path.parts[0] != "segments"]
        _check_paths(
            "edit", "the module read back, outside segments", outside, expected
        )
        _check_stored_as_sent("edit", outside, plan)

        placeholders = [
            {"segment": copy.deepcopy(s["segment"]), **{k: [] for k in case.lists}}
            for s in lacking
        ]
        check(
            after.get("segments") == [*(before.get("segments") or []), *placeholders],
            "the segments read back are not the edge's own followed by one empty "
            "placeholder per segment it lacked",
        )
        check(
            _raw(await profile_site.read_present()) == profile_before,
            "the profile's module changed with an edge write",
        )

        # What the edge inherits: only the edit may show, never the placeholders.
        effective_after = await effective_data(
            lab.client, layout.edge_id, case.name, lab.enterprise_id
        )
        check(
            effective_after.get("segments") == effective_before.get("segments"),
            "the placeholders changed the effective segments of the edge",
        )
        moved = [
            c
            for c in raw_changes(effective_before, effective_after)
            if c.path.parts[0] != "segments" and c.path.parts[: len(allowed)] != allowed
        ]
        check(
            not moved,
            f"the effective configuration changed beyond the edit: {describe(moved)}",
        )


async def test_unrelated_edit_adds_the_missing_segments_as_empty_placeholders(
    lab: Lab, pipeline: ConfigPipeline
) -> None:
    layout = await _lacking_layout(lab, FIREWALL_CASE, writable=True)
    await _reconcile(lab, pipeline, FIREWALL_CASE, layout)


class PlaceholderRejected(AssertionError):
    """The Orchestrator refused a deviceSettings segment entry that has only a descriptor."""


@pytest.mark.xfail(
    strict=True,
    raises=PlaceholderRejected,
    reason=(
        "a deviceSettings segment entry with only the segment descriptor is "
        "refused by the Orchestrator, which wants routes.static in each entry"
    ),
)
async def test_missing_device_settings_segment_is_added_as_a_placeholder_the_orchestrator_accepts(
    lab: Lab, pipeline: ConfigPipeline
) -> None:
    layout = await _lacking_layout(lab, DEVICE_SETTINGS_CASE, writable=False)
    try:
        await _reconcile(lab, pipeline, DEVICE_SETTINGS_CASE, layout)
    except VcoOperationError as exc:
        if "static route is missing" in str(exc):
            raise PlaceholderRejected from exc
        pytest.skip("the module's data is refused for a reason of its own")


# ---------------------------------------------------------------------------
# 6. Insert a missing edge module, then delete it
# ---------------------------------------------------------------------------


def _placeholders(
    profile: dict[str, Any], lists: tuple[str, ...]
) -> list[dict[str, Any]]:
    return [
        {"segment": copy.deepcopy(s["segment"]), **{k: [] for k in lists}}
        for s in profile.get("segments") or []
    ]


async def test_missing_edge_qos_is_inserted_from_an_empty_layer_and_reads_back_as_sent(
    lab: Lab, pipeline: ConfigPipeline, writes: Writes
) -> None:
    layout = next((e for e in lab.layouts if not e.has(Name.QOS)), None)
    if layout is None:
        pytest.skip("every edge already has a QoS module of its own")
    site = lab.edge_site(layout, Name.QOS)
    async with restoring(site) as original:
        assert original is None
        profile = _raw(
            await lab.profile_site(layout.profile_id, Name.QOS).read_present()
        )
        state = await pipeline.load(layout.edge_id, enterprise_id=lab.enterprise_id)
        # A disabled service rate limit: new content that limits nothing.
        state.edge.qos.service_rate_limit = ServiceRateLimit.model_validate(
            {"enabled": False, "inputType": "percent", "value": 1}
        )
        plan = _planned(state, Name.QOS)
        check(plan.operation == "insert", f"planned {plan.operation}, expected insert")

        result = await pipeline.save(state)
        check(
            result.modules_inserted == [Name.QOS] and not result.modules_updated,
            f"inserted {result.modules_inserted}, updated {result.modules_updated}",
        )
        check(
            len(writes.inserts) == 1 and not writes.updates, "expected one insert only"
        )

        stored = await site.read_present()
        check(
            stored.raw_data == plan.data,
            "the inserted module does not read back as it was sent: "
            + describe(raw_changes(plan.data, stored.raw_data)),
        )
        check(
            _raw(stored).get("segments") == _placeholders(profile, ("rules",)),
            "the inserted module's segments are not one empty placeholder per "
            "profile segment",
        )

        await delete_module(site, stored.id_)
        check(await site.read() is None, "the inserted module is still there")


def _has_key(value: object, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_has_key(v, key) for v in value.values())  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    if isinstance(value, list):
        return any(_has_key(v, key) for v in value)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return False


def _firewall_that_comes_back_whole(module: AnyConfigurationModule) -> bool:
    """A firewall module that can be deleted and inserted again as it was.

    An insert takes no refs, and the Orchestrator issues new rule IDs for the
    rules it is given, so a module with either would not be the same module.
    """
    return (
        isinstance(module, FirewallConfigurationModule)
        and not (isinstance(module.raw_refs, dict) and module.raw_refs)
        and not _has_key(module.raw_data, "ruleLogicalId")
    )


async def test_edge_firewall_deleted_and_inserted_again_reads_back_as_sent(
    lab: Lab, pipeline: ConfigPipeline, writes: Writes
) -> None:
    layout = await lab.pick_writable_edge(
        Name.FIREWALL,
        _firewall_that_comes_back_whole,
        "with no refs and no rule IDs, which an insert would not give back",
    )
    site = lab.edge_site(layout, Name.FIREWALL)
    async with restoring(site) as original:
        assert original is not None
        profile = _raw(
            await lab.profile_site(layout.profile_id, Name.FIREWALL).read_present()
        )
        await delete_module(site, (await site.read_present()).id_)
        check(await site.read() is None, "the firewall module was not deleted")
        writes.updates.clear()

        state = await pipeline.load(layout.edge_id, enterprise_id=lab.enterprise_id)
        state.edge.firewall.syslog_forwarding = True  # forward logs; none are collected
        plan = _planned(state, Name.FIREWALL)
        check(plan.operation == "insert", f"planned {plan.operation}, expected insert")
        result = await pipeline.save(state)
        check(
            result.modules_inserted == [Name.FIREWALL] and not result.modules_updated,
            f"inserted {result.modules_inserted}, updated {result.modules_updated}",
        )
        check(
            len(writes.inserts) == 1 and not writes.updates, "expected one insert only"
        )

        stored = await site.read_present()
        check(
            stored.raw_data == plan.data,
            "the inserted module does not read back as it was sent: "
            + describe(raw_changes(plan.data, stored.raw_data)),
        )
        check(
            _raw(stored).get("segments") == _placeholders(profile, ("outbound",)),
            "the inserted module's segments are not one empty placeholder per "
            "profile segment",
        )

        await delete_module(site, stored.id_)
        check(await site.read() is None, "the inserted module is still there")
    # Leaving the block inserted the original module again, and checked it.


# ---------------------------------------------------------------------------
# 7. A stale version through the pipeline
# ---------------------------------------------------------------------------


def _wan_link(module: AnyConfigurationModule) -> bool:
    return isinstance(module, WanConfigurationModule) and bool(module.data.links)


async def test_second_save_from_an_older_state_is_refused_and_spends_the_state(
    lab: Lab, pipeline: ConfigPipeline
) -> None:
    layout = await lab.pick_writable_edge(Name.WAN, _wan_link, "with a link")
    site = lab.edge_site(layout, Name.WAN)
    async with restoring(site) as original:
        assert original is not None
        newer = await pipeline.load(layout.edge_id, enterprise_id=lab.enterprise_id)
        older = await pipeline.load(layout.edge_id, enterprise_id=lab.enterprise_id)
        wan = newer.module("WAN")
        assert isinstance(wan, WanConfigurationModule) and isinstance(
            wan.data.links, list
        )
        link = wan.data.links[0].name
        kept, refused = synthetic_name(), synthetic_name()
        for state, text in ((newer, kept), (older, refused)):
            _ = await pipeline.apply(
                state, module="WAN", patch={"links": {link: {"description": text}}}
            )

        _ = await pipeline.save(newer)
        check(newer.spent, "the state that saved is not spent")
        with pytest.raises(ConfigConflictError):
            _ = await pipeline.save(older)

        after = await site.read_present()
        stored = plain_changes(original.data, after.raw_data, WanData)
        _check_paths(
            "stale save",
            "the module read back",
            stored,
            [Path(("links", Key(link), "description"))],
        )
        check(
            stored[0].after == kept,
            "the module holds something other than the first state's edit",
        )

        check(older.spent, "the state whose write was refused is not spent")
        with pytest.raises(StateSpentError):
            _ = await pipeline.apply(
                older, module="WAN", patch={"links": {link: {"description": "x"}}}
            )
        with pytest.raises(StateSpentError):
            _ = await pipeline.save(older)


# ---------------------------------------------------------------------------
# 8. Refs
# ---------------------------------------------------------------------------


def _device_settings_with_refs(module: AnyConfigurationModule) -> bool:
    return (
        isinstance(module, DeviceSettingsConfigurationModule)
        and isinstance(module.raw_refs, dict)
        and bool(module.raw_refs)
        and bool(module.data.lan.networks)
    )


async def test_save_of_a_data_edit_sends_no_refs(
    lab: Lab, pipeline: ConfigPipeline, writes: Writes
) -> None:
    layout = await lab.pick_writable_edge(
        Name.DEVICE_SETTINGS, _device_settings_with_refs, "with refs"
    )
    site = lab.edge_site(layout, Name.DEVICE_SETTINGS)
    async with restoring(site) as original:
        assert original is not None
        writes.updates.clear()  # the preflight write was not the save under test
        state = await pipeline.load(layout.edge_id, enterprise_id=lab.enterprise_id)
        _ = await _cost_edit(pipeline, state, _json_object(original.data))
        check(
            _planned(state, Name.DEVICE_SETTINGS).refs is None,
            "the plan carries refs for a data edit",
        )
        _ = await pipeline.save(state)
        check(
            len(writes.updates) == 1, f"expected one write, saw {len(writes.updates)}"
        )
        check(
            writes.updates[0].update.refs is MISSING,
            "a data edit sent refs along with the data",
        )


# ---------------------------------------------------------------------------
# 9. A segment key in another case edits the segment it names
# ---------------------------------------------------------------------------


def _firewall_segment_with_letters(module: AnyConfigurationModule) -> bool:
    return isinstance(module, FirewallConfigurationModule) and any(
        s.segment.name.swapcase() != s.segment.name for s in module.data.segments
    )


async def test_segment_named_in_another_case_is_edited_in_place_not_duplicated(
    lab: Lab, pipeline: ConfigPipeline
) -> None:
    layout = await lab.pick_writable_edge(
        Name.FIREWALL,
        _firewall_segment_with_letters,
        "with a segment name that has letters",
    )
    site = lab.edge_site(layout, Name.FIREWALL)
    async with restoring(site) as original:
        assert original is not None
        before = _json_object(plain(original.data))
        entry = next(
            s
            for s in before["segments"]
            if s["segment"]["name"].swapcase() != s["segment"]["name"]
        )
        name = entry["segment"]["name"]
        current = entry.get("firewall_logging_enabled")
        flag = not current if isinstance(current, bool) else True

        state = await pipeline.load(layout.edge_id, enterprise_id=lab.enterprise_id)
        _ = await pipeline.apply(
            state,
            module="firewall",
            patch={"segments": {name.swapcase(): {"firewall_logging_enabled": flag}}},
        )
        plan = _planned(state, Name.FIREWALL)
        segment = Path(("segments", Key(name)))
        expected = [segment.child("firewall_logging_enabled")]
        if entry.get("outboundOverride") is not True:  # an edit to a sibling sets it
            expected.append(segment.child("outboundOverride"))
        _check_paths("edit by another case", "the plan", plan.changes, expected)
        _ = await pipeline.save(state)

        after = _json_object(plain((await site.read_present()).raw_data))
        stored = raw_changes(before, after, FirewallData)
        _check_paths("edit by another case", "the module read back", stored, expected)
        check(
            len(after["segments"]) == len(before["segments"]),
            "a segment was added: the other-case key did not find the existing one",
        )
