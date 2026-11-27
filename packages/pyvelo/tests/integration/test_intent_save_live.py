"""Live tests of ``ConfigPipeline``: load, apply, save, against a real Orchestrator.

A save sends each changed module's raw data as read plus only the caller's
changes, under the version it was read at; it reconciles the segments an edge
module lacks, inserts a missing edge firewall or QoS module, and refuses a stale
write. The state continues after a save, from the module the Orchestrator
returns: a second edit saves without a reload, and a refused save leaves the
state holding the module as stored, to redo the lost edits on. These tests
prove that on a lab: they edit through the pipeline, save, read the module back
and compare it with what was there, then put everything back.

Every test that writes does so inside ``restoring`` (see
``live_support``), which restores the module and checks that it is
restored whether or not the test passes. A test that finds no suitable target
skips. Edits are synthetic and inert: a copy of a rule after the rule it copies,
a QoE threshold, a link description, a LAN network's OSPF cost, a syslog flag.
A write error is reported by its class and redacted paths (``outcome``): its
own message names lab entities and shows their values.

Server-managed keys seen in what the Orchestrator stores (the only ones allowed;
``test_server_changes_hold_only_known_rewrites`` fails on any other, naming its
path, to be added here and to the test's known list, or to ``MODULE_REWRITES``
for a value stored otherwise at a planned path):

* ``ruleLogicalId``: assigned to a rule that was added without one.
* ``routeMapsVersion``: added to a deviceSettings segment entry that lacks it
  (accepted drift, see ``live_support.settle``).
* refs: the Orchestrator re-derives a deviceSettings module's refs from its data
  when the data changes and drops one whose object is gone; a firewall or WAN
  module's refs stay as they were. A deviceSettings or QoS write that has
  segments carries its refs as read (the plan derives them); the tests edit no
  owner of a ref row and restore data only.

What an edge inherits: ``edge/getEdgeConfigurationModules`` returns the module as
the Orchestrator composes it from the profile's and the edge's. The
reconciliation tests compare it before and after a write.

How the Orchestrator refuses a stale write is recorded by the run, not assumed.
Each test that makes one prints a ``refused with`` line naming the form: the
precondition error (its conditional check on), or the module row as stored, or
a row count of zero (the check off, ``returnData`` honoured or ignored). With
the check off and the module row returned, a stale save that edits a value the
other writer also changed raises ``WriteNotAppliedError`` (a clash); with it
on, ``ConfigConflictError``. A VCO that ignores ``returnData`` answers
``rows: 0`` instead, which is a stated refusal (``ConfigConflictError``).
Seen on the lab (first write-back run, 2026-11-22): the module row as stored,
so the check is off and ``returnData`` is honoured; a stale save of the same
value raised ``WriteNotAppliedError``, of another value ``ConfigConflictError``.
Four tests record what no assertion can know in advance: the level a module
of an edge comes back at after a refused save
(``test_refused_edge_save_refreshes_the_state``), what the Orchestrator stores
for an IPv6 key sent in upper case (``test_an_ipv6_key_in_upper_case_is_recorded``,
which needs a marked edge with an IPv6 static route of prefix 48 or longer to
copy), the read-back of an insert answered with an ID alone
(``test_an_insert_answered_with_an_id_is_read_back``), and the shape of the ref
rows the Orchestrator returns after a save that sends refs
(``test_record_the_refs_a_write_returns``, which prints field names and counts
and no value).

The cases of section 14 (derived refs) write to a marked profile's deviceSettings
module. The two that set a DNS provider need a marked profile whose Global
Segment has a DNS section and no primary provider set, and a network service of
type ``dns`` in the enterprise; each skips, saying which, where one is missing.

The cases of section 15 assign a segment of the enterprise to a marked profile
that lacks it in all three of its modules (device settings, QoS and firewall),
and restore the three, QoS and firewall first. The first reads the three back and
counts the writes and the reads of a version; the second, on a profile that is
also marked ``pyvelo-it-with-edges``, records whether the QoS and firewall
writes are sent with the version read before the save. Each skips where no
marked profile lacks a segment of the enterprise.

The cases of section 16 (hub roles) make an Edge or a hub cluster a hub of a
marked profile's segment, and remove it again. They need PYVELO_IT_HUB, the
logical ID of an Edge or a cluster of the enterprise that is no hub of the
segment they use, and skip without it. The first adds the hub to the segment's
Branch-to-Hub and backhaul lists (two role steps and one device settings
write) and removes it (one disable and one write); the second, with the hub
saved, adds it to a copy of a QoS rule of the segment and removes the rule and
the hub. A role call is not undone by the restore of a module, so each case
removes the hub in a ``finally`` as well: it sends the disable steps, and a hub
whose roles are set while its lists lack it (a save that stopped after its
steps) is added to the lists first so that the removal has steps to derive.
Each skips where no marked profile has a segment that lacks the hub (and, for
the second case, has a QoS rule in it).

Requires VCO_HOST and VCO_TOKEN (VCO_ENTERPRISE_ID if the token needs one), and
to write, PYVELO_IT_WRITE_HOST and PYVELO_IT_WRITE_ENTERPRISE_ID, and for the
hub cases PYVELO_IT_HUB: see ``live_support``.

Run:
    uv run --env-file .env pytest -m integration \
        packages/pyvelo/tests/integration/test_intent_save_live.py
"""

from __future__ import annotations

import copy
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

import pytest
from live_support import (
    SEGMENT_DRIFT_KEY,
    SERVER_ASSIGNED_KEYS,
    TARGET_MARK,
    WITH_EDGES_MARK,
    Answer,
    DataRefusedError,
    EdgeLayout,
    HubTarget,
    Lab,
    Site,
    admit_hub,
    changes_between,
    check,
    check_as_read,
    check_entries,
    check_no_override_flag,
    check_paths,
    delete_module,
    describe,
    effective_data,
    expect,
    failure_text,
    has_key,
    outcome,
    own_data_refusal,
    read_backs,
    record,
    record_answers,
    record_refusal,
    redact,
    ref_names,
    refusal_codes,
    refusal_form,
    require_writes,
    restoring,
    run_of,
    same_value,
    settle,
    synthetic_name,
    value_at,
    where,
    without_return_data,
    write_data,
)
from pydantic import MISSING
from pyvelo.client import (
    ConfigConflictError,
    ModuleWriteError,
    VcoClient,
    VcoOperationError,
    WriteNotAppliedError,
)
from pyvelo.client.derived_refs import APPEND_ONLY_REF_NAMES, PROCESSOR_REF_NAMES
from pyvelo.client.enums.configuration import ConfigurationModuleName as Name
from pyvelo.client.hub_services import BACKHAUL_REF, CLUSTER_HUB_REF, EDGE_HUB_REF
from pyvelo.client.models.configuration import (
    DeviceSettingsData,
    FirewallData,
    QosData,
    WanData,
)
from pyvelo.client.models.configuration.qos.qoe import QoeSettings
from pyvelo.client.models.configuration.qos.root import ServiceRateLimit
from pyvelo.client.models.configuration.ref_owners import declared_ref_names
from pyvelo.client.schemas.configuration import (
    AnyStoredModule,
    DeviceSettingsConfigurationModule,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
    InsertConfigurationModule,
    QosConfigurationModule,
    UpdateConfigurationModule,
    WanConfigurationModule,
)
from pyvelo.client.schemas.enterprise import (
    EnterpriseServiceSummary,
    GetEnterpriseServices,
)
from pyvelo.config import (
    ConfigPipeline,
    ConfigState,
    PatchError,
    SaveResult,
    add,
    delete,
)
from pyvelo.config.resolvers.segment import SegmentResolver
from pyvelo.core import ConfigContext, VcoModel
from pyvelo.core.rebase import Key, Path, RawChange, raw_changes
from pyvelo.core.response_check import (
    PathPattern,
    ServerChange,
    matches,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence

    from pyvelo.client.models.configuration.device_settings.segment import StaticRoute
    from pyvelo.client.module_write import ModulePlan
    from pyvelo.client.schemas.edge import GetEdgeConfigurationStack
    from pyvelo.core.identity import SegmentIdentity

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@dataclass
class Writes:
    """The write requests a test sent through the client."""

    updates: list[UpdateConfigurationModule]
    inserts: list[InsertConfigurationModule]


class WriteRefusedError(AssertionError):
    """A test that must send nothing tried to write."""


@pytest.fixture
def refused_writes(vco_client: VcoClient, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Refuse every write to the client, and list the attempts: nothing is sent."""
    attempts: list[str] = []

    async def refuse_update(
        _request: UpdateConfigurationModule, *, level: ConfigContext | None = None
    ) -> None:
        del level
        attempts.append("update")
        raise WriteRefusedError

    async def refuse_insert(
        _request: InsertConfigurationModule, *, level: ConfigContext | None = None
    ) -> Any:
        del level
        attempts.append("insert")
        raise WriteRefusedError

    monkeypatch.setattr(vco_client, "update_configuration_module", refuse_update)
    monkeypatch.setattr(vco_client, "insert_configuration_module", refuse_insert)
    return attempts


@pytest.fixture
def writes(vco_client: VcoClient, monkeypatch: pytest.MonkeyPatch) -> Writes:
    record = Writes(updates=[], inserts=[])
    update = vco_client.update_configuration_module
    insert = vco_client.insert_configuration_module

    async def spy_update(
        request: UpdateConfigurationModule, *, level: ConfigContext | None = None
    ) -> AnyStoredModule | None:
        record.updates.append(request)
        return await update(request, level=level)

    async def spy_insert(
        request: InsertConfigurationModule, *, level: ConfigContext | None = None
    ) -> Any:
        record.inserts.append(request)
        return await insert(request, level=level)

    monkeypatch.setattr(vco_client, "update_configuration_module", spy_update)
    monkeypatch.setattr(vco_client, "insert_configuration_module", spy_insert)
    return record


@pytest.fixture
def stack_reads(vco_client: VcoClient, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The edge ID of each configuration-stack read, the read an edge ``load`` makes.

    A test's own reads of a site are stack reads too: a test clears the list
    before what it counts.
    """
    reads: list[int] = []
    read = vco_client.get_edge_configuration_stack

    async def spy_read(
        request: GetEdgeConfigurationStack,
    ) -> tuple[EdgeConfiguration, EnterpriseConfigurationProfile]:
        reads.append(request.edge_id)
        return await read(request)

    monkeypatch.setattr(vco_client, "get_edge_configuration_stack", spy_read)
    return reads


# ---------------------------------------------------------------------------
# Shared checks
# ---------------------------------------------------------------------------


async def _save(
    pipeline: ConfigPipeline, state: ConfigState, *, dry_run: bool = False
) -> SaveResult:
    """``pipeline.save``; a write error fails the test with ``outcome`` alone."""
    try:
        return await pipeline.save(state, dry_run=dry_run)
    except ModuleWriteError as error:
        # Failed outside the handler: the error must not be chained to the report.
        message = outcome(error)
    pytest.fail(message, pytrace=False)


async def _set_at(
    pipeline: ConfigPipeline, state: ConfigState, module: str, path: Path, value: object
) -> None:
    """Set the value at *path*, a path of names only, through ``apply``."""
    patch: object = value
    for part in reversed(path.parts):
        patch = {part: patch}
    _ = await pipeline.apply(state, module=module, patch=patch)


def _check_stored_as_sent(
    label: str, stored: Sequence[RawChange], plan: ModulePlan
) -> None:
    sent = {str(c.path): c for c in plan.changes}
    for change in stored:
        planned = sent.get(str(change.path))
        check(
            planned is not None
            and same_value(change.before, planned.before)
            and same_value(change.after, planned.after),
            f"{label}: {redact(change.path)} was stored other than as sent",
        )


def _planned(state: ConfigState, name: Name) -> ModulePlan:
    return next(m for m in state.plan().modules if m.name == name.value)


def _json_object(value: object) -> dict[str, Any]:
    """A deep copy of a JSON object, for a test to read or change freely."""
    return copy.deepcopy(expect(value, dict, "the JSON value"))


def _raw(module: AnyStoredModule) -> dict[str, Any]:
    return _json_object(module.raw_data)


# ---------------------------------------------------------------------------
# 1. No edit, nothing planned, nothing sent
# ---------------------------------------------------------------------------


def _unedited(label: str, state: ConfigState, failures: list[str]) -> int:
    config = state.target_config
    modules = [] if config.modules is MISSING else config.modules
    plan = state.plan()
    for module, module_plan in zip(modules, plan.modules, strict=True):
        if module_plan.operation != "none" or module_plan.changes:
            failures.append(
                f"{label} {module.name}: planned {module_plan.operation} "
                f"({describe(module_plan.changes)})"
            )
        elif module_plan.data != module.raw_data:
            failures.append(f"{label} {module.name}: plan.data differs from raw_data")
    return len(modules)


async def _save_nothing(
    label: str, pipeline: ConfigPipeline, state: ConfigState, failures: list[str]
) -> None:
    """Save a state with no edit: it must return without sending, and stay usable."""
    try:
        result = await pipeline.save(state)
    except WriteRefusedError:
        failures.append(f"{label}: save with no edit tried to write")
        return
    if result.modules_updated or result.modules_inserted:
        failures.append(f"{label}: save with no edit reports a write")


async def test_no_edit_plans_nothing_and_sends_nothing(
    lab: Lab, pipeline: ConfigPipeline, refused_writes: list[str]
) -> None:
    # Every write is refused before it reaches the Orchestrator (``refused_writes``),
    # so a regression here fails the test and cannot change the lab.
    failures: list[str] = []
    checked = 0
    for layout in lab.layouts:
        state = await pipeline.load(layout.edge_id, enterprise_id=lab.enterprise_id)
        checked += _unedited(f"edge #{layout.index}", state, failures)
        await _save_nothing(f"edge #{layout.index}", pipeline, state, failures)
    for position, profile_id in enumerate(lab.profile_ids):
        state = await pipeline.load_profile(profile_id, enterprise_id=lab.enterprise_id)
        checked += _unedited(f"profile #{position}", state, failures)
        await _save_nothing(f"profile #{position}", pipeline, state, failures)
    if not checked:
        pytest.skip("the Orchestrator has no edge or profile configuration")
    if refused_writes:
        failures.append(f"{len(refused_writes)} write attempt(s) were refused")
    check(
        not failures,
        f"{len(failures)} problem(s) across {checked} modules with no edit:\n"
        + "\n".join(failures),
    )


# ---------------------------------------------------------------------------
# 4. Profile level: load_profile, apply, save
# ---------------------------------------------------------------------------


def _profile_rule_source(module: AnyStoredModule) -> bool:
    return isinstance(module, FirewallConfigurationModule) and any(
        s.outbound for s in module.data.segments
    )


async def _copy_last_rule(pipeline: ConfigPipeline, state: ConfigState) -> Path:
    """Add a copy of a profile segment's last outbound rule through ``apply``; its path.

    The copy goes after the rule it copies, which matches first. It has no
    ``ruleLogicalId``: the Orchestrator assigns one.
    """
    module = expect(
        state.module("firewall"), FirewallConfigurationModule, "the firewall module"
    )
    segment = next(s.segment.name for s in module.data.segments if s.outbound)
    rules = [
        rule.model_dump(mode="json", by_alias=False, context={"compact": True})
        for rule in state.profile.firewall.segment(segment).outbound
    ]
    name = synthetic_name()
    added: dict[str, Any] = {**expect(rules[-1], dict, "a rule"), "name": name}
    added.pop("rule_logical_id", None)
    _ = await pipeline.apply(
        state,
        module="firewall",
        patch={"segments": {segment: {"outbound": {name: add(added)}}}},
    )
    return Path(("segments", Key(segment), "outbound", Key(name)))


@pytest.mark.live_write
async def test_profile_rule_added_through_apply_reads_back_as_that_rule_only(
    lab: Lab, pipeline: ConfigPipeline
) -> None:
    profile_id = await lab.pick_writable_profile(
        Name.FIREWALL, _profile_rule_source, "with a rule to copy"
    )
    site = lab.profile_site(profile_id, Name.FIREWALL)
    async with restoring(site) as original:
        assert original is not None
        state = await pipeline.load_profile(profile_id, enterprise_id=lab.enterprise_id)
        expected = [await _copy_last_rule(pipeline, state)]
        plan = _planned(state, Name.FIREWALL)
        check(plan.operation == "update", f"planned {plan.operation}, expected update")
        check_paths("rule edit", "the plan", plan.changes, expected)

        result = await _save(pipeline, state)
        check(
            result.modules_updated == [Name.FIREWALL] and not result.modules_inserted,
            f"saved {result.modules_updated} / inserted {result.modules_inserted}",
        )

        after = await site.read_present()
        stored = changes_between(
            Name.FIREWALL, original.data, after.raw_data, FirewallData
        )
        check_paths("rule edit", "the profile read back", stored, expected)
        _check_stored_as_sent("rule edit", stored, plan)
        check_no_override_flag("rule edit", original.data, plan.data, after.raw_data)


def _profile_has_qoe(module: AnyStoredModule) -> bool:
    return isinstance(module, QosConfigurationModule) and isinstance(
        module.data.qoe, QoeSettings
    )


@pytest.mark.live_write
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
        module = expect(state.module("QOS"), QosConfigurationModule, "the QoS module")
        qoe = expect(module.data.qoe, QoeSettings, "the QoE settings")
        yellow = qoe.voice.latency.yellow  # a reporting threshold
        _ = await pipeline.apply(
            state,
            module="QOS",
            patch={"qoe": {"voice": {"latency": {"yellow": yellow + 1}}}},
        )

        expected = [Path(("qoe", "voice", "latency", "yellow"))]
        plan = _planned(state, Name.QOS)
        check_paths("profile edit", "the plan", plan.changes, expected)
        result = await _save(pipeline, state)
        check(result.modules_updated == [Name.QOS], f"saved {result.modules_updated}")

        after = await site.read_present()
        stored = changes_between(Name.QOS, original.data, after.raw_data, QosData)
        check_paths("profile edit", "the profile read back", stored, expected)
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


def _check_inherited(
    label: str,
    before: dict[str, Any],
    after: dict[str, Any],
    name: Name,
    allowed: Sequence[tuple[str, ...]],
) -> None:
    """What an edge inherits, as the Orchestrator composes it, changed only by the edit.

    The segments, which hold the rules, must be the same; anything else may change
    only at an *allowed* key, where the edit is.
    """
    settled = expect(settle(name, before, after), dict, "the effective data")
    check(
        settled.get("segments") == before.get("segments"),
        f"{label}: the effective segments of the edge changed",
    )
    moved = [
        c
        for c in raw_changes(before, settled)
        if c.path.parts[0] != "segments"
        and not any(c.path.parts[: len(key)] == key for key in allowed)
    ]
    check(
        not moved,
        f"{label}: the effective configuration changed beyond the edit: "
        f"{describe(moved)}",
    )


@dataclass(frozen=True)
class Reconcile:
    id: str
    name: Name
    model: type[VcoModel]
    empty: dict[str, Any]
    """What a placeholder segment entry has besides the segment: lists, empty."""
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
    networks = raw.get("lan", {}).get("networks")
    if not networks:
        pytest.skip("the module has no LAN network to edit")
    vlan = networks[0]["vlanId"]
    # Assigned on the typed data, so that no override flag is set along with it.
    network = next(
        n for n in state.edge.device_settings.lan.networks if n.vlan_id == vlan
    )
    network.cost += 1  # OSPF cost
    return [Path(("lan", "networks", Key(vlan), "cost"))], ("lan", "networks")


FIREWALL_CASE = Reconcile(
    "firewall", Name.FIREWALL, FirewallData, {"outbound": []}, _syslog_edit
)
DEVICE_SETTINGS_CASE = Reconcile(
    "device-settings",
    Name.DEVICE_SETTINGS,
    DeviceSettingsData,
    {"routes": {"static": []}},  # the Orchestrator refuses an entry without it
    _cost_edit,
)


async def _lacking_layout(lab: Lab, case: Reconcile) -> EdgeLayout:
    """An edge whose *case* module lacks a segment its profile has and takes a write.

    Skip where no marked edge lacks one; fail where some do and every one
    refuses a write of its own data.
    """
    candidates: list[EdgeLayout] = []
    for layout in lab.require_edge_targets():
        edge_module = await lab.edge_site(layout, case.name).read()
        profile_module = await lab.profile_site(layout.profile_id, case.name).read()
        if edge_module is None or profile_module is None:
            continue
        if _lacking(_raw(edge_module), _raw(profile_module)):
            candidates.append(layout)
    if not candidates:
        pytest.skip(f"no edge {case.name.value} module lacks a segment its profile has")
    layout = await lab.first_writable_or_fail(candidates, case.name)
    assert layout is not None  # every candidate is a marked edge
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
        before = _json_object(original.data)
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
        check_paths("edit", "the plan", plan.changes, expected)
        _ = await _save(pipeline, state)

        written = await site.read_present()
        after = _json_object(settle(case.name, before, written.raw_data))
        stored = raw_changes(before, after, case.model)
        outside = [c for c in stored if c.path.parts[0] != "segments"]
        check_paths("edit", "the module read back, outside segments", outside, expected)
        _check_stored_as_sent("edit", outside, plan)

        placeholders = [
            {"segment": copy.deepcopy(s["segment"]), **copy.deepcopy(case.empty)}
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
        if case.name is Name.FIREWALL:
            check_no_override_flag(
                "reconciliation", before, plan.data, written.raw_data
            )

        # What the edge inherits: only the edit may show, never the placeholders.
        _check_inherited(
            "edit",
            effective_before,
            await effective_data(
                lab.client, layout.edge_id, case.name, lab.enterprise_id
            ),
            case.name,
            [allowed],
        )


@pytest.mark.live_write
async def test_unrelated_edit_adds_the_missing_segments_as_empty_placeholders(
    lab: Lab, pipeline: ConfigPipeline
) -> None:
    layout = await _lacking_layout(lab, FIREWALL_CASE)
    await _reconcile(lab, pipeline, FIREWALL_CASE, layout)


GLOBAL_SEGMENT_ID = 0


def _has_lan_network(raw: dict[str, Any]) -> bool:
    lan = raw.get("lan")
    return isinstance(lan, dict) and bool(lan.get("networks"))  # pyright: ignore[reportUnknownMemberType]


def _is_empty_entry(entry: object) -> bool:
    """A segment entry that holds nothing.

    Its descriptor and routes with only empty lists.

    That is what a placeholder is (``routeMapsVersion``, which the Orchestrator
    adds on a write, does not count). Taking such an entry out of a module and
    putting it back loses no routing, BGP, OSPF, NAT or any other content.
    """
    if not isinstance(entry, dict):
        return False
    fields = cast("dict[str, object]", entry)
    if set(fields) - {SEGMENT_DRIFT_KEY} != {"segment", "routes"}:
        return False
    routes = fields["routes"]
    return isinstance(routes, dict) and all(
        isinstance(value, list) and not value
        for value in cast("dict[str, object]", routes).values()
    )


def _empty_shared_segments(
    edge: dict[str, Any], profile: dict[str, Any]
) -> list[object]:
    """The IDs of the non-Global segments both layers have, empty on the edge.

    An entry is empty as ``_is_empty_entry`` says.
    """
    in_profile = [_segment_id(s) for s in profile.get("segments") or []]
    return [
        found
        for entry in edge.get("segments") or []
        if (found := _segment_id(entry)) != GLOBAL_SEGMENT_ID
        and found in in_profile
        and _is_empty_entry(entry)
    ]


async def _reconcile_unrefused(
    lab: Lab, pipeline: ConfigPipeline, case: Reconcile, layout: EdgeLayout
) -> None:
    """``_reconcile``; a refusal by the Orchestrator fails the test.

    The failure gives its codes alone.
    """
    refused: str | None = None
    try:
        await _reconcile(lab, pipeline, case, layout)
    except VcoOperationError as exc:
        refused = refusal_codes(exc)
    check(refused is None, f"the Orchestrator refused the write: {refused}")


@pytest.mark.live_write
async def test_missing_device_settings_segment_is_added_as_a_placeholder_the_orchestrator_accepts(  # noqa: E501 (a test's name)
    lab: Lab, pipeline: ConfigPipeline
) -> None:
    case = DEVICE_SETTINGS_CASE
    lacking: list[EdgeLayout] = []
    sharing: list[EdgeLayout] = []
    for layout in lab.require_edge_targets():
        edge_module = await lab.edge_site(layout, case.name).read()
        profile_module = await lab.profile_site(layout.profile_id, case.name).read()
        if edge_module is None or profile_module is None:
            continue
        edge, profile = _raw(edge_module), _raw(profile_module)
        if not _has_lan_network(edge):
            continue
        if _lacking(edge, profile):
            lacking.append(layout)
        elif _empty_shared_segments(edge, profile):
            sharing.append(layout)

    # An edge that already lacks a segment its profile has; a module that
    # refuses its own unchanged data is passed over (it would refuse anything),
    # and the test fails where every such edge refuses.
    target = await lab.first_writable_or_fail(lacking, case.name)
    if target is not None:
        own = lab.edge_site(target, case.name)
        refs_own = ref_names((await own.read_present()).raw_refs)
        await _reconcile_unrefused(lab, pipeline, case, target)
        check(
            ref_names((await own.read_present()).raw_refs) == refs_own,
            "the module has other refs than before the test",
        )
        return

    # Else make an edge lack one: take out a non-Global segment entry that is
    # already empty (nothing in it is lost) from a module that takes a write,
    # and put it back afterwards.
    target = await lab.first_writable_or_fail(sharing, case.name)
    if target is None:
        pytest.skip(
            "no marked edge deviceSettings module lacks a segment entry of its "
            "profile's, or has an empty one besides the Global one"
        )
    site = lab.edge_site(target, case.name)
    profile_site = lab.profile_site(target.profile_id, case.name)
    refs_before = ref_names((await site.read_present()).raw_refs)
    async with restoring(site):
        module = await site.read_present()
        data = _raw(module)
        profile = _raw(await profile_site.read_present())
        removed = _empty_shared_segments(data, profile)[0]
        data["segments"] = [s for s in data["segments"] if _segment_id(s) != removed]
        refused: str | None = None
        try:
            await write_data(site, module, data)
        except DataRefusedError as exc:
            refused = refusal_codes(exc)
        if refused is not None:
            pytest.skip(
                "the Orchestrator takes no module without that segment entry: "
                f"{refused}"
            )
        check(
            len(_lacking(_raw(await site.read_present()), profile)) == 1,
            "the edge does not lack the segment entry that was taken out",
        )
        await _reconcile_unrefused(lab, pipeline, case, target)
    # The restore compares data; the Orchestrator re-derives a deviceSettings
    # module's refs from it, so the refs are compared here, by name.
    check(
        ref_names((await site.read_present()).raw_refs) == refs_before,
        "the module has other refs than before the test",
    )


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


@pytest.mark.live_write
async def test_an_insert_returns_its_row(
    lab: Lab, pipeline: ConfigPipeline, writes: Writes
) -> None:
    """A missing edge QoS module, inserted from an empty layer, reads back as sent.

    The state then holds the row the insert returned, no longer new, and a
    second edit saves as an update of it.
    """
    lab.require_edge_targets(replace_modules=True)
    layout = next(iter(lab.edges_lacking(Name.QOS)), None)
    if layout is None:
        pytest.skip(
            "no edge marked pyvelo-it-replace-modules lacks a QoS module of its own"
        )
    profile_module = await lab.profile_site(layout.profile_id, Name.QOS).read()
    if profile_module is None:
        pytest.skip("the profile of an edge with no QoS module has none either")
    site = lab.edge_site(layout, Name.QOS)
    async with restoring(site) as original:
        assert original is None
        profile = _raw(profile_module)
        effective_before = await effective_data(
            lab.client, layout.edge_id, Name.QOS, lab.enterprise_id
        )
        state = await pipeline.load(layout.edge_id, enterprise_id=lab.enterprise_id)
        # A disabled service rate limit: new content that limits nothing.
        state.edge.qos.service_rate_limit.set_local(
            ServiceRateLimit.model_validate(
                {"enabled": False, "inputType": "percent", "value": 1}
            )
        )
        plan = _planned(state, Name.QOS)
        check(plan.operation == "insert", f"planned {plan.operation}, expected insert")

        result = await _save(pipeline, state)
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
        _check_inherited(
            "insert",
            effective_before,
            await effective_data(
                lab.client, layout.edge_id, Name.QOS, lab.enterprise_id
            ),
            Name.QOS,
            # The Orchestrator also fills in the default QoE thresholds in the
            # effective QoS of an edge that has a QoS module of its own (seen on
            # a lab; they are the values an edge uses without one). Allowed only
            # where there were none before.
            [("serviceRateLimit",)] + ([] if "qoe" in effective_before else [("qoe",)]),
        )

        # The state holds the row the insert returned.
        inserted = state.module("QOS")
        check(not inserted.is_new, "the inserted module is still new in the state")
        check(
            inserted.id_ == stored.id_,
            "the state's module has another ID than the module inserted",
        )
        check_as_read("insert", inserted, stored)

        # A second edit, from the state as the insert left it, is an update.
        writes.inserts.clear()
        writes.updates.clear()
        limit = expect(
            state.edge.qos.service_rate_limit.local,
            ServiceRateLimit,
            "the edge's own rate limit",
        )
        limit.value += 1  # the limit stays disabled: it limits nothing
        plan = _planned(state, Name.QOS)
        check(plan.operation == "update", f"planned {plan.operation}, expected update")
        result = await _save(pipeline, state)
        check(
            result.modules_updated == [Name.QOS] and not result.modules_inserted,
            f"updated {result.modules_updated}, inserted {result.modules_inserted}",
        )
        check(
            len(writes.updates) == 1 and not writes.inserts, "expected one update only"
        )
        check(
            writes.updates[0].version == stored.version,
            "the update does not carry the version the insert returned",
        )
        updated = await site.read_present()
        check_paths(
            "update after the insert",
            "the module read back",
            changes_between(Name.QOS, stored.raw_data, updated.raw_data, QosData),
            [Path(("serviceRateLimit", "value"))],
        )

        await delete_module(site, updated.id_)
        check(await site.read() is None, "the inserted module is still there")


def _firewall_that_comes_back_whole(module: AnyStoredModule) -> bool:
    """A firewall module that can be deleted and inserted again as it was.

    An insert takes no refs, and the Orchestrator issues new rule IDs for the
    rules it is given, so a module with either would not be the same module.
    A module with inbound (port-forward) or NAT content is left alone too: that
    is the local content an edge's connectivity can depend on.
    """
    raw = module.raw_data
    return (
        isinstance(module, FirewallConfigurationModule)
        and not (isinstance(module.raw_refs, dict) and module.raw_refs)
        and not has_key(raw, "ruleLogicalId")
        and not (isinstance(raw, dict) and (raw.get("inbound") or raw.get("inboundV6")))  # pyright: ignore[reportUnknownMemberType]
        and not has_key(raw, "nat")
        and not has_key(raw, "natV6")
    )


@pytest.mark.live_write
async def test_edge_firewall_deleted_and_inserted_again_reads_back_as_sent(
    lab: Lab, pipeline: ConfigPipeline, writes: Writes
) -> None:
    layout = await lab.pick_writable_edge(
        Name.FIREWALL,
        _firewall_that_comes_back_whole,
        "with no refs, no rule IDs and no inbound or NAT content, which an insert "
        "would not give back or which a lab edge may depend on",
        replace_modules=True,
    )
    site = lab.edge_site(layout, Name.FIREWALL)
    async with restoring(site) as original:
        assert original is not None
        profile = _raw(
            await lab.profile_site(layout.profile_id, Name.FIREWALL).read_present()
        )
        effective_before = await effective_data(
            lab.client, layout.edge_id, Name.FIREWALL, lab.enterprise_id
        )
        await delete_module(site, (await site.read_present()).id_)
        check(await site.read() is None, "the firewall module was not deleted")
        writes.updates.clear()

        state = await pipeline.load(layout.edge_id, enterprise_id=lab.enterprise_id)
        state.edge.firewall.syslog_forwarding.set_local(
            True
        )  # forward logs; none are collected
        plan = _planned(state, Name.FIREWALL)
        check(plan.operation == "insert", f"planned {plan.operation}, expected insert")
        result = await _save(pipeline, state)
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
        check_no_override_flag("insert", original.data, plan.data, stored.raw_data)
        _check_inherited(
            "insert",
            effective_before,
            await effective_data(
                lab.client, layout.edge_id, Name.FIREWALL, lab.enterprise_id
            ),
            Name.FIREWALL,
            [("syslog_forwarding",)],
        )

        await delete_module(site, stored.id_)
        check(await site.read() is None, "the inserted module is still there")
    # Leaving the block inserted the original module again, and checked it.


# ---------------------------------------------------------------------------
# 7. A stale version through the pipeline
# ---------------------------------------------------------------------------


def _wan_link(module: AnyStoredModule) -> bool:
    """Whether *module* is a WAN module whose first link has a key (an internal ID)."""
    if not isinstance(module, WanConfigurationModule):
        return False
    links = module.data.links
    return links is not MISSING and bool(links) and links[0].internal_id is not MISSING


VOICE_RED = Path(("qoe", "voice", "latency", "red"))
VIDEO_RED = Path(("qoe", "video", "latency", "red"))


def _qoe_red_thresholds(module: AnyStoredModule) -> bool:
    """Whether *module* is a QoS module whose raw data has red latency thresholds.

    They are those of voice and video.
    """
    return _profile_has_qoe(module) and all(
        isinstance(value_at(module.raw_data, path), int)
        for path in (VOICE_RED, VIDEO_RED)
    )


@dataclass(frozen=True)
class Stale:
    id: str
    newer: Path
    """The threshold the newer state raises by one, and saves first."""
    older: Path
    """The threshold the older state raises by two, and saves after it."""


STALE_CASES = [
    # Both states edit one value: the older one's edit meets the newer one's.
    Stale("same-value", VOICE_RED, VOICE_RED),
    # Each edits its own: the newer one's edit is a change beside the older one's.
    Stale("another-value", VOICE_RED, VIDEO_RED),
]


@pytest.mark.live_write
@pytest.mark.parametrize("case", STALE_CASES, ids=[c.id for c in STALE_CASES])
async def test_second_save_from_an_older_state_is_refused_and_refreshes_the_state(
    lab: Lab,
    pipeline: ConfigPipeline,
    capsys: pytest.CaptureFixture[str],
    case: Stale,
) -> None:
    profile_id = await lab.pick_writable_profile(
        Name.QOS, _qoe_red_thresholds, "with red QoE thresholds for voice and video"
    )
    site = lab.profile_site(profile_id, Name.QOS)
    async with restoring(site) as original:
        assert original is not None
        newer = await pipeline.load_profile(profile_id, enterprise_id=lab.enterprise_id)
        older = await pipeline.load_profile(profile_id, enterprise_id=lab.enterprise_id)
        # Reporting thresholds, which steer nothing; a red one raised stays above
        # its yellow one.
        kept = expect(value_at(original.data, case.newer), int, "a threshold") + 1
        as_read = expect(value_at(original.data, case.older), int, "a threshold")
        redone = as_read + 2
        await _set_at(pipeline, newer, "QOS", case.newer, kept)
        await _set_at(pipeline, older, "QOS", case.older, redone)

        _ = await _save(pipeline, newer)
        # Where both edit one value, a VCO with its conditional check off answers
        # with the module as stored, whose value there is the newer state's: a
        # clash, WriteNotAppliedError. With the check on it is ConfigConflictError.
        with pytest.raises((ConfigConflictError, WriteNotAppliedError)) as caught:
            _ = await pipeline.save(older)
        error = caught.value
        record_refusal(capsys, f"stale save, {case.id}", error)
        if case.newer != case.older:
            # Nothing the older state planned is stored, and nothing there moved.
            check(
                isinstance(error, ConfigConflictError),
                f"the stale save raised {outcome(error)}",
            )

        # The error, and the state, hold the module as stored: the newer state's.
        now = await site.read_present()
        check(
            value_at(now.raw_data, case.newer) == kept,
            "the module does not hold the newer state's edit",
        )
        check(error.module is not None, "the error carries no module")
        assert error.module is not None
        check_as_read("the error's module", error.module, now)
        check_as_read("the refused state's module", older.module("QOS"), now)
        check(not older.plan().changed, "the refused state still plans a write")

        # The lost edit is the older state's; the server changes are the newer's.
        check_entries("stale save", "lost_edits", error.lost_edits, [case.older])
        (lost,) = error.lost_edits
        check(
            lost.as_read == as_read and lost.intended == redone,
            "the lost edit does not hold the value read and the value meant",
        )
        server: list[Path] = []
        if case.newer == case.older:
            check(
                lost.stored == kept and lost.clash,
                "the lost edit is not a clash with the newer state's value",
            )
        else:
            check(
                not lost.clash, "the lost edit is a clash, though nothing moved there"
            )
            server = [case.newer]
        check_entries("stale save", "server_changes", error.server_changes, server)

        # The same state redoes the edit and saves, with no reload.
        await _set_at(pipeline, older, "QOS", case.older, redone)
        _ = await _save(pipeline, older)
        after = await site.read_present()
        check_paths(
            "redo",
            "the module read back",
            changes_between(Name.QOS, original.data, after.raw_data, QosData),
            list(dict.fromkeys([case.newer, case.older])),
        )
        check(
            value_at(after.raw_data, case.older) == redone,
            "the module does not hold the redone edit",
        )
        if case.newer != case.older:
            check(
                value_at(after.raw_data, case.newer) == kept,
                "the module no longer holds the newer state's edit",
            )


# ---------------------------------------------------------------------------
# 8. Refs
# ---------------------------------------------------------------------------


def _device_settings_with_refs(module: AnyStoredModule) -> bool:
    return (
        isinstance(module, DeviceSettingsConfigurationModule)
        and isinstance(module.raw_refs, dict)
        and bool(module.raw_refs)
        and bool(module.data.segments)
        and bool(module.data.lan.networks)
    )


@pytest.mark.live_write
async def test_save_of_a_data_edit_sends_its_refs_as_read(
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
        as_read = {
            name: rows
            for name, rows in cast("dict[str, Any]", original.refs).items()
            if name not in APPEND_ONLY_REF_NAMES
        }
        plan = _planned(state, Name.DEVICE_SETTINGS)
        check(
            plan.refs == as_read and plan.ref_changes == (),
            "the plan does not carry the refs as read for a data edit",
        )
        _ = await _save(pipeline, state)
        check(
            len(writes.updates) == 1, f"expected one write, saw {len(writes.updates)}"
        )
        check(
            writes.updates[0].update.refs == as_read,
            "a data edit of a module with segments did not send its refs as read",
        )


# ---------------------------------------------------------------------------
# 9. A segment key in another case edits the segment it names
# ---------------------------------------------------------------------------


def _firewall_segment_with_letters(module: AnyStoredModule) -> bool:
    return isinstance(module, FirewallConfigurationModule) and any(
        s.segment.name.swapcase() != s.segment.name for s in module.data.segments
    )


@pytest.mark.live_write
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
        before = _json_object(original.data)
        effective_before = await effective_data(
            lab.client, layout.edge_id, Name.FIREWALL, lab.enterprise_id
        )
        entry = next(
            s
            for s in before["segments"]
            if s["segment"]["name"].swapcase() != s["segment"]["name"]
        )
        name = entry["segment"]["name"]
        # A logging flag of the segment: it changes no rule and steers no traffic.
        current = entry.get("firewall_logging_enabled")
        flag = not current if isinstance(current, bool) else True

        state = await pipeline.load(layout.edge_id, enterprise_id=lab.enterprise_id)
        _ = await pipeline.apply(
            state,
            module="firewall",
            patch={"segments": {name.swapcase(): {"firewall_logging_enabled": flag}}},
        )
        plan = _planned(state, Name.FIREWALL)
        expected = [Path(("segments", Key(name), "firewall_logging_enabled"))]
        check_paths("edit by another case", "the plan", plan.changes, expected)
        _ = await _save(pipeline, state)

        written = await site.read_present()
        after = _json_object(written.raw_data)
        stored = raw_changes(before, after, FirewallData)
        check_paths("edit by another case", "the module read back", stored, expected)
        check(
            len(after["segments"]) == len(before["segments"]),
            "a segment was added: the other-case key did not find the existing one",
        )
        check_no_override_flag(
            "edit by another case", before, plan.data, written.raw_data
        )

        # No inherited rule changed: only a logging flag of the segment did.
        effective_after = await effective_data(
            lab.client, layout.edge_id, Name.FIREWALL, lab.enterprise_id
        )
        rules_before = [e.get("outbound") for e in effective_before["segments"]]
        rules_after = [e.get("outbound") for e in effective_after["segments"]]
        check(
            rules_before == rules_after,
            "the effective outbound rules of the edge changed with a logging flag",
        )


# ---------------------------------------------------------------------------
# 10. The state after a save
# ---------------------------------------------------------------------------


def _first_link(state: ConfigState) -> tuple[str, str]:
    """The key of the state's first WAN link: its internal ID and address family.

    Link names repeat.
    """
    wan = expect(state.module("WAN"), WanConfigurationModule, "the WAN module")
    links = wan.data.links
    check(links is not MISSING and bool(links), "the WAN module has no link")
    assert links is not MISSING
    return str(links[0].internal_id), links[0].addressing_version


@pytest.mark.live_write
async def test_save_then_edit_and_save_again_without_a_reload(
    lab: Lab, pipeline: ConfigPipeline, writes: Writes, stack_reads: list[int]
) -> None:
    layout = await lab.pick_writable_edge(
        Name.WAN, _wan_link, "with a link that has an internal ID"
    )
    site = lab.edge_site(layout, Name.WAN)
    async with restoring(site) as original:
        assert original is not None
        state = await pipeline.load(layout.edge_id, enterprise_id=lab.enterprise_id)
        link = _first_link(state)
        loaded = state.module("WAN").version
        first, second = synthetic_name(), synthetic_name()

        # The pipeline's stack reads are counted call by call: the test's own
        # reads of the module are stack reads too.
        writes.updates.clear()
        stack_reads.clear()
        _ = await pipeline.apply(
            state, module="WAN", patch={"links": {link: {"description": first}}}
        )
        _ = await _save(pipeline, state)
        reloads = len(stack_reads)

        saved = await site.read_present()
        check_as_read("after the first save", state.module("WAN"), saved)
        check(
            saved.version != loaded, "the first save did not change the module version"
        )
        check(not state.plan().changed, "plan() after a save is not empty")

        stack_reads.clear()
        _ = await pipeline.apply(
            state, module="WAN", patch={"links": {link: {"description": second}}}
        )
        _ = await _save(pipeline, state)
        reloads += len(stack_reads)
        check(
            not reloads,
            f"the pipeline read the configuration stack {reloads} time(s) for the "
            "two saves",
        )
        check(
            len(writes.updates) == 2, f"expected two writes, saw {len(writes.updates)}"
        )
        check(
            writes.updates[1].version == saved.version,
            "the second save does not carry the version the first one returned",
        )

        read = await site.read_present()
        check_as_read("after the second save", state.module("WAN"), read)
        stored = changes_between(Name.WAN, original.data, read.raw_data, WanData)
        check_paths(
            "two saves",
            "the module read back",
            stored,
            [Path(("links", Key(link), "description"))],
        )
        check(
            stored[0].after == second,
            "the module does not hold the second save's edit",
        )


def _qos_rule_source(module: AnyStoredModule) -> bool:
    return isinstance(module, QosConfigurationModule) and any(
        rule.match.ip_version == "IPv4v6"
        for segment in module.data.segments
        for rule in segment.rules
    )


def _lan_network(module: AnyStoredModule) -> bool:
    return isinstance(module, DeviceSettingsConfigurationModule) and bool(
        module.data.lan.networks
    )


async def _add_firewall_rule(
    pipeline: ConfigPipeline, state: ConfigState
) -> list[Path]:
    return [(await _copy_last_rule(pipeline, state)).child("ruleLogicalId")]


async def _add_qos_rule(_: ConfigPipeline, state: ConfigState) -> list[Path]:
    """Append a copy of a profile segment's IPv4v6 rule to the segment's rules.

    After the rule it copies, which matches first. The copy has a
    ``ruleLogicalId`` of its own, as the model gives a new rule.
    """
    segment, source = next(
        (segment, rule)
        for segment in state.profile.qos.segments
        for rule in segment.rules
        if rule.match.ip_version == "IPv4v6"
    )
    added = source.model_copy(deep=True)
    added.name = synthetic_name()
    added.rule_logical_id = uuid.uuid4()
    segment.rules.append(added)
    return []


async def _raise_lan_cost(pipeline: ConfigPipeline, state: ConfigState) -> list[Path]:
    _ = await _cost_edit(pipeline, state, _raw(state.module(Name.DEVICE_SETTINGS)))
    return []


@dataclass(frozen=True)
class Rewrite:
    id: str
    name: Name
    level: Literal["edge", "profile"]
    suitable: Callable[[AnyStoredModule], bool]
    what: str
    edit: Callable[[ConfigPipeline, ConfigState], Awaitable[list[Path]]]
    """Makes the edit through the pipeline; returns where a server change must be."""
    known: tuple[PathPattern, ...]
    """Where a server change may be: the keys the header lists, for this module."""
    refs_rederived: bool = False
    """The Orchestrator re-derives the module's refs from its data (the header)."""


SERVER_ASSIGNED: tuple[PathPattern, ...] = tuple(
    ("**", key) for key in sorted(SERVER_ASSIGNED_KEYS)
)

REWRITE_CASES = [
    Rewrite(
        "firewall-rule",
        Name.FIREWALL,
        "profile",
        _profile_rule_source,
        "with a rule to copy",
        _add_firewall_rule,
        SERVER_ASSIGNED,
    ),
    Rewrite(
        "qos-rule",
        Name.QOS,
        "profile",
        _qos_rule_source,
        "with an IPv4v6 rule to copy",
        _add_qos_rule,
        SERVER_ASSIGNED,
    ),
    Rewrite(
        "device-settings",
        Name.DEVICE_SETTINGS,
        "edge",
        _lan_network,
        "with a LAN network",
        _raise_lan_cost,
        (("segments", "*", SEGMENT_DRIFT_KEY),),
        refs_rederived=True,
    ),
]


async def _rewrite_target(
    lab: Lab, pipeline: ConfigPipeline, case: Rewrite
) -> tuple[Site, Callable[[], Awaitable[ConfigState]]]:
    """The site a *case* writes, and how to load its state; or skip."""
    if case.level == "profile":
        profile_id = await lab.pick_writable_profile(
            case.name, case.suitable, case.what
        )

        async def load_profile() -> ConfigState:
            return await pipeline.load_profile(
                profile_id, enterprise_id=lab.enterprise_id
            )

        return lab.profile_site(profile_id, case.name), load_profile

    layout = await lab.pick_writable_edge(case.name, case.suitable, case.what)

    async def load_edge() -> ConfigState:
        return await pipeline.load(layout.edge_id, enterprise_id=lab.enterprise_id)

    return lab.edge_site(layout, case.name), load_edge


def _known_rewrite(case: Rewrite, change: ServerChange) -> bool:
    if change.part == "refs":
        return case.refs_rederived
    return any(matches(change.path, pattern) for pattern in case.known)


@pytest.mark.live_write
@pytest.mark.parametrize("case", REWRITE_CASES, ids=[c.id for c in REWRITE_CASES])
async def test_server_changes_hold_only_known_rewrites(
    lab: Lab, pipeline: ConfigPipeline, case: Rewrite
) -> None:
    site, load = await _rewrite_target(lab, pipeline, case)
    async with restoring(site) as original:
        assert original is not None
        state = await load()
        required = await case.edit(pipeline, state)
        # A rewrite at a path the edit planned fails here, by outcome.
        result = await _save(pipeline, state)
        check(
            result.modules_updated == [case.name] and not result.modules_inserted,
            f"{case.id}: saved {result.modules_updated} / inserted "
            f"{result.modules_inserted}",
        )

        found = result.server_changes.get(case.name, ())
        unknown = [c for c in found if not _known_rewrite(case, c)]
        check(
            not unknown,
            f"{case.id}: server changes beyond the known rewrites: {where(unknown)}. "
            "A key only the Orchestrator adds goes in the known list here "
            "(Rewrite.known, or SERVER_ASSIGNED_KEYS, and the module header); a "
            "value it stores otherwise at a planned path goes in MODULE_REWRITES",
        )
        reported = {str(c.path) for c in found}
        missing = [p for p in required if str(p) not in reported]
        check(
            not missing,
            f"{case.id}: no server change at {', '.join(redact(p) for p in missing)}",
        )


# ---------------------------------------------------------------------------
# 11. A refused save at edge level
# ---------------------------------------------------------------------------


@pytest.mark.live_write
@pytest.mark.parametrize("read_back", [False, True], ids=["as-answered", "read-back"])
async def test_refused_edge_save_refreshes_the_state(
    lab: Lab,
    pipeline: ConfigPipeline,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    read_back: bool,
) -> None:
    """A stale save of an edge module leaves the state holding the module as stored.

    The state can save it again.

    ``read-back`` sends the writes without ``returnData``, so the
    Orchestrator answers with a row count and the module is read back from
    the edge's configuration. Either way the module must come back at the
    level it was loaded at: an edge state's modules are loaded without one.
    """
    layout = await lab.pick_writable_edge(
        Name.WAN, _wan_link, "with a link that has an internal ID"
    )
    site = lab.edge_site(layout, Name.WAN)
    async with restoring(site) as original:
        assert original is not None
        if read_back:
            without_return_data(lab.client, monkeypatch)
        newer = await pipeline.load(layout.edge_id, enterprise_id=lab.enterprise_id)
        older = await pipeline.load(layout.edge_id, enterprise_id=lab.enterprise_id)
        link = _first_link(older)
        loaded = older.module("WAN").config_context
        kept, refused, redone = synthetic_name(), synthetic_name(), synthetic_name()
        for state, text in ((newer, kept), (older, refused)):
            _ = await pipeline.apply(
                state, module="WAN", patch={"links": {link: {"description": text}}}
            )
        _ = await _save(pipeline, newer)

        with pytest.raises((ConfigConflictError, WriteNotAppliedError)) as caught:
            _ = await pipeline.save(older)
        error = caught.value
        now = await site.read_present()
        check(error.module is not None, "the error carries no module")
        assert error.module is not None
        level = error.module.config_context
        record(
            capsys,
            f"refused edge save, {'read back' if read_back else 'as answered'}: "
            f"{refusal_form(error)} ({type(error).__name__}); the module was loaded "
            f"at level {loaded or 'none'} and comes back at level {level or 'none'}",
        )
        check(
            level is loaded,
            "the module comes back from a refused save at another level than it "
            "was loaded at",
        )
        check_as_read("the error's module", error.module, now)
        check_as_read("the refused state's module", older.module("WAN"), now)
        check(not older.plan().changed, "the refused state still plans a write")
        path = Path(("links", Key(link), "description"))
        check_entries("refused edge save", "lost_edits", error.lost_edits, [path])
        (lost,) = error.lost_edits
        check(
            lost.intended == refused and lost.stored == kept and lost.clash,
            "the lost edit is not the refused description, a clash with the newer "
            "state's",
        )

        # The same state redoes the edit and saves, with no reload.
        _ = await pipeline.apply(
            older, module="WAN", patch={"links": {link: {"description": redone}}}
        )
        _ = await _save(pipeline, older)
        after = await site.read_present()
        stored = changes_between(Name.WAN, original.data, after.raw_data, WanData)
        check_paths("redo", "the module read back", stored, [path])
        check(stored[0].after == redone, "the module does not hold the redone edit")


# ---------------------------------------------------------------------------
# 12. An IPv6 key in upper case
# ---------------------------------------------------------------------------


def _v6_route(data: object) -> tuple[str, StaticRoute] | None:
    """The segment name and the route of the first long-prefix IPv6 static route.

    Its prefix is 48 or longer.
    """
    if not isinstance(data, DeviceSettingsData):
        return None
    for segment in data.segments:
        routes = segment.routes
        if routes is MISSING:
            continue
        for route in routes.static_v6:
            prefix = str(route.cidr_prefix)
            if prefix.isdecimal() and int(prefix) >= 48:
                return segment.segment.name, route
    return None


def _v6_route_source(module: AnyStoredModule) -> bool:
    return _v6_route(module.data) is not None


def _v6_destinations(module: AnyStoredModule, description: str) -> list[str]:
    """The destination of each IPv6 static route of *module* that has *description*."""
    data = expect(module.data, DeviceSettingsData, "the deviceSettings data")
    return [
        route.destination
        for segment in data.segments
        if segment.routes is not MISSING
        for route in segment.routes.static_v6
        if route.description == description
    ]


@pytest.mark.live_write
async def test_an_ipv6_key_in_upper_case_is_recorded(
    lab: Lab, pipeline: ConfigPipeline, capsys: pytest.CaptureFixture[str]
) -> None:
    """What the Orchestrator stores for an IPv6 key sent in upper case.

    And whether the save passes.

    A copy of an IPv6 static route the edge has, to a prefix of the
    documentation range and not advertised, is added through ``apply`` with
    its destination in upper case. The run records whether the destination
    is stored as sent or in lower case, and what applying the same ``add``
    again does to the state: a key stored in another text is not found by its
    selector, and the item is planned a second time. Nothing of that second
    apply is saved.
    """
    layout = await lab.pick_writable_edge(
        Name.DEVICE_SETTINGS,
        _v6_route_source,
        "with an IPv6 static route of prefix 48 or longer to copy",
    )
    site = lab.edge_site(layout, Name.DEVICE_SETTINGS)
    async with restoring(site) as original:
        assert original is not None
        state = await pipeline.load(layout.edge_id, enterprise_id=lab.enterprise_id)
        found = _v6_route(state.edge.device_settings)
        check(found is not None, "the state has no IPv6 static route to copy")
        assert found is not None
        segment, source = found
        # A group of A000 to FFFF: upper case differs from the canonical text
        # only by its case.
        sent = f"2001:DB8:{0xA000 + uuid.uuid4().int % 0x6000:X}::"
        name = synthetic_name()
        key = (sent, source.cidr_prefix, source.gateway, source.wan_interface)
        body: dict[str, Any] = {
            field: value
            for field, value in source.model_dump(
                mode="json", by_alias=False, context={"compact": True}
            ).items()
            if field not in ("destination", "cidr_prefix", "gateway", "wan_interface")
        }
        body.update(description=name, advertise=False)
        patch = {key: add(body)}

        async def apply() -> None:
            _ = await pipeline.apply(
                state,
                module="device_settings",
                segment=segment,
                path="routes.static_v6",
                patch=patch,
            )

        await apply()
        plan = _planned(state, Name.DEVICE_SETTINGS)
        route = Path(("segments", Key(segment), "routes", "staticV6", Key(key)))
        check_paths("IPv6 route", "the plan", plan.changes, [route])

        saved = "passed"
        try:
            _ = await pipeline.save(state)
        except ModuleWriteError as error:
            saved = f"raised {outcome(error)}"
        kept = _v6_destinations(await site.read_present(), name)
        if len(kept) != 1:
            form = f"in {len(kept)} routes"
        elif kept[0] == sent:
            form = "as sent"
        elif kept[0] == sent.lower():
            form = "in lower case"
        else:
            form = "in another text"
        try:
            await apply()
        except PatchError as error:
            again = (
                "is refused: the item is there"
                if error.kind == "exists"
                else f"is refused ({error.kind})"
            )
        else:
            again = "plans a second item" if state.plan().changed else "plans nothing"
        record(
            capsys,
            f"an IPv6 key in upper case: the save {saved}; the destination is stored "
            f"{form}; applying the same add again {again}",
        )
        check(saved == "passed", f"the save of an IPv6 key in upper case {saved}")
        check(
            form in ("as sent", "in lower case"),
            f"the destination of the added route is stored {form}",
        )


# ---------------------------------------------------------------------------
# 13. An insert answered with an ID is read back
# ---------------------------------------------------------------------------


@pytest.mark.live_write
async def test_an_insert_answered_with_an_id_is_read_back(
    lab: Lab,
    pipeline: ConfigPipeline,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing edge QoS module, inserted without ``returnData``, is read back.

    It is read by the ID answered.

    As from a VCO that ignores ``returnData`` (``without_return_data``): the
    insert is answered with the new module's ID, and the state must then hold
    the module as a read made right after shows it.
    """
    lab.require_edge_targets(replace_modules=True)
    layout = next(iter(lab.edges_lacking(Name.QOS)), None)
    if layout is None:
        pytest.skip(
            "no edge marked pyvelo-it-replace-modules lacks a QoS module of its own"
        )
    site = lab.edge_site(layout, Name.QOS)
    async with restoring(site) as original:
        assert original is None
        without_return_data(lab.client, monkeypatch)
        answers = record_answers(lab.client, monkeypatch)
        state = await pipeline.load(layout.edge_id, enterprise_id=lab.enterprise_id)
        # A disabled service rate limit: new content that limits nothing.
        state.edge.qos.service_rate_limit.set_local(
            ServiceRateLimit.model_validate(
                {"enabled": False, "inputType": "percent", "value": 1}
            )
        )
        plan = _planned(state, Name.QOS)
        check(plan.operation == "insert", f"planned {plan.operation}, expected insert")
        result = await _save(pipeline, state)
        check(
            result.modules_inserted == [Name.QOS] and not result.modules_updated,
            f"inserted {result.modules_inserted}, updated {result.modules_updated}",
        )
        check(
            read_backs(answers) == 1,
            f"the insert was read back {read_backs(answers)} time(s), expected once",
        )

        stored = await site.read_present()
        inserted = state.module("QOS")
        check(not inserted.is_new, "the inserted module is still new in the state")
        check(
            inserted.id_ == stored.id_,
            "the state's module has another ID than the module inserted",
        )
        check_as_read("insert", inserted, stored)
        check(
            stored.raw_data == plan.data,
            "the inserted module does not read back as it was sent: "
            + describe(raw_changes(plan.data, stored.raw_data)),
        )
        record(
            capsys,
            "an insert without returnData: answered with an ID, and read back as "
            f"stored at level {inserted.config_context or 'none'}",
        )

        await delete_module(site, stored.id_)
        check(await site.read() is None, "the inserted module is still there")


# ---------------------------------------------------------------------------
# 14. Derived refs: a provider set and cleared, a stale version, every row kept
# ---------------------------------------------------------------------------

PRIMARY_PROVIDER = "deviceSettings:dns:primaryProvider"
SEGMENT_REF = "deviceSettings:segment"
SENT_ROW_FIELDS = frozenset(
    {"configurationId", "moduleId", "enterpriseObjectId", "ref"}
)
"""The fields of a new row; a per-segment ref also has ``segmentObjectId``."""


def _rows_named(refs: object, name: str) -> list[dict[str, Any]]:
    """The rows of ref *name* in raw *refs*: the one object, or each of the list."""
    if not isinstance(refs, dict):
        return []
    held = cast("dict[str, Any]", refs).get(name)
    rows = cast("list[Any]", held) if isinstance(held, list) else [held]
    return [cast("dict[str, Any]", row) for row in rows if isinstance(row, dict)]


REBUILT_REF_NAMES = PROCESSOR_REF_NAMES - APPEND_ONLY_REF_NAMES
"""Names whose rows the VCO's processors delete and rebuild on a write: not compared
after one.

``deviceSettings:vnfs:edge`` is a processor name too, but it is append-only, so
its rows stay and are compared.
"""


def _row_keys(refs: object) -> Counter[tuple[str, str, str]]:
    """Each row of raw *refs* by ``(name, logicalId, segmentLogicalId)``.

    The rows of a rebuilt name are left out.

    Row IDs are re-issued on a write, so they are no part of the key.
    """
    keys: Counter[tuple[str, str, str]] = Counter()
    if isinstance(refs, dict):
        for name in cast("dict[str, Any]", refs):
            if name in REBUILT_REF_NAMES:
                continue
            for row in _rows_named(refs, name):
                key = (
                    name,
                    str(row.get("logicalId")).lower(),
                    str(row.get("segmentLogicalId")).lower(),
                )
                keys[key] += 1
    return keys


def _rebuilt_rows(refs: object) -> int:
    """How many rows of raw *refs* are of a rebuilt name."""
    return sum(len(_rows_named(refs, name)) for name in REBUILT_REF_NAMES)


def _stable_refs(refs: object) -> dict[str, Any]:
    """Raw *refs* without a client-connector ref.

    That ref carries a login token that is new on every read.
    """
    if not isinstance(refs, dict):
        return {}
    return {
        name: rows
        for name, rows in cast("dict[str, Any]", refs).items()
        if "clientConnector" not in name
    }


def _check_state_holds_stored(
    label: str, state: ConfigState, stored: AnyStoredModule
) -> None:
    """After a save the state's module is the module as stored.

    The version, data and refs of a read made right after.
    """
    held = state.module(Name.DEVICE_SETTINGS)
    check_as_read(label, held, stored)
    ours, theirs = _stable_refs(held.raw_refs), _stable_refs(stored.raw_refs)
    differing = sorted(
        name for name in {*ours, *theirs} if ours.get(name) != theirs.get(name)
    )
    check(
        not differing,
        f"{label}: the refs held differ from a read made right after, at "
        f"{', '.join(differing)}",
    )


def _global_entry(raw: object) -> dict[str, Any] | None:
    """The segment entry of the Global Segment in a module's raw data."""
    if not isinstance(raw, dict):
        return None
    entries = cast("dict[str, Any]", raw).get("segments")
    for entry in cast("list[Any]", entries) if isinstance(entries, list) else []:
        if _segment_id(entry) == GLOBAL_SEGMENT_ID:
            return cast("dict[str, Any]", entry)
    return None


def _descriptor_of(entry: dict[str, Any] | None) -> tuple[str, str] | None:
    """The name and the logical ID of a segment entry's segment.

    Only where it has both as text.
    """
    descriptor = entry.get("segment") if entry is not None else None
    if not isinstance(descriptor, dict):
        return None
    fields = cast("dict[str, Any]", descriptor)
    name, logical_id = fields.get("name"), fields.get("segmentLogicalId")
    if isinstance(name, str) and isinstance(logical_id, str):
        return name, logical_id
    return None


def _primary_rows(refs: object, segment_logical_id: str) -> list[dict[str, Any]]:
    return [
        row
        for row in _rows_named(refs, PRIMARY_PROVIDER)
        if str(row.get("segmentLogicalId")).lower() == segment_logical_id.lower()
    ]


def _global_segment_without_provider(module: AnyStoredModule) -> bool:
    """A device settings module with refs read.

    Its Global Segment has a DNS section and no primary provider row.
    """
    if not isinstance(module, DeviceSettingsConfigurationModule):
        return False
    if not isinstance(module.raw_refs, dict):
        return False
    entry = _global_entry(module.raw_data)
    descriptor = _descriptor_of(entry)
    if entry is None or descriptor is None:
        return False
    section = entry.get("dns")
    return (
        isinstance(section, dict)
        and isinstance(cast("dict[str, Any]", section).get("primaryProvider"), dict)
        and not _primary_rows(module.raw_refs, descriptor[1])
    )


@dataclass(frozen=True)
class DnsTarget:
    """A marked profile's Global Segment, and a network service to set on it.

    The service is of type ``dns``.
    """

    profile_id: int
    segment: str
    segment_logical_id: str
    service: uuid.UUID


async def _dns_services(lab: Lab) -> list[EnterpriseServiceSummary]:
    request = GetEnterpriseServices()
    if lab.enterprise_id is not None:
        request.enterprise_id = lab.enterprise_id
    services = await lab.client.get_enterprise_service_summaries(request)
    return [service for service in services if service.type_ == "dns"]


async def _dns_target(lab: Lab) -> DnsTarget:
    """The target of a case that sets a DNS provider.

    Skip, saying what to set up, where there is none.

    The opt-in is checked first and the services are read next: the pick of a
    profile writes the module back to try it, so a case that is to skip for a
    missing service skips before it.
    """
    require_writes(run_of(lab.client))
    services = await _dns_services(lab)
    if not services:
        pytest.skip(
            "the enterprise has no network service of type dns: add one for "
            "this test to set as a provider"
        )
    profile_id = await lab.pick_writable_profile(
        Name.DEVICE_SETTINGS,
        _global_segment_without_provider,
        "whose Global Segment has a DNS section and no primary provider set",
    )
    module = await lab.profile_site(profile_id, Name.DEVICE_SETTINGS).read_present()
    descriptor = _descriptor_of(_global_entry(module.raw_data))
    check(descriptor is not None, "the Global Segment is gone from the profile")
    assert descriptor is not None
    return DnsTarget(profile_id, descriptor[0], descriptor[1], services[0].logical_id)


async def _set_primary_provider(
    pipeline: ConfigPipeline,
    state: ConfigState,
    target: DnsTarget,
    service: uuid.UUID | None,
) -> None:
    """Set the Global Segment's primary DNS provider to the service of this logical ID.

    Clear it with ``None``.
    """
    _ = await pipeline.apply(
        state,
        module=Name.DEVICE_SETTINGS.value,
        segment=target.segment,
        patch={
            "dns": {
                "primaryProvider": {
                    "service": None if service is None else str(service)
                }
            }
        },
    )


def _check_provider_change(
    label: str, plan: ModulePlan, *, added: bool, target: DnsTarget
) -> None:
    """The plan adds (or removes) the primary provider row of the Global Segment.

    It changes no other row.
    """
    changes = plan.ref_changes
    check(
        len(changes) == 1
        and changes[0].added == added
        and changes[0].name == PRIMARY_PROVIDER
        and changes[0].segment == target.segment,
        f"{label}: the plan lists {len(changes)} ref change(s), expected the "
        "primary provider row of the Global Segment "
        f"{'added' if added else 'removed'} alone",
    )


def _check_primary_row(
    label: str, stored: AnyStoredModule, target: DnsTarget, *, set_: bool
) -> None:
    """The stored refs hold the primary provider row of the Global Segment, or none.

    The row is for the service set.
    """
    rows = _primary_rows(stored.raw_refs, target.segment_logical_id)
    if not set_:
        check(not rows, f"{label}: the primary provider row is still stored")
        return
    check(
        len(rows) == 1
        and str(rows[0].get("logicalId")).lower() == str(target.service).lower(),
        f"{label}: {len(rows)} primary provider row(s) stored, expected the one "
        "of the service set",
    )


def _what(error: BaseException) -> str:
    """*error* as what holds no lab data.

    ``failure_text``, else the suite's own message of a failed check.
    """
    return failure_text(error) or str(error)


@asynccontextmanager
async def _provider_cleared(
    pipeline: ConfigPipeline, lab: Lab, target: DnsTarget, site: Site
) -> AsyncGenerator[None]:
    """However the block ends, clear the Global Segment's primary provider row.

    Clear it only if one is stored.

    It reads the module fresh and saves a fresh state, so it needs nothing the
    block held. A block that failed and a clearing that fails are both
    reported; the block's own failure is never hidden by the clearing's.
    """

    async def clear() -> None:
        stored = await site.read_present()
        if not _primary_rows(stored.raw_refs, target.segment_logical_id):
            return
        state = await pipeline.load_profile(
            target.profile_id, enterprise_id=lab.enterprise_id
        )
        await _set_primary_provider(pipeline, state, target, None)
        _ = await _save(pipeline, state)
        after = await site.read_present()
        check(
            not _primary_rows(after.raw_refs, target.segment_logical_id),
            "clearing the primary provider afterwards left its row stored",
        )

    try:
        yield
    except BaseException as case:
        try:
            await clear()
        except (Exception, pytest.fail.Exception) as cleanup:  # noqa: BLE001 - both are reported
            pytest.fail(
                f"{_what(case)}; and clearing the primary provider row afterwards "
                f"failed: {_what(cleanup)}; the lab may still hold the row",
                pytrace=False,
            )
        raise
    else:
        await clear()


@pytest.mark.live_write
async def test_set_and_clear_a_dns_provider(lab: Lab, pipeline: ConfigPipeline) -> None:
    target = await _dns_target(lab)
    site = lab.profile_site(target.profile_id, Name.DEVICE_SETTINGS)
    async with (
        restoring(site) as original,
        _provider_cleared(pipeline, lab, target, site),
    ):
        assert original is not None
        state = await pipeline.load_profile(
            target.profile_id, enterprise_id=lab.enterprise_id
        )

        await _set_primary_provider(pipeline, state, target, target.service)
        plan = _planned(state, Name.DEVICE_SETTINGS)
        _check_provider_change("set", plan, added=True, target=target)
        _ = await _save(pipeline, state)
        stored = await site.read_present()
        _check_state_holds_stored("set", state, stored)
        _check_primary_row("set", stored, target, set_=True)

        await _set_primary_provider(pipeline, state, target, None)
        plan = _planned(state, Name.DEVICE_SETTINGS)
        _check_provider_change("clear", plan, added=False, target=target)
        _ = await _save(pipeline, state)
        stored = await site.read_present()
        _check_state_holds_stored("clear", state, stored)
        _check_primary_row("clear", stored, target, set_=False)


def _raise_profile_lan_cost(state: ConfigState, by: int) -> Path:
    """Raise the OSPF cost of the profile's first LAN network by *by*.

    Return the path it changes.

    Assigned on the typed data, so that no override flag is set along with it.
    """
    network = state.profile.device_settings.lan.networks[0]
    network.cost += by
    return Path(("lan", "networks", Key(network.vlan_id), "cost"))


@pytest.mark.live_write
async def test_a_stale_version_with_refs_raises_before_sending(
    lab: Lab, pipeline: ConfigPipeline
) -> None:
    profile_id = await lab.pick_writable_profile(
        Name.DEVICE_SETTINGS,
        _device_settings_with_refs,
        "with refs, a segment and a LAN network",
    )
    site = lab.profile_site(profile_id, Name.DEVICE_SETTINGS)
    run = run_of(lab.client)
    async with restoring(site) as original:
        assert original is not None
        newer = await pipeline.load_profile(profile_id, enterprise_id=lab.enterprise_id)
        older = await pipeline.load_profile(profile_id, enterprise_id=lab.enterprise_id)
        kept = _raise_profile_lan_cost(newer, 1)
        _ = _raise_profile_lan_cost(older, 2)
        check(
            _planned(older, Name.DEVICE_SETTINGS).refs is not None,
            "the plan of the older state does not carry the refs",
        )

        # The second writer: the newer state saves first, through the guarded client.
        _ = await _save(pipeline, newer)
        written = run.sent
        with pytest.raises(ConfigConflictError) as caught:
            _ = await pipeline.save(older)
        error = caught.value
        check(
            not error.stated and not error.sent,
            f"the stale save raised {outcome(error)}, expected a refusal that "
            "is neither stated nor sent",
        )
        check(
            run.sent == written,
            f"the stale save sent {run.sent - written} write(s), expected none",
        )

        # The module holds the newer state's edit, and nothing of the older one.
        now = await site.read_present()
        check_paths(
            "stale save",
            "the module read back",
            changes_between(
                Name.DEVICE_SETTINGS, original.data, now.raw_data, DeviceSettingsData
            ),
            [kept],
        )
        check(error.module is not None, "the error carries no module")
        assert error.module is not None
        check_as_read("the error's module", error.module, now)


@pytest.mark.live_write
async def test_a_data_edit_keeps_every_row(
    lab: Lab, pipeline: ConfigPipeline, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every ref row as read is still stored after a data edit.

    The rows of the names the VCO rebuilds are the exception.

    The VCO deletes and rebuilds the hand-off gateway and controller objects
    and the rest of ``REBUILT_REF_NAMES`` on every such write, and a write is
    not compared at them; their rows are counted and printed apart.
    """
    profile_id = await lab.pick_writable_profile(
        Name.DEVICE_SETTINGS,
        _device_settings_with_refs,
        "with refs, a segment and a LAN network",
    )
    site = lab.profile_site(profile_id, Name.DEVICE_SETTINGS)
    async with restoring(site) as original:
        assert original is not None
        as_read = _row_keys(original.refs)
        state = await pipeline.load_profile(profile_id, enterprise_id=lab.enterprise_id)
        _ = _raise_profile_lan_cost(state, 1)
        _ = await _save(pipeline, state)

        stored = await site.read_present()
        record(
            capsys,
            "a data edit: rows of the names the VCO rebuilds: "
            f"{_rebuilt_rows(original.refs)} as read, "
            f"{_rebuilt_rows(stored.raw_refs)} stored",
        )
        _check_state_holds_stored("data edit", state, stored)
        gone = as_read - _row_keys(stored.raw_refs)
        check(
            not gone,
            f"a data edit lost {gone.total()} of {as_read.total()} ref row(s) as "
            f"read, of: {', '.join(sorted({name for name, _, _ in gone}))}",
        )


@pytest.mark.live_write
async def test_record_the_refs_a_write_returns(
    lab: Lab, pipeline: ConfigPipeline, capsys: pytest.CaptureFixture[str]
) -> None:
    """The fields of the ref rows the Orchestrator returns after a save that sends refs.

    For each declared name.

    A primary DNS provider is set on the Global Segment, so that the save adds a
    row of the five-field shape (``configurationId``, ``moduleId``,
    ``enterpriseObjectId``, ``ref``, and ``segmentObjectId`` for a per-segment
    ref) beside the rows as read. The run records, for each declared ref name,
    how many rows come back, which fields they have, and whether every field
    sent is among them, which only a live VCO can tell. Only ref names
    and field names are printed, never a value; the provider is cleared again.
    """
    target = await _dns_target(lab)
    site = lab.profile_site(target.profile_id, Name.DEVICE_SETTINGS)
    saved = "passed"
    async with restoring(site) as original:
        assert original is not None
        async with _provider_cleared(pipeline, lab, target, site):
            state = await pipeline.load_profile(
                target.profile_id, enterprise_id=lab.enterprise_id
            )
            await _set_primary_provider(pipeline, state, target, target.service)
            try:
                _ = await pipeline.save(state)
            except ModuleWriteError as error:
                saved = f"raised {failure_text(error)}"
            stored = await site.read_present()
            lines = [f"a save that sends refs: the save {saved}"]
            declared = declared_ref_names(DeviceSettingsData, ConfigContext.PROFILE)
            for name in sorted(declared):
                rows = _rows_named(stored.raw_refs, name)
                if not rows:
                    lines.append(f"  {name}: no row")
                    continue
                sent: frozenset[str] = SENT_ROW_FIELDS
                if name != SEGMENT_REF:
                    sent = sent | {"segmentObjectId"}
                fields = sorted({field for row in rows for field in row})
                kept = all(sent <= set(row) for row in rows)
                lines.append(
                    f"  {name}: {len(rows)} row(s), fields {', '.join(fields)}; "
                    f"every field sent is kept: {'yes' if kept else 'no'}"
                )
            record(capsys, "\n".join(lines))
        # Past the clearing, so that a save that failed has its row cleared first.
        check(saved == "passed", f"the save of a provider {saved}")


# ---------------------------------------------------------------------------
# 15. A segment of the enterprise assigned to a profile
# ---------------------------------------------------------------------------

SEGMENT_MODULES = (Name.DEVICE_SETTINGS, Name.QOS, Name.FIREWALL)
"""The three modules of a profile that hold its segments, device settings written
first.
"""

VERSION_READ = "configuration/getConfigurationModules"
"""The request that reads a module's ``version`` alone (``noData``)."""


@dataclass(frozen=True)
class SegmentTarget:
    """A marked profile, and an enterprise segment it lacks.

    None of the profile's three modules has the segment.
    """

    profile_id: int
    segment: SegmentIdentity


def _segment_ids_of(data: object) -> set[object]:
    """The ``segmentId`` of each segment entry of raw module *data*."""
    entries = cast("dict[str, object]", data).get("segments")
    return {_segment_id(entry) for entry in cast("list[object]", entries or [])}


def _lacked_segment(state: ConfigState) -> SegmentIdentity | None:
    """The first enterprise segment, by ID, that the profile's three modules lack.

    ``None`` where there is none or a module is missing.
    """
    try:
        held = [
            _segment_ids_of(state.module(name).raw_data) for name in SEGMENT_MODULES
        ]
    except ValueError:
        return None
    resolver = state.resolvers.get_resolver(SegmentResolver)
    return next(
        (
            identity
            for identity in sorted(resolver.identities(), key=lambda i: i.segment_id)
            if all(identity.segment_id not in ids for ids in held)
        ),
        None,
    )


async def _segment_target(
    lab: Lab, pipeline: ConfigPipeline, *, with_edges: bool
) -> SegmentTarget:
    """The first marked profile that lacks an enterprise segment.

    It takes a write of each of its three modules.

    With *with_edges*, the profile must carry the mark for edges assigned to it
    as well. Skip where there is none to use, fail where each one that lacks a
    segment refuses a write of its own data. The opt-in is checked first, and
    the modules are tried last: trying a module writes it back.
    """
    require_writes(run_of(lab.client))
    marked = [
        entry.profile_id
        for entry in lab.profiles
        if entry.target and (not with_edges or WITH_EDGES_MARK in entry.marks)
    ]
    if not marked:
        pytest.skip(
            f"no profile is marked {TARGET_MARK}"
            + (f" and {WITH_EDGES_MARK}" if with_edges else "")
        )
    refusals: list[str] = []
    for profile_id in marked:
        state = await pipeline.load_profile(profile_id, enterprise_id=lab.enterprise_id)
        segment = _lacked_segment(state)
        if segment is None:
            continue
        for name in SEGMENT_MODULES:
            refusal = await own_data_refusal(lab.profile_site(profile_id, name))
            if refusal is not None:
                refusals.append(f"{name.value}: {refusal}")
                break
        else:
            return SegmentTarget(profile_id, segment)
    if refusals:
        pytest.fail(
            f"none of the {len(refusals)} marked profile(s) that lack an enterprise "
            f"segment takes a write of its own modules ({'; '.join(refusals)}): the "
            "modules are not valid as they stand, or the update path is broken",
            pytrace=False,
        )
    pytest.skip(
        "no marked profile lacks a segment of the enterprise in all three of its "
        "device settings, QoS and firewall modules"
    )


def _version_reads(answers: Sequence[Answer]) -> int:
    """How many of the recorded answers are to a module's version read.

    That request reads a module's version alone.
    """
    return sum(answer.method == VERSION_READ for answer in answers)


def _update_of(writes: Writes, module: AnyStoredModule) -> UpdateConfigurationModule:
    """The one update the test sent to *module*."""
    sent = [u for u in writes.updates if u.configuration_module_id == module.id_]
    check(len(sent) == 1, f"{len(sent)} update(s) sent to the {module.name} module")
    return sent[0]


@pytest.mark.live_write
async def test_assign_a_segment_and_read_it_back(
    lab: Lab,
    pipeline: ConfigPipeline,
    writes: Writes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A segment assigned to a profile is three writes, device settings first.

    It reads back as the state holds it.

    The modules are restored QoS and firewall first, device settings last, as
    the restore of device settings sends its refs as read. Three reads of a
    module's version are made: device settings reads its own, because it sends
    refs, and QoS and firewall read theirs because they follow a profile's
    device settings write and are rebased onto what it changed.
    """
    target = await _segment_target(lab, pipeline, with_edges=False)
    sites = {
        name: lab.profile_site(target.profile_id, name) for name in SEGMENT_MODULES
    }
    async with (
        restoring(sites[Name.DEVICE_SETTINGS]) as original,
        restoring(sites[Name.QOS]),
        restoring(sites[Name.FIREWALL]),
    ):
        assert original is not None
        state = await pipeline.load_profile(
            target.profile_id, enterprise_id=lab.enterprise_id
        )
        writes.updates.clear()  # the preflight writes were not the save under test
        writes.inserts.clear()
        _ = await pipeline.assign_segment(state, target.segment.name)
        planned = {plan.name for plan in state.plan().changed}
        check(
            planned == {name.value for name in SEGMENT_MODULES},
            "the assignment does not plan a write of each of the three modules",
        )
        answers = record_answers(lab.client, monkeypatch)
        result = await _save(pipeline, state)

        modules = {name: state.module(name) for name in SEGMENT_MODULES}
        check(
            len(writes.updates) == 3 and not writes.inserts,
            f"{len(writes.updates)} update(s) and {len(writes.inserts)} insert(s) "
            "sent, expected three updates",
        )
        check(
            writes.updates[0].configuration_module_id
            == modules[Name.DEVICE_SETTINGS].id_,
            "the first write was not to the device settings module",
        )
        check(
            result.modules_updated[0] == Name.DEVICE_SETTINGS
            and set(result.modules_updated) == set(SEGMENT_MODULES)
            and not result.modules_inserted,
            f"updated {result.modules_updated}, inserted {result.modules_inserted}",
        )
        for name in SEGMENT_MODULES:
            sent = _update_of(writes, modules[name])
            check(
                target.segment.segment_id in _segment_ids_of(sent.update.data),
                f"the {name.value} write does not carry the new segment",
            )
        rows = _rows_named(writes.updates[0].update.refs, SEGMENT_REF)
        check(
            len(rows) == len(_rows_named(original.refs, SEGMENT_REF)) + 1
            and any(
                row.get("enterpriseObjectId") == target.segment.object_id
                for row in rows
            ),
            f"the device settings write carries {len(rows)} {SEGMENT_REF} row(s), "
            "expected the rows as read and one for the new segment",
        )
        check(
            _version_reads(answers) == 3,
            f"{_version_reads(answers)} version read(s) made by the save, expected "
            "three",
        )

        fresh = await pipeline.load_profile(
            target.profile_id, enterprise_id=lab.enterprise_id
        )
        check(not fresh.plan().changed, "a profile read after the save plans a change")
        for name in SEGMENT_MODULES:
            check_as_read(f"assign {name.value}", modules[name], fresh.module(name))
        _check_state_holds_stored("assign", state, fresh.module(Name.DEVICE_SETTINGS))


@pytest.mark.live_write
async def test_record_versions_around_the_device_settings_write(
    lab: Lab,
    pipeline: ConfigPipeline,
    writes: Writes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Whether the device settings write of an assignment changes other versions.

    The versions are those of the QoS and firewall modules.

    The version of each of the two is read before the save, and the versions
    their writes were sent with are taken from the requests. Only whether the
    two are the same is said, never a version. The profile is one that edges
    are assigned to, where a change of version is most likely to be seen.
    """
    target = await _segment_target(lab, pipeline, with_edges=True)
    sites = {
        name: lab.profile_site(target.profile_id, name) for name in SEGMENT_MODULES
    }
    async with (
        restoring(sites[Name.DEVICE_SETTINGS]),
        restoring(sites[Name.QOS]),
        restoring(sites[Name.FIREWALL]),
    ):
        state = await pipeline.load_profile(
            target.profile_id, enterprise_id=lab.enterprise_id
        )
        later = (Name.QOS, Name.FIREWALL)
        before = {
            name: expect(
                (await sites[name].read_present()).version,
                str,
                f"the {name.value} version read before the save",
            )
            for name in later
        }
        writes.updates.clear()  # the preflight writes were not the save under test
        writes.inserts.clear()
        _ = await pipeline.assign_segment(state, target.segment.name)
        _ = await _save(pipeline, state)

        lines = ["the versions of the modules written after device settings:"]
        for name in later:
            sent = expect(
                _update_of(writes, state.module(name)).version,
                str,
                f"the version the {name.value} write was sent with",
            )
            lines.append(
                f"  {name.value}: sent with "
                + (
                    "the version read before the save"
                    if sent == before[name]
                    else "another version than the one read before the save"
                )
            )
        record(capsys, "\n".join(lines))


# ---------------------------------------------------------------------------
# 16. Hub roles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HubCase:
    """A marked profile's segment that lacks the hub in all three lists.

    It comes with the flags of its VPN as read.
    """

    profile_id: int
    segment: str
    hub: HubTarget
    vpn_enabled: bool
    hub_list_enabled: bool


def _vpn_lists(vpn: dict[str, Any]) -> dict[str, list[Any]]:
    """The three role lists of a segment's raw ``vpn``.

    By the name they are patched under: ``hub``, ``bridge`` and ``backhaul``.
    """

    def listed(section: object, key: str) -> list[Any]:
        held = (
            cast("dict[str, Any]", section).get(key)
            if isinstance(section, dict)
            else None
        )
        return cast("list[Any]", held) if isinstance(held, list) else []

    backhaul = vpn.get("backHaulEdges")
    return {
        "hub": listed(vpn.get("edgeToEdgeHub"), "vpnHubs"),
        "bridge": listed(vpn.get("edgeToEdgeDetail"), "vpnHubs"),
        "backhaul": cast("list[Any]", backhaul) if isinstance(backhaul, list) else [],
    }


def _holding(vpn: dict[str, Any], hub: HubTarget) -> set[str]:
    """The role lists of *vpn* that hold *hub*, by their entry's logical ID."""
    return {
        role
        for role, entries in _vpn_lists(vpn).items()
        if any(
            isinstance(entry, dict)
            and str(cast("dict[str, Any]", entry).get("logicalId")).lower()
            == str(hub.logical_id)
            for entry in entries
        )
    }


def _segment_entries(raw: object) -> list[tuple[str, dict[str, Any]]]:
    """The ``(name, entry)`` of each segment entry of raw module data."""
    entries = (
        cast("dict[str, Any]", raw).get("segments") if isinstance(raw, dict) else None
    )
    found: list[tuple[str, dict[str, Any]]] = []
    for entry in cast("list[Any]", entries) if isinstance(entries, list) else []:
        held = (
            cast("dict[str, Any]", entry).get("segment")
            if isinstance(entry, dict)
            else None
        )
        name = (
            cast("dict[str, Any]", held).get("name") if isinstance(held, dict) else None
        )
        if isinstance(name, str):
            found.append((name, cast("dict[str, Any]", entry)))
    return found


def _free_segment(raw: object, hub: HubTarget) -> tuple[str, bool, bool] | None:
    """The first segment of device settings *raw* whose three lists lack *hub*.

    It comes with its ``vpn.enabled`` and hub list ``enabled`` as read.
    """
    for name, entry in _segment_entries(raw):
        vpn = entry.get("vpn")
        if not isinstance(vpn, dict):
            continue
        vpn = cast("dict[str, Any]", vpn)
        if _holding(vpn, hub):
            continue
        section = vpn.get("edgeToEdgeHub")
        hub_list = (
            cast("dict[str, Any]", section).get("enabled")
            if isinstance(section, dict)
            else None
        )
        return name, vpn.get("enabled") is True, hub_list is True
    return None


def _qos_rules(raw: object, segment: str) -> list[Any]:
    """The rules of the QoS segment *segment* in raw module data."""
    for name, entry in _segment_entries(raw):
        rules = entry.get("rules")
        if name == segment and isinstance(rules, list):
            return cast("list[Any]", rules)
    return []


async def _hub_case(lab: Lab, hub: HubTarget, *, with_rule: bool) -> HubCase:
    """The first marked profile with a segment that lacks the hub.

    With *with_rule*, the segment has a QoS rule; the profile's modules take a write.

    Skip where there is none, fail where each refuses a write of its own
    data. The modules are tried last: trying one writes it back.
    """
    names = (Name.DEVICE_SETTINGS, Name.QOS) if with_rule else (Name.DEVICE_SETTINGS,)
    marked = [entry.profile_id for entry in lab.profiles if entry.target]
    if not marked:
        pytest.skip(f"no profile is marked {TARGET_MARK}")
    refusals: list[str] = []
    for profile_id in marked:
        modules = {
            name: await lab.profile_site(profile_id, name).read() for name in names
        }
        device_settings = modules[Name.DEVICE_SETTINGS]
        if (
            any(module is None for module in modules.values())
            or device_settings is None
        ):
            continue
        free = _free_segment(device_settings.raw_data, hub)
        if free is None:
            continue
        qos = modules.get(Name.QOS)
        if with_rule and (qos is None or not _qos_rules(qos.raw_data, free[0])):
            continue
        for name in names:
            refusal = await own_data_refusal(lab.profile_site(profile_id, name))
            if refusal is not None:
                refusals.append(f"{name.value}: {refusal}")
                break
        else:
            return HubCase(profile_id, free[0], hub, free[1], free[2])
    if refusals:
        pytest.fail(
            f"none of the {len(refusals)} marked profile(s) with a segment that "
            f"lacks the hub takes a write of its own modules ({'; '.join(refusals)}): "
            "the modules are not valid as they stand, or the update path is broken",
            pytrace=False,
        )
    pytest.skip(
        "no marked profile has a segment whose Branch-to-Hub, bridge and backhaul "
        "lists lack the hub" + (" and that has a QoS rule" if with_rule else "")
    )


def _add_hub_patch(case: HubCase) -> dict[str, Any]:
    """The patch that adds the hub to the segment's Branch-to-Hub and backhaul lists.

    It comes with the flags the UI's rules need on.
    """
    entry = {"+": add({"logicalId": str(case.hub.logical_id)})}
    return {
        "segments": {
            case.segment: {
                "vpn": {
                    "enabled": True,
                    "edgeToEdgeHub": {"enabled": True, "vpnHubs": entry},
                    "backHaulEdges": entry,
                }
            }
        }
    }


def _remove_hub_patch(case: HubCase, holding: set[str]) -> dict[str, Any]:
    """The patch that takes the hub out of the lists in *holding*.

    It puts the flags back as read.
    """
    key = str(case.hub.logical_id)
    vpn: dict[str, Any] = {
        "enabled": case.vpn_enabled,
        "edgeToEdgeHub": {"enabled": case.hub_list_enabled},
    }
    if "hub" in holding:
        vpn["edgeToEdgeHub"]["vpnHubs"] = {key: delete()}
    if "bridge" in holding:
        vpn["edgeToEdgeDetail"] = {"vpnHubs": {key: delete()}}
    if "backhaul" in holding:
        vpn["backHaulEdges"] = {key: delete()}
    return {"segments": {case.segment: {"vpn": vpn}}}


def _segment_vpn(module: AnyStoredModule, case: HubCase) -> dict[str, Any]:
    """The raw ``vpn`` of the case's segment in a device settings module.

    Empty where the segment is gone.
    """
    for name, entry in _segment_entries(module.raw_data):
        vpn = entry.get("vpn")
        if name == case.segment and isinstance(vpn, dict):
            return cast("dict[str, Any]", vpn)
    return {}


def _steps(state: ConfigState) -> list[tuple[str, str]]:
    """The ``(action, role)`` of each role step the state's plan has."""
    return [(step.action, step.role) for step in state.plan().steps]


def _hub_rows(refs: object, hub: HubTarget) -> list[dict[str, Any]]:
    """The device settings rows of *hub*.

    An Edge's row holds its logical ID in ``data``, a cluster's in the row.
    """
    found: list[dict[str, Any]] = []
    for row in _rows_named(
        refs, EDGE_HUB_REF if hub.kind == "edge" else CLUSTER_HUB_REF
    ):
        held = row.get("data")
        logical_id = (
            cast("dict[str, Any]", held).get("logicalId")
            if hub.kind == "edge" and isinstance(held, dict)
            else row.get("logicalId")
        )
        if str(logical_id).lower() == str(hub.logical_id):
            found.append(row)
    return found


async def _add_hub(  # noqa: PLR0913, PLR0917 (existing: split it when it next changes)
    pipeline: ConfigPipeline,
    lab: Lab,
    case: HubCase,
    writes: Writes,
    state: ConfigState,
    site: Site,
) -> AnyStoredModule:
    """Add the hub to the segment's lists and save: two steps and one write.

    Return the module as stored.
    """
    run = run_of(lab.client)
    _ = await pipeline.apply(
        state, module="device_settings", patch=_add_hub_patch(case)
    )
    check(
        _steps(state) == [("enable", "hub"), ("enable", "backhaul")],
        (
            f"the plan has the steps {_steps(state)}, "
            "expected enable hub then enable backhaul"
        ),
    )
    dry = await _save(pipeline, state, dry_run=True)
    check(
        [outcome.status for outcome in dry.steps] == ["would_send"] * 2,
        "a dry run does not say that both steps would be sent",
    )
    writes.updates.clear()
    sent = run.sent
    result = await _save(pipeline, state)
    check(
        [outcome.status for outcome in result.steps] == ["sent"] * 2,
        "the save did not send both steps",
    )
    check(
        len(writes.updates) == 1 and run.sent == sent + 3,
        f"{len(writes.updates)} update(s) and {run.sent - sent} write(s) sent, "
        "expected two role calls and one update",
    )
    return await site.read_present()


async def _remove_hub(  # noqa: PLR0913, PLR0917 (existing: split it when it next changes)
    pipeline: ConfigPipeline,
    lab: Lab,
    case: HubCase,
    writes: Writes,
    state: ConfigState,
    site: Site,
) -> AnyStoredModule:
    """Take the hub out of the segment's lists and save: one disable and one write.

    Return the module as stored.
    """
    run = run_of(lab.client)
    held = _holding(_segment_vpn(state.module(Name.DEVICE_SETTINGS), case), case.hub)
    _ = await pipeline.apply(
        state, module="device_settings", patch=_remove_hub_patch(case, held)
    )
    check(
        _steps(state) == [("disable", "hub")],
        f"the plan has the steps {_steps(state)}, expected one disable of the hub",
    )
    writes.updates.clear()
    sent = run.sent
    result = await _save(pipeline, state)
    check(
        [outcome.status for outcome in result.steps] == ["sent"],
        "the save did not send the disable",
    )
    check(
        len(writes.updates) == 1 and run.sent == sent + 2,
        f"{len(writes.updates)} update(s) and {run.sent - sent} write(s) sent, "
        "expected one role call and one update",
    )
    return await site.read_present()


async def _clear_hub(pipeline: ConfigPipeline, lab: Lab, case: HubCase) -> None:
    """However a case ended, take the hub out of the segment's lists.

    Send its disable.

    It reads the profile fresh and saves a fresh state, so it needs nothing
    the case held. A hub in no list may still have its roles set (a save that
    stopped after its steps): it is added to the lists to find out, by a dry
    run, and when a step is in place the save is made, which sends the rest,
    so that the removal has steps to derive.
    """
    state = await pipeline.load_profile(
        case.profile_id, enterprise_id=lab.enterprise_id
    )
    held = _holding(_segment_vpn(state.module(Name.DEVICE_SETTINGS), case), case.hub)
    if not held:
        _ = await pipeline.apply(
            state, module="device_settings", patch=_add_hub_patch(case)
        )
        dry = await _save(pipeline, state, dry_run=True)
        if not any(outcome.status == "in_place" for outcome in dry.steps):
            return
        _ = await _save(pipeline, state)
        held = _holding(
            _segment_vpn(state.module(Name.DEVICE_SETTINGS), case), case.hub
        )
    _ = await pipeline.apply(
        state, module="device_settings", patch=_remove_hub_patch(case, held)
    )
    _ = await _save(pipeline, state)
    after = await lab.profile_site(case.profile_id, Name.DEVICE_SETTINGS).read_present()
    check(
        not _holding(_segment_vpn(after, case), case.hub),
        "removing the hub afterwards left it in a list",
    )


@asynccontextmanager
async def _cleaned_up(
    what: str, clear: Callable[[], Awaitable[None]]
) -> AsyncGenerator[None]:
    """However the block ends, run *clear*.

    The block's own failure is never hidden by it, and a clearing that fails is reported
    too.
    """
    try:
        yield
    except BaseException as case:
        try:
            await clear()
        except (Exception, pytest.fail.Exception) as cleanup:  # noqa: BLE001 - both are reported
            pytest.fail(
                f"{_what(case)}; and {what} afterwards failed: {_what(cleanup)}; "
                "the lab may still hold it",
                pytrace=False,
            )
        raise
    else:
        await clear()


@pytest.mark.live_write
async def test_add_a_hub_then_remove_it(
    lab: Lab, pipeline: ConfigPipeline, writes: Writes
) -> None:
    """A hub added to a segment's Branch-to-Hub and backhaul lists, then removed.

    Added, it is two steps and one write; removed, one disable and one write.

    A read after each save holds the row and the lists, then neither, and the
    profile is restored as read. The restore of the module does not undo a
    role call, so the hub is removed in a ``finally`` too.
    """
    hub = await admit_hub(lab.client, lab.enterprise_id)
    case = await _hub_case(lab, hub, with_rule=False)
    site = lab.profile_site(case.profile_id, Name.DEVICE_SETTINGS)
    async with (
        restoring(site) as original,
        _cleaned_up("removing the hub", lambda: _clear_hub(pipeline, lab, case)),
    ):
        assert original is not None
        rows_before = len(_hub_rows(original.refs, hub))
        state = await pipeline.load_profile(
            case.profile_id, enterprise_id=lab.enterprise_id
        )

        stored = await _add_hub(pipeline, lab, case, writes, state, site)
        check(
            _holding(_segment_vpn(stored, case), hub) == {"hub", "backhaul"},
            "a read after the save does not show the hub in the Branch-to-Hub and "
            "backhaul lists alone",
        )
        check(
            len(_hub_rows(stored.raw_refs, hub)) == rows_before + 1,
            "a read after the save does not hold the one new row of the hub",
        )
        ref = EDGE_HUB_REF if hub.kind == "edge" else CLUSTER_HUB_REF
        check(
            len(_rows_named(writes.updates[0].update.refs, ref))
            == len(_rows_named(original.refs, ref)) + 1,
            "the write does not carry the rows as read and the row of the hub",
        )
        _check_state_holds_stored("add hub", state, stored)

        stored = await _remove_hub(pipeline, lab, case, writes, state, site)
        check(
            not _holding(_segment_vpn(stored, case), hub),
            "a read after the removal still shows the hub in a list",
        )
        check(
            len(_hub_rows(stored.raw_refs, hub)) == rows_before,
            "a read after the removal holds a row of the hub",
        )
        _check_state_holds_stored("remove hub", state, stored)
        fresh = await pipeline.load_profile(
            case.profile_id, enterprise_id=lab.enterprise_id
        )
        check(
            not fresh.plan().changed and not fresh.plan().steps,
            "a profile read after the removal plans a change",
        )
        check(
            _segment_vpn(stored, case).get("enabled") is case.vpn_enabled,
            "the flags of the segment's VPN were not put back as read",
        )


def _rule_patch(case: HubCase, name: str, change: object) -> dict[str, Any]:
    return {"segments": {case.segment: {"rules": {name: change}}}}


def _rule_uses_hub(raw: object, case: HubCase, name: str) -> bool:
    """Whether the rule *name* of the case's QoS segment holds the hub.

    It holds it as a backhaul hub.
    """
    for rule in _qos_rules(raw, case.segment):
        if (
            not isinstance(rule, dict)
            or cast("dict[str, Any]", rule).get("name") != name
        ):
            continue
        action = cast("dict[str, Any]", rule).get("action")
        route = (
            cast("dict[str, Any]", action).get("edge2EdgeRouteAction")
            if isinstance(action, dict)
            else None
        )
        config = (
            cast("dict[str, Any]", route).get("routeCfg")
            if isinstance(route, dict)
            else None
        )
        entries = (
            cast("dict[str, Any]", config).get("backhaulEdges")
            if isinstance(config, dict)
            else None
        )
        return any(
            isinstance(entry, dict)
            and str(cast("dict[str, Any]", entry).get("edgeLogicalId")).lower()
            == str(case.hub.logical_id)
            for entry in (
                cast("list[Any]", entries) if isinstance(entries, list) else []
            )
        )
    return False


def _has_rule(raw: object, case: HubCase, name: str) -> bool:
    return any(
        isinstance(rule, dict) and cast("dict[str, Any]", rule).get("name") == name
        for rule in _qos_rules(raw, case.segment)
    )


async def _clear_rule(
    pipeline: ConfigPipeline, lab: Lab, case: HubCase, name: str
) -> None:
    """However a case ended, delete the rule it added to the QoS segment.

    Delete it only if one is stored.
    """
    site = lab.profile_site(case.profile_id, Name.QOS)
    if not _has_rule((await site.read_present()).raw_data, case, name):
        return
    state = await pipeline.load_profile(
        case.profile_id, enterprise_id=lab.enterprise_id
    )
    _ = await pipeline.apply(
        state, module="qos", patch=_rule_patch(case, name, delete())
    )
    _ = await _save(pipeline, state)
    check(
        not _has_rule((await site.read_present()).raw_data, case, name),
        "deleting the rule afterwards left it stored",
    )


@pytest.mark.live_write
async def test_a_rule_backhauls_to_the_hub(
    lab: Lab, pipeline: ConfigPipeline, writes: Writes
) -> None:
    """With the hub saved, a QoS rule that names it as a backhaul hub has its row.

    The row's object is the hub's service.

    The rule is a copy of a rule of the segment. It is deleted, then the hub is
    removed, the order a caller must follow: a hub that rules use is removed
    last. Both are also done in a ``finally``.
    """
    hub = await admit_hub(lab.client, lab.enterprise_id)
    case = await _hub_case(lab, hub, with_rule=True)
    sites = {
        name: lab.profile_site(case.profile_id, name)
        for name in (Name.DEVICE_SETTINGS, Name.QOS)
    }
    rule = synthetic_name()
    async with (
        restoring(sites[Name.DEVICE_SETTINGS]),
        restoring(sites[Name.QOS]) as qos_original,
        _cleaned_up("removing the hub", lambda: _clear_hub(pipeline, lab, case)),
        _cleaned_up(
            "deleting the rule", lambda: _clear_rule(pipeline, lab, case, rule)
        ),
    ):
        assert qos_original is not None
        state = await pipeline.load_profile(
            case.profile_id, enterprise_id=lab.enterprise_id
        )
        stored = await _add_hub(
            pipeline, lab, case, writes, state, sites[Name.DEVICE_SETTINGS]
        )
        (row,) = _hub_rows(stored.raw_refs, hub)
        service_id = row.get("enterpriseObjectId")
        check(
            isinstance(service_id, int),
            "the hub's row in the device settings refs names no service",
        )

        # A state read now knows the hub's service for the segment: the rule is
        # added to it, and the hub is named in the rule.
        state = await pipeline.load_profile(
            case.profile_id, enterprise_id=lab.enterprise_id
        )
        segment = next(
            s for s in state.profile.qos.segments if s.segment.name == case.segment
        )
        added = {
            **expect(
                segment.rules[0].model_dump(
                    mode="json", by_alias=False, context={"compact": True}
                ),
                dict,
                "a rule",
            ),
            "name": rule,
        }
        added.pop("rule_logical_id", None)
        _ = await pipeline.apply(
            state, module="qos", patch=_rule_patch(case, rule, add(added))
        )
        _ = await pipeline.apply(
            state,
            module="qos",
            patch=_rule_patch(
                case,
                rule,
                {
                    "action": {
                        "edge2EdgeRouteAction": {
                            "routeCfg": {
                                "backhaulEdges": {
                                    "+": add({"edgeLogicalId": str(hub.logical_id)})
                                }
                            }
                        }
                    }
                },
            ),
        )
        writes.updates.clear()
        result = await _save(pipeline, state)
        check(
            result.modules_updated == [Name.QOS]
            and not result.steps
            and len(writes.updates) == 1,
            f"saved {result.modules_updated} with {len(result.steps)} step(s) and "
            f"{len(writes.updates)} update(s), expected the QoS module alone",
        )
        sent = _rows_named(writes.updates[0].update.refs, BACKHAUL_REF)
        check(
            len(sent) == len(_rows_named(qos_original.refs, BACKHAUL_REF)) + 1
            and any(r.get("enterpriseObjectId") == service_id for r in sent),
            "the QoS write does not carry the rows as read and the row whose "
            "object is the hub's service",
        )
        qos = await sites[Name.QOS].read_present()
        check(
            _rule_uses_hub(qos.raw_data, case, rule)
            and any(
                r.get("enterpriseObjectId") == service_id
                for r in _rows_named(qos.raw_refs, BACKHAUL_REF)
            ),
            "a read after the save does not hold the rule with the hub and the row "
            "whose object is the hub's service",
        )

        # The rule's use goes first, then the hub.
        _ = await pipeline.apply(
            state, module="qos", patch=_rule_patch(case, rule, delete())
        )
        _ = await _save(pipeline, state)
        qos = await sites[Name.QOS].read_present()
        check(
            not _has_rule(qos.raw_data, case, rule)
            and not any(
                r.get("enterpriseObjectId") == service_id
                for r in _rows_named(qos.raw_refs, BACKHAUL_REF)
            ),
            "a read after deleting the rule still holds it or its backhaul row",
        )
        state = await pipeline.load_profile(
            case.profile_id, enterprise_id=lab.enterprise_id
        )
        stored = await _remove_hub(
            pipeline, lab, case, writes, state, sites[Name.DEVICE_SETTINGS]
        )
        check(
            not _holding(_segment_vpn(stored, case), hub)
            and not _hub_rows(stored.raw_refs, hub),
            "a read after the removal still holds the hub",
        )
