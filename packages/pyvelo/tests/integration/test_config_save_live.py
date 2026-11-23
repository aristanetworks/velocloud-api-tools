"""Live tests of the configuration write path (``plan_module`` / ``update_module``).

A save sends a module's raw data as it was read, with only the caller's changes
applied, under the version it was read at, and returns the module as stored.
These tests prove that against a real Orchestrator: they edit, save, read the
module back and compare it with what was there before, then put everything
back.

Every test that writes does so inside ``restoring`` (see ``_config_live``), which
restores the module and checks that it is restored whether or not the test
passes. Targets are found at run time; a test that finds none is skipped. The
edits are synthetic and inert: a copy of a rule appended after the rule it
copies, a QoE threshold, a link description, a LAN network's OSPF cost. A write
error is reported by its class and redacted paths (``_outcome``): its own
message names lab entities and shows their values.

Server-managed keys seen in what the Orchestrator stores (the tests allow
exactly these and nothing else):

* ``ruleLogicalId``: assigned to a rule that was added without one.
* ``routeMapsVersion``: added to a deviceSettings segment entry that lacks it
  (accepted drift, see ``_config_live.settle``).
* refs: the Orchestrator re-derives a deviceSettings module's refs from its data
  when the data changes and drops one whose object is gone; a firewall or WAN
  module's refs stay as they were. The tests never write refs and restore data
  only.

How the Orchestrator refuses a stale write is recorded by the run, not assumed.
Each test that makes one prints a ``refused with`` line naming the form: the
precondition error (its conditional check on), or the module row as stored, or
a row count of zero (the check off, ``returnData`` honoured or ignored). With
the check off and the module row returned, a stale write that edits a value
another write also changed raises ``WriteNotAppliedError`` (a clash); with it
on, ``ConfigConflictError``. A VCO that ignores ``returnData`` answers
``rows: 0`` instead, which is a stated refusal (``ConfigConflictError``).
Seen on the lab (first write-back run, 2026-11-22): the module row as stored,
so the check is off and ``returnData`` is honoured; a stale ``update_module``
of the same value raised ``WriteNotAppliedError``, of another value
``ConfigConflictError``.

Requires VCO_HOST and VCO_TOKEN (VCO_ENTERPRISE_ID if the token needs one).

Run:
    uv run --env-file .env pytest -m integration packages/pyvelo/tests/integration/test_config_save_live.py
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, cast

import pytest
from pydantic import MISSING
from pyvelo.client import (
    ConfigConflictError,
    ModuleWriteError,
    VcoOperationError,
    WriteNotAppliedError,
)
from pyvelo.client.enums.configuration import ConfigurationModuleName as Name
from pyvelo.client.exceptions import is_precondition_failure
from pyvelo.client.models.configuration import (
    DeviceSettingsData,
    DeviceSettingsRefs,
    FirewallData,
    QosData,
    WanData,
)
from pyvelo.client.models.configuration.firewall.rules import FirewallOutboundRule
from pyvelo.client.models.configuration.qos.qoe import QoeSettings
from pyvelo.client.models.configuration.qos.rule import QosRule
from pyvelo.client.module_check import data_model
from pyvelo.client.module_write import plan_module, update_request, wire_dump
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    ConfigurationModuleBase,
    DeviceSettingsConfigurationModule,
    FirewallConfigurationModule,
    QosConfigurationModule,
    UpdateConfigurationModule,
    WanConfigurationModule,
)
from pyvelo.core import ConfigContext, VcoModel
from pyvelo.core.rebase import Key, Path, RawChange, raw_changes
from pyvelo.core.response_check import PlannedChange, ServerChange

from ._config_live import (
    Lab,
    Site,
    changes_between,
    check,
    check_no_override_flag,
    describe,
    expect,
    find_module,
    read_profile,
    read_stack,
    redact,
    restoring,
    synthetic_name,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

type EditableData = DeviceSettingsData | WanData | QosData | FirewallData

SERVER_ASSIGNED_KEYS = frozenset({"ruleLogicalId"})
"""Keys the Orchestrator adds to a new rule; an added item is compared without them."""


def data_of(module: ConfigurationModuleBase) -> EditableData:
    data: object = getattr(module, "data", None)
    if not isinstance(data, DeviceSettingsData | WanData | QosData | FirewallData):
        pytest.fail(f"the data of a module is a {type(data).__name__}", pytrace=False)
    return data


# ---------------------------------------------------------------------------
# 1. No edit, nothing planned
# ---------------------------------------------------------------------------


def _plan_nothing(
    where: str,
    module: AnyConfigurationModule,
    level: ConfigContext,
    profile: AnyConfigurationModule | None,
    failures: list[str],
) -> None:
    plan = plan_module(module, level=level, profile=profile)
    raw: object = module.raw_data
    if plan.operation != "none" or plan.changes:
        failures.append(
            f"{where} {module.name}: planned {plan.operation} ({describe(plan.changes)})"
        )
    elif plan.data != raw or plan.data is raw:
        failures.append(f"{where} {module.name}: plan.data is not a copy of raw_data")
    elif plan.refs is not None:
        failures.append(f"{where} {module.name}: planned refs with no edit")
    refs: object = getattr(module, "refs", MISSING)
    if isinstance(module.raw_refs, dict) and refs is not MISSING:
        again = plan_module(
            module, refs=refs if isinstance(refs, VcoModel | dict) else None
        ).refs
        if again is not None:
            failures.append(f"{where} {module.name}: unchanged refs were planned")


async def test_no_edit_plans_nothing_on_every_edge_and_profile(lab: Lab) -> None:
    failures: list[str] = []
    checked = 0
    for layout in lab.layouts:
        edge, profile = await read_stack(lab.client, layout.edge_id, lab.enterprise_id)
        for module in [] if edge.modules is MISSING else edge.modules:
            _plan_nothing(
                f"edge #{layout.index}",
                module,
                ConfigContext.EDGE,
                find_module(profile, module.name),
                failures,
            )
            checked += 1
    for position, profile_id in enumerate(lab.profile_ids):
        config = await read_profile(lab.client, profile_id, lab.enterprise_id)
        for module in [] if config.modules is MISSING else config.modules:
            _plan_nothing(
                f"profile #{position}", module, ConfigContext.PROFILE, None, failures
            )
            checked += 1
    if not checked:
        pytest.skip("the Orchestrator has no edge or profile configuration")
    check(
        not failures,
        f"{len(failures)} of {checked} modules plan a write with no edit:\n"
        + "\n".join(failures),
    )


# ---------------------------------------------------------------------------
# 2. Edit, read back, only the intended change
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """One save: an edit to the module's data, and the paths it must change."""

    label: str
    apply: Callable[[Any], list[Path]]
    """Edits the data in place and returns the paths the edit changes."""


@dataclass(frozen=True)
class Case:
    id: str
    name: Name
    model: type[VcoModel]
    suitable: Callable[[AnyConfigurationModule], bool]
    steps: Callable[[Any], list[Step]]
    what: str


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


def _where(changes: Iterable[PlannedChange | ServerChange]) -> str:
    """The paths of *changes*, redacted, with ``refs`` before a refs entry."""
    shown = [
        f"refs {redact(c.path)}" if c.part == "refs" else redact(c.path)
        for c in changes
    ]
    return ", ".join(shown) or "none"


def _stored_otherwise(lost_edits: Iterable[PlannedChange]) -> list[Path]:
    """Where in each lost edit the value stored is not the value sent, at the deepest path."""
    return [
        Path((*change.path.parts, *inner.path.parts))
        for change in lost_edits
        for inner in raw_changes(change.intended, change.stored)
    ]


def _outcome(error: ModuleWriteError) -> str:
    """*error* as its class and its paths, redacted.

    Its own message names segments and rules of the lab and shows their
    values, so a test reports this in its place. Where a lost edit was
    stored otherwise than sent is a candidate for ``MODULE_REWRITES``.
    """
    kind = type(error).__name__
    if isinstance(error, ConfigConflictError):
        kind += f" ({error.detail})"
    otherwise = ", ".join(redact(p) for p in _stored_otherwise(error.lost_edits))
    return (
        f"{kind} on the {error.module_name or 'unnamed'} module: "
        f"lost edits {_where(error.lost_edits)}; "
        f"stored otherwise than sent at {otherwise or 'none'}; "
        f"applied {_where(error.applied)}; "
        f"server changes {_where(error.server_changes)}"
    )


async def _update[M: ConfigurationModuleBase](
    lab: Lab, module: M, data: EditableData
) -> M:
    """``update_module``; a write error fails the test with ``_outcome`` alone."""
    try:
        return await lab.client.update_module(
            module, data, enterprise_id=lab.enterprise_id
        )
    except ModuleWriteError as error:
        # Failed outside the handler: the error must not be chained to the report.
        message = _outcome(error)
    pytest.fail(message, pytrace=False)


def _refusal_form(error: ModuleWriteError) -> str:
    """How the Orchestrator refused the stale write that raised *error*.

    ``write_module`` raises the outcome from the refusal the client raised:
    a ``ConfigConflictError`` from the precondition error, or one for a row
    count of zero. When the module could not be read back after the refusal,
    the refusal is the outcome's context instead (the outcome was raised
    while handling it). An outcome with neither was checked against the
    module row the Orchestrator answered with.
    """
    refusal = error.__cause__ if error.__cause__ is not None else error.__context__
    if not isinstance(refusal, ConfigConflictError):
        return "the module row as stored"
    reason = refusal.__cause__
    if isinstance(reason, VcoOperationError) and is_precondition_failure(reason):
        return "the precondition error"
    return "a row count of zero"


def _record_refusal(
    capsys: pytest.CaptureFixture[str], label: str, error: ModuleWriteError
) -> None:
    """Print, past the capture, the form of the refusal and the error it made."""
    with capsys.disabled():
        print(
            f"\n{label}: refused with {_refusal_form(error)} ({type(error).__name__})"
        )


def _check_entries(
    label: str,
    what: str,
    changes: Sequence[PlannedChange | ServerChange],
    expected: Sequence[Path],
) -> None:
    got = sorted(str(c.path) for c in changes)
    want = sorted(str(p) for p in expected)
    check(
        got == want,
        f"{label}: {what} lists {_where(changes)}; expected "
        f"{', '.join(redact(p) for p in expected) or 'none'}",
    )


def _check_as_read(
    label: str, held: ConfigurationModuleBase, read: ConfigurationModuleBase
) -> None:
    """*held* is the module as a read made right after shows it: its version and raw data."""
    check(
        held.version == read.version,
        f"{label}: the module held has another version than a read made right after",
    )
    check(
        held.raw_data == read.raw_data,
        f"{label}: the module held differs from a read made right after: "
        + describe(raw_changes(read.raw_data, held.raw_data, data_model(read))),
    )


def _value_at(document: object, path: Path) -> object:
    """The value at *path*, a path of names only, in a JSON document; ``MISSING`` if there is none."""
    value = document
    for part in path.parts:
        if not isinstance(value, dict) or not isinstance(part, str):
            return MISSING
        value = cast(dict[str, object], value).get(part, MISSING)
    return value


async def apply_steps(
    lab: Lab, site: Site, model: type[VcoModel], steps: Sequence[Step]
) -> list[list[RawChange]]:
    """Save each step through ``update_module`` and check what the Orchestrator stored.

    Each step reads the module afresh, edits it, plans it (the plan must hold
    only the intended paths), saves it, reads it back and compares it with what
    it was before the step: only the intended paths may differ, and what is
    stored there is what was sent (but for the keys the server assigns).
    Returns, for each step, the differences found.
    """
    observed: list[list[RawChange]] = []
    for step in steps:
        module = await site.read_present()
        before = copy.deepcopy(module.raw_data)
        data = data_of(module)
        expected = step.apply(data)
        plan = plan_module(module)
        check(
            plan.operation == "update",
            f"{step.label}: planned {plan.operation}, expected an update",
        )
        _check_paths(step.label, "the plan", plan.changes, expected)

        _ = await _update(lab, module, data)

        after = await site.read_present()
        check(
            after.version != module.version,
            f"{step.label}: the module version did not change on write",
        )
        stored = changes_between(site.name, before, after.raw_data, model)
        _check_paths(step.label, "the module as read back", stored, expected)
        if site.name is Name.FIREWALL:
            check_no_override_flag(step.label, before, plan.data, after.raw_data)
        sent = {str(c.path): c for c in plan.changes}
        for change in stored:
            planned = sent[str(change.path)]
            check(
                _same_value(change.before, planned.before)
                and _same_value(change.after, planned.after),
                f"{step.label}: {redact(change.path)} was stored other than as sent",
            )
        observed.append(stored)
    return observed


# -- firewall ---------------------------------------------------------------


def _firewall_usable(module: AnyConfigurationModule) -> bool:
    return isinstance(module, FirewallConfigurationModule) and any(
        s.outbound for s in module.data.segments
    )


def _firewall_steps(data: FirewallData) -> list[Step]:
    segment_name = next(s.segment.name for s in data.segments if s.outbound)
    first, second = synthetic_name(), synthetic_name()
    outbound = Path(("segments", Key(segment_name), "outbound"))

    def rules(d: FirewallData) -> list[FirewallOutboundRule]:
        return next(s for s in d.segments if s.segment.name == segment_name).outbound

    def append_copy(name: str) -> Callable[[FirewallData], list[Path]]:
        # A copy of the last rule, after it: the rule it copies matches first, so
        # the copy never decides anything.
        def apply(d: FirewallData) -> list[Path]:
            items = rules(d)
            added = items[-1].model_copy(deep=True)
            added.name = name
            added.rule_logical_id = MISSING
            items.append(added)
            return [outbound.child(Key(name))]

        return apply

    def swap(d: FirewallData) -> list[Path]:
        # The two copies are identical but for their names, so either order is inert.
        items = rules(d)
        i, j = (
            next(k for k, r in enumerate(items) if r.name == n) for n in (first, second)
        )
        items[i], items[j] = items[j], items[i]
        return [outbound]

    def delete(name: str) -> Callable[[FirewallData], list[Path]]:
        def apply(d: FirewallData) -> list[Path]:
            items = rules(d)
            del items[next(k for k, r in enumerate(items) if r.name == name)]
            return [outbound.child(Key(name))]

        return apply

    return [
        Step("add a rule", append_copy(first)),
        Step("add a second rule", append_copy(second)),
        Step("reorder the two", swap),
        Step("delete the first", delete(first)),
        Step("delete the second", delete(second)),
    ]


# -- QoS --------------------------------------------------------------------


def _qoe_usable(module: AnyConfigurationModule) -> bool:
    return isinstance(module, QosConfigurationModule) and isinstance(
        module.data.qoe, QoeSettings
    )


def _qoe_steps(_: QosData) -> list[Step]:
    def apply(d: QosData) -> list[Path]:
        qoe = expect(d.qoe, QoeSettings, "the QoE settings")
        qoe.voice.latency.yellow += 1  # a reporting threshold; it steers nothing
        return [Path(("qoe", "voice", "latency", "yellow"))]

    return [Step("raise the voice latency threshold", apply)]


def _qos_rule_usable(module: AnyConfigurationModule) -> bool:
    return (
        isinstance(module, QosConfigurationModule)
        and _qos_source(module.data) is not None
    )


def _qos_source(data: QosData, version: str = "IPv4v6") -> tuple[str, str] | None:
    """The segment and rule names of the first QoS rule that matches *version* addresses."""
    for segment in data.segments:
        for rule in segment.rules:
            if rule.match.ip_version == version:
                return segment.segment.name, rule.name
    return None


def _qos_rule_steps(data: QosData, version: str = "IPv4v6") -> list[Step]:
    source = _qos_source(data, version)
    assert source is not None
    segment_name, source_name = source
    name = synthetic_name()
    rules = Path(("segments", Key(segment_name), "rules"))

    def items(d: QosData) -> list[QosRule]:
        return next(s for s in d.segments if s.segment.name == segment_name).rules

    def add(d: QosData) -> list[Path]:
        # After the rule it copies, which matches first.
        rule_list = items(d)
        added = next(r for r in rule_list if r.name == source_name).model_copy(
            deep=True
        )
        added.name = name
        added.rule_logical_id = uuid.uuid4()  # as the model gives a new rule
        rule_list.append(added)
        return [rules.child(Key(name))]

    def delete(d: QosData) -> list[Path]:
        rule_list = items(d)
        del rule_list[next(k for k, r in enumerate(rule_list) if r.name == name)]
        return [rules.child(Key(name))]

    return [Step("add a rule", add), Step("delete it", delete)]


# -- deviceSettings and WAN --------------------------------------------------


def _lan_usable(module: AnyConfigurationModule) -> bool:
    return isinstance(module, DeviceSettingsConfigurationModule) and bool(
        module.data.lan.networks
    )


def _lan_steps(data: DeviceSettingsData) -> list[Step]:
    vlan = data.lan.networks[0].vlan_id

    def apply(d: DeviceSettingsData) -> list[Path]:
        next(n for n in d.lan.networks if n.vlan_id == vlan).cost += 1  # OSPF cost
        return [Path(("lan", "networks", Key(vlan), "cost"))]

    return [Step("raise a LAN network's cost", apply)]


def _wan_usable(module: AnyConfigurationModule) -> bool:
    """Whether *module* is a WAN module whose first link has a key (an internal ID)."""
    if not isinstance(module, WanConfigurationModule):
        return False
    links = module.data.links
    return links is not MISSING and bool(links) and links[0].internal_id is not MISSING


def _wan_steps(data: WanData) -> list[Step]:
    first = expect(data.links, list, "the WAN links")[0]
    # The WAN key: internal ID and address family (link names repeat).
    key = (first.internal_id, first.addressing_version)
    description = synthetic_name()

    def apply(d: WanData) -> list[Path]:
        links = expect(d.links, list, "the WAN links")
        next(
            link for link in links if (link.internal_id, link.addressing_version) == key
        ).description = description
        return [Path(("links", Key((str(key[0]), key[1])), "description"))]

    return [Step("describe a link", apply)]


CASES = [
    Case(
        "firewall-rules",
        Name.FIREWALL,
        FirewallData,
        _firewall_usable,
        _firewall_steps,
        "with a rule to copy",
    ),
    Case("qos-qoe", Name.QOS, QosData, _qoe_usable, _qoe_steps, "with QoE thresholds"),
    Case(
        "qos-rule",
        Name.QOS,
        QosData,
        _qos_rule_usable,
        _qos_rule_steps,
        "with an IPv4v6 rule to copy",
    ),
    Case(
        "device-settings",
        Name.DEVICE_SETTINGS,
        DeviceSettingsData,
        _lan_usable,
        _lan_steps,
        "with a LAN network",
    ),
    Case("wan", Name.WAN, WanData, _wan_usable, _wan_steps, "with a link"),
]


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_edit_reads_back_as_only_the_intended_change(
    lab: Lab, case: Case
) -> None:
    layout = await lab.pick_writable_edge(case.name, case.suitable, case.what)
    site = lab.edge_site(layout, case.name)
    async with restoring(site) as original:
        assert original is not None
        steps = case.steps(data_of(await site.read_present()))
        _ = await apply_steps(lab, site, case.model, steps)
        final = await site.read_present()
        if len(steps) > 1:  # the steps undo each other
            leftover = changes_between(
                site.name, original.data, final.raw_data, case.model
            )
            check(
                not leftover,
                f"after the last step the module still differs from the original: "
                f"{describe(leftover)}",
            )


# ---------------------------------------------------------------------------
# 3. Keys the model does not know survive an edit
# ---------------------------------------------------------------------------


def _unrelated(a: Path, b: Path) -> bool:
    """Neither path is, or lies inside, the other."""
    n = min(len(a.parts), len(b.parts))
    return a.parts[:n] != b.parts[:n]


def _uncarried_keys(module: AnyConfigurationModule) -> list[Path]:
    """Where the raw data has keys that the typed data does not write back."""
    data = data_of(module)
    dump = wire_dump(data, data.config_context)
    return [
        c.path
        for c in raw_changes(module.raw_data, dump, type(data))
        if c.after is MISSING
    ]


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_keys_the_model_does_not_carry_survive_an_edit(
    lab: Lab, case: Case
) -> None:
    layout = await lab.pick_writable_edge(
        case.name,
        lambda m: case.suitable(m) and bool(_uncarried_keys(m)),
        f"{case.what} and keys the model does not carry",
    )
    site = lab.edge_site(layout, case.name)
    async with restoring(site):
        module = await site.read_present()
        uncarried = _uncarried_keys(module)
        first_step = case.steps(data_of(module))[0]
        (changes,) = await apply_steps(lab, site, case.model, [first_step])
        lost = [u for u in uncarried if not all(_unrelated(c.path, u) for c in changes)]
        check(
            not lost,
            f"{len(lost)} of {len(uncarried)} keys the model does not carry were "
            f"changed or dropped by the edit: {', '.join(redact(p) for p in lost)}",
        )


async def test_a_qos_rule_copied_from_an_ipv4_rule_saves(lab: Lab) -> None:
    layout = await lab.pick_writable_edge(
        Name.QOS,
        lambda m: (
            isinstance(m, QosConfigurationModule)
            and _qos_source(m.data, "IPv4") is not None
        ),
        "with an IPv4 rule to copy",
    )
    site = lab.edge_site(layout, Name.QOS)
    async with restoring(site):
        data = expect(data_of(await site.read_present()), QosData, "the QoS data")
        _ = await apply_steps(lab, site, QosData, _qos_rule_steps(data, "IPv4"))


# ---------------------------------------------------------------------------
# 7. A stale version is refused
# ---------------------------------------------------------------------------


async def test_a_module_read_before_another_write_is_refused(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = await lab.pick_writable_edge(Name.WAN, _wan_usable, "with a link")
    site = lab.edge_site(layout, Name.WAN)
    async with restoring(site) as original:
        assert original is not None
        newer = await site.read_present()
        older = await site.read_present()
        written, refused = synthetic_name(), synthetic_name()
        for module, text in ((newer, written), (older, refused)):
            data = expect(data_of(module), WanData, "the WAN data")
            expect(data.links, list, "the WAN links")[0].description = text
        _ = await _update(lab, newer, data_of(newer))
        # Both writes edit one value. A VCO with its conditional check on refuses
        # the version: ConfigConflictError. One with it off answers with the
        # module as stored, whose value there is the other write's: a clash,
        # WriteNotAppliedError.
        with pytest.raises((ConfigConflictError, WriteNotAppliedError)) as caught:
            _ = await lab.client.update_module(
                older, data_of(older), enterprise_id=lab.enterprise_id
            )
        error = caught.value
        _record_refusal(capsys, "a stale update_module, one value", error)

        after = await site.read_present()
        stored = changes_between(Name.WAN, original.data, after.raw_data, WanData)
        check(
            len(stored) == 1 and stored[0].after == written,
            f"the refused write changed the module: {describe(stored)}",
        )
        # The error holds the module as stored, with the other write's value,
        # and the refused edit to redo: in either form, a clash.
        check(error.module is not None, "the error carries no module")
        assert error.module is not None
        _check_as_read("the error's module", error.module, after)
        _check_entries(
            "the refused write", "lost_edits", error.lost_edits, [stored[0].path]
        )
        (lost,) = error.lost_edits
        check(
            lost.intended == refused and lost.stored == written and lost.clash,
            "the lost edit is not the refused description, a clash with the other "
            "write's",
        )
        _check_entries("the refused write", "server_changes", error.server_changes, [])


VOICE_YELLOW = Path(("qoe", "voice", "latency", "yellow"))
VOICE_RED = Path(("qoe", "voice", "latency", "red"))
VIDEO_RED = Path(("qoe", "video", "latency", "red"))


def _qoe_thresholds_usable(module: AnyConfigurationModule) -> bool:
    """Whether *module* is a QoS module whose raw data has the QoE thresholds ``_raised`` raises."""
    return _qoe_usable(module) and all(
        isinstance(_value_at(module.raw_data, path), int)
        for path in (VOICE_YELLOW, VOICE_RED, VIDEO_RED)
    )


def _raised(module: AnyConfigurationModule, path: Path) -> QosData:
    """A copy of the module's QoS data with the QoE threshold at *path* one higher.

    A reporting threshold, which steers nothing; a red one raised stays above
    its yellow one. The module's own data is left as it was read.
    """
    data = expect(data_of(module), QosData, "the QoS data").model_copy(deep=True)
    value = expect(_value_at(module.raw_data, path), int, "a QoE threshold")
    patch: dict[str, object] = {str(path.parts[-1]): value + 1}
    for part in reversed(path.parts[:-1]):
        patch = {str(part): patch}
    return data.merge_patch(patch)


async def test_update_module_returns_the_module_as_stored(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = await lab.pick_writable_edge(
        Name.QOS, _qoe_thresholds_usable, "with voice and video QoE thresholds"
    )
    site = lab.edge_site(layout, Name.QOS)
    async with restoring(site):
        first = await site.read_present()
        # update_module replaces `first` (it raises on a second write of it), so
        # the stale write below goes out from a copy taken before the first one.
        stale = copy.deepcopy(first)
        one = await _update(lab, first, _raised(first, VOICE_YELLOW))
        _check_as_read("the first write", one, await site.read_present())

        # A second write, from the module the first returned.
        two = await _update(lab, one, _raised(one, VOICE_RED))
        now = await site.read_present()
        _check_as_read("the second write", two, now)
        _check_paths(
            "two writes",
            "the module read back",
            changes_between(Name.QOS, first.raw_data, now.raw_data, QosData),
            [VOICE_YELLOW, VOICE_RED],
        )

        # A write from the copy of the first module carries a stale version. It
        # edits a value neither write touched, so nothing at its path moved: a
        # conflict, whichever form the refusal takes.
        with pytest.raises(ModuleWriteError) as caught:
            _ = await lab.client.update_module(
                stale, _raised(stale, VIDEO_RED), enterprise_id=lab.enterprise_id
            )
        error = caught.value
        _record_refusal(capsys, "a stale update_module, another value", error)
        check(
            isinstance(error, ConfigConflictError),
            f"the stale write raised {_outcome(error)}",
        )

        after = await site.read_present()
        check(
            after.version == now.version and after.raw_data == now.raw_data,
            "the refused write changed the module",
        )
        check(error.module is not None, "the error carries no module")
        assert error.module is not None
        _check_as_read("the error's module", error.module, after)
        _check_entries("the refused write", "lost_edits", error.lost_edits, [VIDEO_RED])
        check(
            not any(change.clash for change in error.lost_edits),
            "the lost edit is a clash, though nothing moved there",
        )
        _check_entries(
            "the refused write",
            "server_changes",
            error.server_changes,
            [VOICE_YELLOW, VOICE_RED],
        )


async def test_a_request_without_a_version_is_not_checked(lab: Lab) -> None:
    layout = await lab.pick_writable_edge(Name.WAN, _wan_usable, "with a link")
    site = lab.edge_site(layout, Name.WAN)
    async with restoring(site) as original:
        assert original is not None
        newer = await site.read_present()
        older = await site.read_present()
        first, second = synthetic_name(), synthetic_name()
        for module, text in ((newer, first), (older, second)):
            data = expect(data_of(module), WanData, "the WAN data")
            expect(data.links, list, "the WAN links")[0].description = text
        _ = await _update(lab, newer, data_of(newer))
        stale: UpdateConfigurationModule = update_request(
            older, plan_module(older), lab.enterprise_id
        )
        stale.version = MISSING
        _ = await lab.client.update_configuration_module(stale)
        after = await site.read_present()
        stored = changes_between(Name.WAN, original.data, after.raw_data, WanData)
        check(
            len(stored) == 1 and stored[0].after == second,
            "a request without a version did not write unconditionally: "
            + describe(stored),
        )


# ---------------------------------------------------------------------------
# 8. Refs
# ---------------------------------------------------------------------------


def _has_refs(module: AnyConfigurationModule) -> bool:
    return (
        isinstance(module, DeviceSettingsConfigurationModule)
        and isinstance(module.raw_refs, dict)
        and bool(module.raw_refs)
        and bool(module.data.lan.networks)
    )


async def test_a_data_edit_sends_no_refs(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = await lab.pick_writable_edge(Name.DEVICE_SETTINGS, _has_refs, "with refs")
    site = lab.edge_site(layout, Name.DEVICE_SETTINGS)
    sent: list[UpdateConfigurationModule] = []
    send = lab.client.update_configuration_module

    async def spy(
        request: UpdateConfigurationModule, *, level: ConfigContext | None = None
    ) -> AnyConfigurationModule | None:
        sent.append(request)
        return await send(request, level=level)

    async with restoring(site):
        module = expect(
            await site.read_present(),
            DeviceSettingsConfigurationModule,
            "the deviceSettings module",
        )

        # Passing the refs as read is no change to them.
        refs = expect(module.refs, DeviceSettingsRefs, "the deviceSettings refs")
        unchanged = plan_module(module, refs=refs)
        check(
            unchanged.operation == "none" and unchanged.refs is None,
            "refs passed back as read were planned as a change",
        )

        monkeypatch.setattr(lab.client, "update_configuration_module", spy)
        _ = await apply_steps(lab, site, DeviceSettingsData, _lan_steps(module.data))
        check(len(sent) == 1, f"expected one write, saw {len(sent)}")
        check(
            sent[0].update.refs is MISSING,
            "a data edit sent refs along with the data",
        )


def _firewall_with_refs(module: AnyConfigurationModule) -> bool:
    return (
        _firewall_usable(module)
        and isinstance(module.raw_refs, dict)
        and bool(module.raw_refs)
    )


async def test_a_data_edit_leaves_the_refs_of_a_firewall_as_they_were(
    lab: Lab,
) -> None:
    layout = await lab.pick_writable_edge(
        Name.FIREWALL, _firewall_with_refs, "with refs and a rule to copy"
    )
    site = lab.edge_site(layout, Name.FIREWALL)
    async with restoring(site) as original:
        assert original is not None
        data = expect(
            data_of(await site.read_present()), FirewallData, "the firewall data"
        )
        steps = _firewall_steps(data)[:1]
        _ = await apply_steps(lab, site, FirewallData, steps)
        after = await site.read_present()
        check(
            after.raw_refs == original.refs,
            "writing the firewall data without refs changed its refs",
        )
