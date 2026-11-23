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

Requires VCO_HOST and VCO_TOKEN (VCO_ENTERPRISE_ID if the token needs one), and
to write, PYVELO_IT_WRITE_HOST and PYVELO_IT_WRITE_ENTERPRISE_ID: see
``live_support``.

Run:
    uv run --env-file .env pytest -m integration packages/pyvelo-intent/tests/integration
"""

from __future__ import annotations

import copy
import uuid
from collections import Counter
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, cast

import pytest
from live_support import (
    SEGMENT_DRIFT_KEY,
    SERVER_ASSIGNED_KEYS,
    DataRefused,
    EdgeLayout,
    Lab,
    Site,
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
from pyvelo.client.models.configuration import (
    DeviceSettingsData,
    FirewallData,
    QosData,
    WanData,
)
from pyvelo.client.models.configuration.device_settings.segment import StaticRoute
from pyvelo.client.models.configuration.qos.qoe import QoeSettings
from pyvelo.client.models.configuration.qos.root import ServiceRateLimit
from pyvelo.client.models.configuration.ref_owners import declared_ref_names
from pyvelo.client.module_write import ModulePlan
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    DeviceSettingsConfigurationModule,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
    InsertConfigurationModule,
    QosConfigurationModule,
    UpdateConfigurationModule,
    WanConfigurationModule,
)
from pyvelo.client.schemas.edge import GetEdgeConfigurationStack
from pyvelo.client.schemas.enterprise import (
    EnterpriseServiceSummary,
    GetEnterpriseServices,
)
from pyvelo.core import ConfigContext, VcoModel
from pyvelo.core.rebase import Key, Path, RawChange, raw_changes
from pyvelo.core.response_check import (
    PathPattern,
    ServerChange,
    matches,
)
from pyvelo_intent import ConfigPipeline, ConfigState, PatchError, SaveResult, add

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@dataclass
class Writes:
    """The write requests a test sent through the client."""

    updates: list[UpdateConfigurationModule]
    inserts: list[InsertConfigurationModule]


class WriteRefused(AssertionError):
    """A test that must send nothing tried to write."""


@pytest.fixture
def refused_writes(vco_client: VcoClient, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Refuse every write to the client, and list the attempts: nothing is sent."""
    attempts: list[str] = []

    async def refuse_update(
        request: UpdateConfigurationModule, *, level: ConfigContext | None = None
    ) -> None:
        attempts.append("update")
        raise WriteRefused

    async def refuse_insert(
        request: InsertConfigurationModule, *, level: ConfigContext | None = None
    ) -> Any:
        attempts.append("insert")
        raise WriteRefused

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
    ) -> AnyConfigurationModule | None:
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


async def _save(pipeline: ConfigPipeline, state: ConfigState) -> SaveResult:
    """``pipeline.save``; a write error fails the test with ``outcome`` alone."""
    try:
        return await pipeline.save(state)
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


def _raw(module: AnyConfigurationModule) -> dict[str, Any]:
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
    except WriteRefused:
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


def _profile_rule_source(module: AnyConfigurationModule) -> bool:
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


def _profile_has_qoe(module: AnyConfigurationModule) -> bool:
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
    """A segment entry that holds nothing: its descriptor and routes with only empty lists.

    That is what a placeholder is (``routeMapsVersion``, which the Orchestrator
    adds on a write, does not count). Taking such an entry out of a module and
    putting it back loses no routing, BGP, OSPF, NAT or any other content.
    """
    if not isinstance(entry, dict):
        return False
    fields = cast(dict[str, object], entry)
    if set(fields) - {SEGMENT_DRIFT_KEY} != {"segment", "routes"}:
        return False
    routes = fields["routes"]
    return isinstance(routes, dict) and all(
        isinstance(value, list) and not value
        for value in cast(dict[str, object], routes).values()
    )


def _empty_shared_segments(
    edge: dict[str, Any], profile: dict[str, Any]
) -> list[object]:
    """The IDs of the segments, other than the Global one, that both layers have and
    whose entry on the edge is empty (``_is_empty_entry``)."""
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
    """``_reconcile``; a refusal by the Orchestrator fails the test, by its codes alone."""
    refused: str | None = None
    try:
        await _reconcile(lab, pipeline, case, layout)
    except VcoOperationError as exc:
        refused = refusal_codes(exc)
    check(refused is None, f"the Orchestrator refused the write: {refused}")


@pytest.mark.live_write
async def test_missing_device_settings_segment_is_added_as_a_placeholder_the_orchestrator_accepts(
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
        except DataRefused as exc:
            refused = refusal_codes(exc)
        if refused is not None:
            pytest.skip(
                f"the Orchestrator takes no module without that segment entry: {refused}"
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
        state.edge.qos.service_rate_limit = ServiceRateLimit.model_validate(
            {"enabled": False, "inputType": "percent", "value": 1}
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
            state.edge.qos.service_rate_limit, ServiceRateLimit, "the rate limit"
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


def _firewall_that_comes_back_whole(module: AnyConfigurationModule) -> bool:
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
        state.edge.firewall.syslog_forwarding = True  # forward logs; none are collected
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


def _wan_link(module: AnyConfigurationModule) -> bool:
    """Whether *module* is a WAN module whose first link has a key (an internal ID)."""
    if not isinstance(module, WanConfigurationModule):
        return False
    links = module.data.links
    return links is not MISSING and bool(links) and links[0].internal_id is not MISSING


VOICE_RED = Path(("qoe", "voice", "latency", "red"))
VIDEO_RED = Path(("qoe", "video", "latency", "red"))


def _qoe_red_thresholds(module: AnyConfigurationModule) -> bool:
    """Whether *module* is a QoS module whose raw data has the red latency thresholds of voice and video."""
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


def _device_settings_with_refs(module: AnyConfigurationModule) -> bool:
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


def _firewall_segment_with_letters(module: AnyConfigurationModule) -> bool:
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
    """The key of the state's first WAN link: its internal ID and address family (link names repeat)."""
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


def _qos_rule_source(module: AnyConfigurationModule) -> bool:
    return isinstance(module, QosConfigurationModule) and any(
        rule.match.ip_version == "IPv4v6"
        for segment in module.data.segments
        for rule in segment.rules
    )


def _lan_network(module: AnyConfigurationModule) -> bool:
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
    suitable: Callable[[AnyConfigurationModule], bool]
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
    read_back: bool,
) -> None:
    """A stale save of an edge module leaves the state holding the module as stored, to save again.

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
    """The segment name and the route of the first IPv6 static route with a prefix of 48 or longer."""
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


def _v6_route_source(module: AnyConfigurationModule) -> bool:
    return _v6_route(module.data) is not None


def _v6_destinations(module: AnyConfigurationModule, description: str) -> list[str]:
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
    """What the Orchestrator stores for an IPv6 key sent in upper case, and whether the save passes.

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
    """A missing edge QoS module, inserted without ``returnData``, is read back by the ID answered.

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
        state.edge.qos.service_rate_limit = ServiceRateLimit.model_validate(
            {"enabled": False, "inputType": "percent", "value": 1}
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
"""Names whose rows the VCO's processors delete and rebuild on a write: not compared after one.

``deviceSettings:vnfs:edge`` is a processor name too, but it is append-only, so
its rows stay and are compared.
"""


def _row_keys(refs: object) -> Counter[tuple[str, str, str]]:
    """Each row of raw *refs* by ``(name, logicalId, segmentLogicalId)`` but those of a rebuilt name.

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
    """Raw *refs* without a client-connector ref, which carries a login token that is new on every read."""
    if not isinstance(refs, dict):
        return {}
    return {
        name: rows
        for name, rows in cast("dict[str, Any]", refs).items()
        if "clientConnector" not in name
    }


def _check_state_holds_stored(
    label: str, state: ConfigState, stored: AnyConfigurationModule
) -> None:
    """After a save the state's module is the module as stored: the version, data and refs of a read made right after."""
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
    """The name and the logical ID of a segment entry's segment, where it has both as text."""
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


def _global_segment_without_provider(module: AnyConfigurationModule) -> bool:
    """A device settings module with refs read, whose Global Segment has a DNS section and no primary provider row."""
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
    """A marked profile's Global Segment, and a network service of type ``dns`` to set on it."""

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
    """The target of a case that sets a DNS provider; skip, saying what to set up, where there is none.

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
    """Set the Global Segment's primary DNS provider to the service of this logical ID, or clear it with ``None``."""
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
    """The plan adds (or removes) the primary provider row of the Global Segment, and no other row."""
    changes = plan.ref_changes
    check(
        len(changes) == 1
        and changes[0].added == added
        and changes[0].name == PRIMARY_PROVIDER
        and changes[0].segment == target.segment,
        f"{label}: the plan lists {len(changes)} ref change(s), expected the "
        f"primary provider row of the Global Segment {'added' if added else 'removed'} alone",
    )


def _check_primary_row(
    label: str, stored: AnyConfigurationModule, target: DnsTarget, *, set_: bool
) -> None:
    """The stored refs hold the primary provider row of the Global Segment, for the service set, or none."""
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
    """*error* as what holds no lab data: ``failure_text``, else the suite's own message of a failed check."""
    return failure_text(error) or str(error)


@asynccontextmanager
async def _provider_cleared(
    pipeline: ConfigPipeline, lab: Lab, target: DnsTarget, site: Site
) -> AsyncGenerator[None]:
    """However the block ends, clear the Global Segment's primary provider row if one is stored.

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
    """Raise the OSPF cost of the profile's first LAN network by *by*; the path it changes.

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
    """Every ref row as read is still stored after a data edit, but the rows of the names the VCO rebuilds.

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
    """The fields of the ref rows the Orchestrator returns after a save that sends refs, for each declared name.

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
