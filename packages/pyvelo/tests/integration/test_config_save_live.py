"""Live tests of the configuration write path (``plan_module`` / ``update_module``).

A save sends a module's raw data as it was read, with only the caller's changes
applied, under the version it was read at. These tests prove that against a real
Orchestrator: they edit, save, read the module back and compare it with what was
there before, then put everything back.

Every test that writes does so inside ``restoring`` (see ``_config_live``), which
restores the module and checks that it is restored whether or not the test
passes. Targets are found at run time; a test that finds none is skipped. The
edits are synthetic and inert: a copy of a rule appended after the rule it
copies, a QoE threshold, a link description, a LAN network's OSPF cost.

Server-managed keys seen in what the Orchestrator stores (the tests allow
exactly these and nothing else):

* ``ruleLogicalId``: assigned to a rule that was added without one.
* ``routeMapsVersion``: added to a deviceSettings segment that lacks it (see
  ``_config_live.SERVER_ADDED_KEYS``).
* refs: the Orchestrator re-derives a deviceSettings module's refs from its data
  when the data changes and drops one whose object is gone; a firewall or WAN
  module's refs stay as they were. The tests never write refs and restore data
  only.

Requires VCO_HOST and VCO_TOKEN (VCO_ENTERPRISE_ID if the token needs one).

Run:
    uv run --env-file .env pytest -m integration packages/pyvelo/tests/integration/test_config_save_live.py
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import MISSING
from pyvelo.client import ConfigConflictError, VcoOperationError
from pyvelo.client.enums.configuration import ConfigurationModuleName as Name
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

from ._config_live import (
    Lab,
    Site,
    check,
    describe,
    find_module,
    is_known_load_gap,
    plain,
    plain_changes,
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
    assert isinstance(data, DeviceSettingsData | WanData | QosData | FirewallData)
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


async def test_edges_that_do_not_load_are_the_known_model_gap(lab: Lab) -> None:
    """Edges that cannot be read are left out of the tests above, not hidden.

    The known gap is a deviceSettings client-connector ref that arrives as one
    object with ``data: null``. Any other reason an edge fails to load raises
    while the lab is discovered, and fails every test.
    """
    if lab.unloadable:
        pytest.xfail(
            f"{len(lab.unloadable)} edge(s) do not load: a client-connector ref "
            "with null data does not validate (known model gap)"
        )


async def test_load_gap_classification() -> None:
    """The gap is recognised by where it is, so that no other failure is taken for it."""
    from pydantic import BaseModel, ValidationError

    class Ref(BaseModel):
        data: dict[str, int]

    class Refs(BaseModel):
        refs: dict[str, Ref]

    with pytest.raises(ValidationError) as gap:
        _ = Refs.model_validate(
            {"refs": {"deviceSettings:clientConnector:config": {"data": None}}}
        )
    assert is_known_load_gap(gap.value)
    with pytest.raises(ValidationError) as other:
        _ = Refs.model_validate({"refs": {"deviceSettings:segment": {"data": None}}})
    assert not is_known_load_gap(other.value)
    assert not is_known_load_gap(ValueError("not a validation error"))


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
        before = plain(module.raw_data)
        data = data_of(module)
        expected = step.apply(data)
        plan = plan_module(module)
        check(
            plan.operation == "update",
            f"{step.label}: planned {plan.operation}, expected an update",
        )
        _check_paths(step.label, "the plan", plan.changes, expected)

        await lab.client.update_module(module, data, enterprise_id=lab.enterprise_id)

        after = await site.read_present()
        check(
            after.version != module.version,
            f"{step.label}: the module version did not change on write",
        )
        stored = raw_changes(before, plain(after.raw_data), model)
        _check_paths(step.label, "the module as read back", stored, expected)
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
        assert isinstance(d.qoe, QoeSettings)
        d.qoe.voice.latency.yellow += 1  # a reporting threshold; it steers nothing
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
    return isinstance(module, WanConfigurationModule) and bool(module.data.links)


def _wan_steps(data: WanData) -> list[Step]:
    assert isinstance(data.links, list)
    link_name = data.links[0].name
    description = synthetic_name()

    def apply(d: WanData) -> list[Path]:
        assert isinstance(d.links, list)
        next(
            link for link in d.links if link.name == link_name
        ).description = description
        return [Path(("links", Key(link_name), "description"))]

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
            leftover = plain_changes(original.data, final.raw_data, case.model)
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


@pytest.mark.xfail(
    strict=True,
    raises=VcoOperationError,
    reason=(
        "a QoS rule's match writes its IPv6 fields for an IPv4 rule, which the "
        "Orchestrator rejects (a firewall rule's match drops them)"
    ),
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
        data = data_of(await site.read_present())
        assert isinstance(data, QosData)
        _ = await apply_steps(lab, site, QosData, _qos_rule_steps(data, "IPv4"))


# ---------------------------------------------------------------------------
# 7. A stale version is refused
# ---------------------------------------------------------------------------


async def test_a_module_read_before_another_write_is_refused(lab: Lab) -> None:
    layout = await lab.pick_writable_edge(Name.WAN, _wan_usable, "with a link")
    site = lab.edge_site(layout, Name.WAN)
    async with restoring(site) as original:
        assert original is not None
        newer = await site.read_present()
        older = await site.read_present()
        written, refused = synthetic_name(), synthetic_name()
        for module, text in ((newer, written), (older, refused)):
            data = data_of(module)
            assert isinstance(data, WanData) and isinstance(data.links, list)
            data.links[0].description = text
        await lab.client.update_module(
            newer, data_of(newer), enterprise_id=lab.enterprise_id
        )
        with pytest.raises(ConfigConflictError):
            await lab.client.update_module(
                older, data_of(older), enterprise_id=lab.enterprise_id
            )
        after = await site.read_present()
        stored = plain_changes(original.data, after.raw_data, WanData)
        check(
            len(stored) == 1 and stored[0].after == written,
            f"the refused write changed the module: {describe(stored)}",
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
            data = data_of(module)
            assert isinstance(data, WanData) and isinstance(data.links, list)
            data.links[0].description = text
        await lab.client.update_module(
            newer, data_of(newer), enterprise_id=lab.enterprise_id
        )
        stale: UpdateConfigurationModule = update_request(
            older, plan_module(older), lab.enterprise_id
        )
        stale.version = MISSING
        await lab.client.update_configuration_module(stale)
        after = await site.read_present()
        stored = plain_changes(original.data, after.raw_data, WanData)
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

    async def spy(request: UpdateConfigurationModule) -> None:
        sent.append(request)
        await send(request)

    async with restoring(site):
        module = await site.read_present()
        assert isinstance(module, DeviceSettingsConfigurationModule)

        # Passing the refs as read is no change to them.
        assert isinstance(module.refs, DeviceSettingsRefs)
        unchanged = plan_module(module, refs=module.refs)
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
        data = data_of(await site.read_present())
        assert isinstance(data, FirewallData)
        steps = _firewall_steps(data)[:1]
        _ = await apply_steps(lab, site, FirewallData, steps)
        after = await site.read_present()
        check(
            after.raw_refs == original.refs,
            "writing the firewall data without refs changed its refs",
        )
