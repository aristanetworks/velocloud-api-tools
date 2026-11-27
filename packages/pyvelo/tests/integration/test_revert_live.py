"""Live tests of ``ConfigPipeline.revert`` of a new module whose insert may be stored.

A save that fails after it sent the insert of a new module leaves the state
not knowing whether the Orchestrator holds the module: the module stays new,
and its name is in ``state.unconfirmed_inserts``. ``revert`` of that module
then reads the configuration. With no module of the name there, it only drops
the state's module. A module that holds exactly what the state's holds, apart
from the values the Orchestrator assigns itself (a firewall rule's
``ruleLogicalId``), is deleted with ``configuration/deleteConfigurationModule``.
Anything else is refused with ``ValueError`` and nothing is deleted.

No mock can say what an Orchestrator answers to that delete, or whether it adds
values of its own to a module as it inserts it, so these tests find out on a
lab:

* a stored insert is deleted, and the state, the edge's own configuration and
  what the edge inherits are as before the insert. Four contents: a firewall
  flag, a firewall rule (the Orchestrator assigns its ID), a QoS rate limit and
  a QoS rule. Where ``revert`` refuses its own insert, the Orchestrator stored
  the module with more or other than was sent: the test fails and names the
  paths;
* an insert that never left is taken back with nothing sent;
* a stored module that another writer changed, and one whose edit the state
  took back, are refused and stay stored; and the two ways on that a refusal
  names work: a state loaded again holds the module as stored, and a save
  repeated inserts no second module;
* what the Orchestrator answers to the delete of a module that is gone, and
  whether it stores two modules of one name in one configuration, are recorded.
  Each prints a line that a passing test shows too.

**These tests delete and insert whole modules.** Each takes the firewall or the
QoS module of an edge marked ``pyvelo-it-replace-modules`` (beside
``pyvelo-it-target``) away, inserts a new one, and puts the original back: the
edge goes by its profile's module in between. Only an edge with no module of
the name, or with one that comes back whole (``_comes_back_whole``), is used;
without one a test skips. A test skips only before it has written anything:
whether the lab has what it needs (such an edge, and in the profile a rule or
a flag to copy) is decided from reads alone (``_edge_to_empty``), and what
turns out otherwise after a write fails the test. Every write is inside
``restoring`` (see ``live_support``), which undoes what the run did whether or
not the test passes. What is inserted is inert: a syslog flag, a disabled rate
limit, or a copy of a rule that is first in a list of the profile's own, so
that the copy decides nothing the profile's rule did not.

Requires VCO_HOST and VCO_TOKEN (VCO_ENTERPRISE_ID if the token needs one), and
PYVELO_IT_WRITE_HOST and PYVELO_IT_WRITE_ENTERPRISE_ID: see ``live_support``.

Run only this file:
    uv run --env-file .env pytest -m integration \
        packages/pyvelo/tests/integration/test_revert_live.py
"""

from __future__ import annotations

import copy
import uuid
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from live_support import (
    SERVER_ASSIGNED_KEYS,
    EdgeLayout,
    Lab,
    Site,
    changes_between,
    check,
    check_as_read,
    delete_again,
    delete_module,
    describe,
    effective_data,
    expect,
    failure_text,
    has_key,
    losing_the_insert_answer,
    record,
    record_answers,
    refusal_codes,
    restoring,
    run_of,
    synthetic_name,
    write_data,
)
from pydantic import MISSING
from pyvelo.client import VcoClient, VcoOperationError
from pyvelo.client.enums.configuration import ConfigurationModuleName as Name
from pyvelo.client.enums.profiles import GetConfigurationWith
from pyvelo.client.models.configuration.qos.root import ServiceRateLimit
from pyvelo.client.models.configuration.ref_owners import ref_owners
from pyvelo.client.module_check import data_model
from pyvelo.client.operations.configuration import find_named_module
from pyvelo.client.schemas.configuration import (
    AnyStoredModule,
    DeleteConfigurationModule,
    FirewallConfigurationModule,
    GetConfiguration,
    InsertConfigurationModule,
    InsertFirewallConfigurationModule,
    InsertQosConfigurationModule,
    QosConfigurationModule,
)
from pyvelo.config import ConfigPipeline, ConfigState, add, delete
from pyvelo.core import ConfigContext, RowsModifiedConfirmation, VcoModel
from pyvelo.core.rebase import RawChange, raw_changes

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from pyvelo.client.module_write import ModulePlan

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

DELETE = "configuration/deleteConfigurationModule"

type DeleteOutcome = RowsModifiedConfirmation | BaseException
"""How a delete ended: its answer as the client read it, or what it raised."""


@dataclass
class Calls:
    """The module inserts and deletes a test sent through the client."""

    inserts: list[InsertConfigurationModule]
    deletes: list[tuple[int, DeleteOutcome]]
    """The module ID of each delete, and how it ended."""


@pytest.fixture
def calls(vco_client: VcoClient, monkeypatch: pytest.MonkeyPatch) -> Calls:
    """Spies over the client's insert and delete, which the write guard is under."""
    seen = Calls(inserts=[], deletes=[])
    insert = vco_client.insert_configuration_module
    delete_module_ = vco_client.delete_configuration_module

    async def spy_insert(
        request: InsertConfigurationModule, *, level: ConfigContext | None = None
    ) -> Any:
        seen.inserts.append(request)
        return await insert(request, level=level)

    async def spy_delete(
        request: DeleteConfigurationModule,
    ) -> RowsModifiedConfirmation:
        try:
            answer = await delete_module_(request)
        except BaseException as error:
            seen.deletes.append((request.id_, error))
            raise
        seen.deletes.append((request.id_, answer))
        return answer

    monkeypatch.setattr(vco_client, "insert_configuration_module", spy_insert)
    monkeypatch.setattr(vco_client, "delete_configuration_module", spy_delete)
    return seen


@dataclass(frozen=True)
class Kit:
    """What every test here works with: the fixtures, as one."""

    lab: Lab
    pipeline: ConfigPipeline
    calls: Calls
    monkeypatch: pytest.MonkeyPatch

    async def load(self, layout: EdgeLayout) -> ConfigState:
        """The state of the edge of *layout*, loaded now."""
        return await self.pipeline.load(
            layout.edge_id, enterprise_id=self.lab.enterprise_id
        )


@pytest.fixture
def kit(
    lab: Lab, pipeline: ConfigPipeline, calls: Calls, monkeypatch: pytest.MonkeyPatch
) -> Kit:
    return Kit(lab, pipeline, calls, monkeypatch)


# ---------------------------------------------------------------------------
# Shared checks
# ---------------------------------------------------------------------------


def _json_object(value: object) -> dict[str, Any]:
    """A deep copy of a JSON object, for a test to read or change freely."""
    return copy.deepcopy(expect(value, dict, "the JSON value"))


def _planned(state: ConfigState, name: Name) -> ModulePlan:
    return next(m for m in state.plan().modules if m.name == name.value)


def _plans_a_change(state: ConfigState, name: Name) -> bool:
    return name.value in [m.name for m in state.plan().changed]


async def _named(site: Site) -> list[AnyStoredModule]:
    """Every module of the site's name in its layer, in the order a read gives them.

    ``Site.read`` gives the first alone.
    """
    return [module for module in await site.layer() if module.name == site.name]


async def _ids_as_listed(site: Site) -> list[int]:
    """The ID of each module of the site's name, as ``getConfiguration`` lists them.

    ``configuration/getConfiguration`` of the site's configuration, with its
    modules, is the read a repeated save and ``revert`` find a module with
    (``find_named_module``); ``_named`` reads the edge's configuration stack.
    """
    request = GetConfiguration(
        id_=site.configuration_id, with_=[GetConfigurationWith.MODULES]
    )
    if site.enterprise_id is not None:
        request.enterprise_id = site.enterprise_id
    modules = (await site.client.get_configuration(request)).modules
    return [
        module.id_
        for module in ([] if modules is MISSING else modules)
        if module.name == site.name
    ]


async def _the_one(site: Site, module_id: int) -> AnyStoredModule:
    """The one module of the site's name, which must be module *module_id*."""
    held = await _named(site)
    check(
        len(held) == 1 and held[0].id_ == module_id,
        f"the edge's configuration holds {len(held)} {site.name.value} module(s), "
        "expected the one the insert stored",
    )
    return held[0]


def _beyond_the_assigned(sent: object, stored: AnyStoredModule) -> list[RawChange]:
    """Where *stored* holds more or other than *sent*, the data of its insert.

    The keys the Orchestrator assigns to a new item (a rule's ID) are left out.
    """
    return [
        change
        for change in raw_changes(sent, stored.raw_data, data_model(stored))
        if not change.path.parts or change.path.parts[-1] not in SERVER_ASSIGNED_KEYS
    ]


def _delete_form(outcome: DeleteOutcome) -> str:
    """How a delete was answered: the row count and whether an error came with it.

    Or the class and codes of the error raised. Nothing of an error's text.
    """
    if isinstance(outcome, VcoOperationError):
        return f"an error ({type(outcome).__name__}: {refusal_codes(outcome)})"
    if isinstance(outcome, BaseException):
        return f"no answer ({type(outcome).__name__})"
    count = "no row count" if outcome.rows is None else f"a row count of {outcome.rows}"
    return f"{count}, {'with' if outcome.error else 'without'} an error"


def _says_deleted(outcome: DeleteOutcome) -> bool:
    """Whether ``revert`` reads *outcome* as the delete done."""
    return (
        isinstance(outcome, RowsModifiedConfirmation)
        and not outcome.error
        and outcome.rows != 0
    )


def _check_untouched(
    label: str, before: AnyStoredModule, after: AnyStoredModule
) -> None:
    """*after*, the stored module read again, is *before* in ID, version and data."""
    check(
        after.id_ == before.id_ and after.version == before.version,
        f"{label}: the stored module is another, or has another version, than "
        "before the refused revert",
    )
    changes = raw_changes(before.raw_data, after.raw_data, data_model(before))
    check(
        not changes,
        f"{label}: the stored module changed in the refused revert: "
        f"{describe(changes)}",
    )


# ---------------------------------------------------------------------------
# What a test inserts
# ---------------------------------------------------------------------------

type OtherWrite = Callable[[dict[str, Any], dict[str, Any]], None]
"""Changes the raw data of the stored module, as another writer would.

The second argument is the raw data of the profile's module of the name.
"""


@dataclass(frozen=True)
class Made:
    """An edit a test made to a new module."""

    undo: Callable[[], Awaitable[None]]
    """Takes the edit back in the state: the module then plans no insert."""
    another_value: OtherWrite | None = None
    """Gives another value to a value the state's module holds."""
    more: OtherWrite | None = None
    """Gives the stored module something the state's lacks, every value the state
    holds left as sent.
    """


type Lack = Callable[[AnyStoredModule], str | None]
"""What the profile's module lacks that a test copies from it, or ``None``.

It is asked of the module as read, before the test writes anything: a test that
lacks what it needs skips there (``_edge_to_empty``).
"""


def _lacks_nothing(_: AnyStoredModule) -> str | None:
    return None


@dataclass(frozen=True)
class Content:
    """What a test puts in a new module of an edge, so that it plans an insert."""

    id: str
    name: Name
    edit: Callable[[ConfigPipeline, ConfigState], Awaitable[Made]]
    lacks: Lack = _lacks_nothing
    """What the edit needs of the profile's module."""
    more_lacks: Lack = _lacks_nothing
    """What ``Made.more`` of the edit needs of it besides."""


def _top_level(raw: object) -> dict[str, object]:
    """A module's raw data as a JSON object; one that is no object has no key."""
    return cast("dict[str, object]", raw) if isinstance(raw, dict) else {}


FIREWALL_FLAGS = (
    "firewall_enabled",
    "firewall_logging_enabled",
    "stateful_firewall_enabled",
)
"""Flags of a firewall module that an edge can hold as its profile does.

None is the flag ``_forward_syslog`` sets, so the module it inserts has none.
"""


def _lacks_a_flag_to_add(profile: AnyStoredModule) -> str | None:
    if any(key in _top_level(profile.raw_data) for key in FIREWALL_FLAGS):
        return None
    return "the profile's firewall module has no flag for the edge's to hold too"


async def _forward_syslog(_: ConfigPipeline, state: ConfigState) -> Made:
    state.edge.firewall.syslog_forwarding.set_local(
        True
    )  # forward logs; none are collected

    async def undo() -> None:
        state.edge.firewall.syslog_forwarding.inherit()

    def another_value(stored: dict[str, Any], _profile: dict[str, Any]) -> None:
        stored["syslog_forwarding"] = False

    def more(stored: dict[str, Any], profile: dict[str, Any]) -> None:
        # A flag the edge then holds exactly as it inherits it: nothing changes
        # for the edge. That the profile has one was asked before the first
        # write (``_lacks_a_flag_to_add``); the module is stored by now, so
        # finding none to add fails the test and skips nothing.
        flag = next(
            (key for key in FIREWALL_FLAGS if key in profile and key not in stored),
            None,
        )
        check(
            flag is not None,
            "the module as stored holds each flag of the profile's that the "
            "test could add to it: the Orchestrator stored the insert with "
            "more than was sent",
        )
        assert flag is not None
        stored[flag] = profile[flag]

    return Made(undo, another_value, more)


async def _limit_nothing(_: ConfigPipeline, state: ConfigState) -> Made:
    # A disabled service rate limit: new content that limits nothing.
    state.edge.qos.service_rate_limit.set_local(
        ServiceRateLimit.model_validate(
            {"enabled": False, "inputType": "percent", "value": 1}
        )
    )

    async def undo() -> None:
        state.edge.qos.service_rate_limit.inherit()

    return Made(undo)


def _rule_list(stored: dict[str, Any], key: str, name: str) -> list[dict[str, Any]]:
    """The list under *key* of the segment entry of *stored* that holds rule *name*."""
    for entry in stored.get("segments") or []:
        rules: list[dict[str, Any]] = entry.get(key) or []
        if any(rule.get("name") == name for rule in rules):
            return rules
    pytest.fail(
        "the stored module does not hold the rule the test added", pytrace=False
    )


def _first_rules(profile: AnyStoredModule) -> list[tuple[str, VcoModel]]:
    """The rule that is first in each segment's list of a profile's module.

    Each with the name of its segment: a firewall module's outbound rules, a
    QoS module's rules.
    """
    if isinstance(profile, FirewallConfigurationModule):
        return [
            (segment.segment.name, segment.outbound[0])
            for segment in profile.data.segments
            if segment.outbound
        ]
    if isinstance(profile, QosConfigurationModule):
        return [
            (segment.segment.name, segment.rules[0])
            for segment in profile.data.segments
            if segment.rules
        ]
    return []


def _sends_refs(rule: VcoModel) -> bool:
    """Whether the insert of a module that holds a copy of *rule* would send refs.

    A plan sends a ref row for each value in the data that a ref row goes
    with. The library declares which fields hold one, and ``ref_owners``
    finds them in a rule as a plan finds them in a module: of all that a
    firewall or a QoS rule holds, it is the hub a QoS rule backhauls to. An
    address group or a port group a rule names is none: the Orchestrator keeps
    those rows itself, and a write sends none of them.
    """
    return bool(ref_owners(rule, ConfigContext.EDGE))


def _rule_to_copy(profile: AnyStoredModule) -> tuple[str, VcoModel] | None:
    """The rule a test copies from a profile's module, with its segment's name.

    Of the rules that are first in a segment's list, the first whose copy is
    inserted as data alone; ``None`` where there is none.
    """
    return next(
        (
            (segment, rule)
            for segment, rule in _first_rules(profile)
            if not _sends_refs(rule)
        ),
        None,
    )


def _lacks_a_rule_to_copy(profile: AnyStoredModule) -> str | None:
    if _rule_to_copy(profile) is not None:
        return None
    name = profile.name
    if not _first_rules(profile):
        return f"the profile's {name} module has no rule to copy"
    return (
        f"each rule that is first in a segment's list of the profile's {name} "
        "module names an object of the enterprise that a ref row goes with (the "
        "hub it backhauls to), so the insert of its copy would send refs; these "
        "tests insert data alone"
    )


async def _copy_the_first_rule(
    pipeline: ConfigPipeline, state: ConfigState, name: Name
) -> Made:
    """Add a copy of a rule that is first in a profile segment's list.

    The copy goes in the edge's new module, under a synthetic name and with no
    ID of its own. It decides nothing new, whichever of an edge's rules and its
    profile's are met first: the rule copied is first in the profile's list, so
    it decides for everything it matches already, and the copy decides the
    same. A firewall rule gets its ``ruleLogicalId`` from the Orchestrator; a
    QoS rule gets one from the library as it is added.

    Which rule it is (``_rule_to_copy``), and that the profile has one, was
    asked before the test's first write (``_lacks_a_rule_to_copy``): finding
    none here, after a write, fails the test and skips nothing.
    """
    firewall = name is Name.FIREWALL
    key = "outbound" if firewall else "rules"
    source = _rule_to_copy(state.profile_config.get_module(name))
    check(
        source is not None,
        f"the profile's {name.value} module as the state loaded it has no rule "
        "to copy, and it had one when the test read it before its first write",
    )
    assert source is not None
    segment_name, rule = source[0], _compact(source[1])
    added = synthetic_name()
    rule["name"] = added
    _ = rule.pop("rule_logical_id", None)

    async def change(patch: object) -> None:
        _ = await pipeline.apply(
            state, module=name.value, segment=segment_name, path=key, patch=patch
        )

    await change({added: add(rule)})

    async def undo() -> None:
        await change({added: delete()})

    def another_value(stored: dict[str, Any], _profile: dict[str, Any]) -> None:
        rules = _rule_list(stored, key, added)
        next(r for r in rules if r.get("name") == added)["name"] = synthetic_name()

    def more(stored: dict[str, Any], _profile: dict[str, Any]) -> None:
        # A second copy, after the first: the first decides, as the profile's did.
        rules = _rule_list(stored, key, added)
        second = copy.deepcopy(next(r for r in rules if r.get("name") == added))
        second["name"] = synthetic_name()
        # Its ID as the library sends a new rule's: none for a firewall rule,
        # which the Orchestrator gives one, and a new one for a QoS rule.
        _ = second.pop("ruleLogicalId", None)
        if not firewall:
            second["ruleLogicalId"] = str(uuid.uuid4())
        rules.append(second)

    return Made(undo, another_value, more)


def _compact(rule: VcoModel) -> dict[str, Any]:
    """A rule model as the patch of ``apply`` takes it."""
    return rule.model_dump(mode="json", by_alias=False, context={"compact": True})


async def _copy_a_firewall_rule(pipeline: ConfigPipeline, state: ConfigState) -> Made:
    return await _copy_the_first_rule(pipeline, state, Name.FIREWALL)


async def _copy_a_qos_rule(pipeline: ConfigPipeline, state: ConfigState) -> Made:
    return await _copy_the_first_rule(pipeline, state, Name.QOS)


FIREWALL_FLAG = Content(
    "firewall-flag", Name.FIREWALL, _forward_syslog, more_lacks=_lacks_a_flag_to_add
)
FIREWALL_RULE = Content(
    "firewall-rule", Name.FIREWALL, _copy_a_firewall_rule, _lacks_a_rule_to_copy
)
QOS_LIMIT = Content("qos-limit", Name.QOS, _limit_nothing)
QOS_RULE = Content("qos-rule", Name.QOS, _copy_a_qos_rule, _lacks_a_rule_to_copy)

CONTENTS = [FIREWALL_FLAG, FIREWALL_RULE, QOS_LIMIT, QOS_RULE]
ONE_PER_MODULE = [FIREWALL_FLAG, QOS_LIMIT]
"""For a case that does not depend on the content: the two that need no rule."""
OTHER_WRITER = [FIREWALL_FLAG, FIREWALL_RULE, QOS_RULE]
"""The contents that say how another writer changes what they stored."""


def _ids(contents: list[Content]) -> list[str]:
    return [content.id for content in contents]


# ---------------------------------------------------------------------------
# The edge, its module gone, and a save whose insert is not answered
# ---------------------------------------------------------------------------


def _comes_back_whole(module: AnyStoredModule) -> bool:
    """A firewall or QoS module that can be deleted and inserted again as it was.

    The restore inserts the data again, and the Orchestrator issues the IDs of
    rules and of ref rows itself, so a module with refs or with a rule ID (a
    QoS module's default rules have them too) would not be the same module. A
    firewall module with inbound (port-forward) or NAT content is left alone as
    well: that is the local content an edge's connectivity can depend on. What
    passes has no rule of its own, so while it is gone the edge meets the same
    rules, its profile's.
    """
    raw = module.raw_data
    top = _top_level(raw)
    return (
        isinstance(module, (FirewallConfigurationModule, QosConfigurationModule))
        and not (isinstance(module.raw_refs, dict) and module.raw_refs)
        and not has_key(raw, "ruleLogicalId")
        and not (top.get("inbound") or top.get("inboundV6"))
        and not has_key(raw, "nat")
        and not has_key(raw, "natV6")
    )


WHOLE = (
    "with no refs, no rule IDs and no inbound or NAT content, which an insert "
    "would not give back or which a lab edge may depend on"
)


NOT_WHOLE_NOW = (
    "module is no longer one that comes back whole from a delete and an "
    "insert, or there is more than one"
)


def _may_be_emptied(held: list[AnyStoredModule]) -> bool:
    """Whether an edge that holds *held*, its modules of a name, can lose them."""
    return len(held) <= 1 and all(_comes_back_whole(module) for module in held)


async def _profile_or_lack(
    lab: Lab, layout: EdgeLayout, name: Name, lacks: list[Lack]
) -> AnyStoredModule | str:
    """The profile's *name* module of the edge of *layout*, or why a test skips it.

    Reads alone: the edge's own modules of the name as they are now (the lab
    was read once, when the run began), and its profile's module, which must
    lack nothing the test copies from it (*lacks*).
    """
    if not _may_be_emptied(await _named(lab.edge_site(layout, name))):
        return f"the edge's {name.value} {NOT_WHOLE_NOW}"
    profile = await lab.profile_site(layout.profile_id, name).read()
    if profile is None:
        return f"the profile of the edge has no {name.value} module"
    return next((lack for ask in lacks if (lack := ask(profile)) is not None), profile)


async def _edge_to_empty(
    lab: Lab, name: Name, lacks: list[Lack]
) -> tuple[EdgeLayout, AnyStoredModule]:
    """An edge that can lose its *name* module, and its profile's module of the name.

    The edge is marked for deleting and inserting modules. Those with no
    *name* module of their own are taken first: nothing is deleted there. Else
    those whose module comes back whole. Everything a test skips for is asked
    here, of each such edge and from reads alone (``_profile_or_lack``), so a
    test skips before anything is written or not at all. Of the edges left,
    the first is taken that has no module, or whose module takes a write of
    its own data: that probe is the first write.
    """
    _ = lab.require_edge_targets(replace_modules=True)
    lacking = lab.edges_lacking(name)
    candidates = lacking or lab.edges_with(
        name, _comes_back_whole, replace_modules=True
    )
    if not candidates:
        pytest.skip(f"no marked edge has a {name.value} module {WHOLE}")
    found: list[tuple[EdgeLayout, AnyStoredModule]] = []
    lack = ""
    for layout in candidates:
        profile = await _profile_or_lack(lab, layout, name, lacks)
        if isinstance(profile, str):
            lack = profile
        else:
            found.append((layout, profile))
    if not found:
        pytest.skip(lack)
    if lacking:
        return found[0]
    taken = await lab.first_writable_or_fail([layout for layout, _ in found], name)
    return next(pair for pair in found if pair[0] is taken)


@dataclass(frozen=True)
class Emptied:
    """A marked edge, inside a ``restoring`` block, with no module of the name."""

    layout: EdgeLayout
    site: Site
    profile: dict[str, Any]
    """The raw data of the profile's module of the name."""
    effective: dict[str, Any]
    """The module as the Orchestrator composes it for the edge, the edge's own gone:
    what the edge goes by before anything is inserted.
    """


@asynccontextmanager
async def _emptied(
    kit: Kit, content: Content, *, more: bool = False
) -> AsyncGenerator[Emptied]:
    """Open ``restoring`` on an edge's module of the content's name, and delete it.

    Whether the lab has what the test needs is asked first, from reads alone
    (``_edge_to_empty``; with *more*, what ``Made.more`` of the content needs
    too): a test skips there, before it has written, or not at all. The module
    is read again inside the block and deleted only where it is still one that
    comes back whole; one that is not, moments after it was read as one, fails
    the test. Leaving the block puts the original back.
    """
    lab, deletes, name = kit.lab, kit.calls.deletes, content.name
    lacks = [content.lacks, content.more_lacks] if more else [content.lacks]
    layout, profile = await _edge_to_empty(lab, name, lacks)
    site = lab.edge_site(layout, name)
    async with restoring(site):
        held = await _named(site)
        check(
            _may_be_emptied(held),
            f"the edge's {name.value} {NOT_WHOLE_NOW}, though it was one when the "
            "test read it a moment ago: nothing was deleted",
        )
        for module in held:
            await delete_module(site, module.id_)
        answered = (
            f": it was answered with {_delete_form(deletes[-1][1])}" if held else ""
        )
        check(
            not await _named(site),
            f"the {name.value} module of the edge is still there after its "
            f"delete{answered}",
        )
        effective = await effective_data(
            lab.client, layout.edge_id, name, lab.enterprise_id
        )
        yield Emptied(layout, site, _json_object(profile.raw_data), effective)


@dataclass(frozen=True)
class Unanswered:
    """A state after a save whose insert of a new module was not answered."""

    state: ConfigState
    made: Made
    plan: ModulePlan
    """The plan the save sent: the insert of the module."""
    inserted: list[int]
    """The ID of the module the insert stored; empty where the insert never left."""


async def _save_unanswered(
    kit: Kit, target: Emptied, content: Content, *, sent: bool
) -> Unanswered:
    """Load the edge, make the edit of *content*, and save with no answer to the insert.

    With *sent* the insert is stored and its answer thrown away; without, it
    never leaves. Either way the save must raise as for a lost answer and leave
    the module's name in ``state.unconfirmed_inserts``. The wrapper that loses
    the answer is taken off again before this returns.
    """
    name = content.name
    state = await kit.load(target.layout)
    check(
        state.find_module(name) is None,
        f"the state loaded after the delete holds a {name.value} module of the edge",
    )
    made = await content.edit(kit.pipeline, state)
    plan = _planned(state, name)
    check(plan.operation == "insert", f"planned {plan.operation}, expected insert")
    # Asked before the first write, of the rule copied (``_sends_refs``): a
    # plan that holds refs all the same fails the test, and nothing is inserted.
    check(
        not plan.refs,
        "the planned insert would send refs, though what the test put in the "
        "module holds nothing a ref row goes with; these tests insert data alone",
    )
    with kit.monkeypatch.context() as patch:
        inserted = losing_the_insert_answer(kit.lab.client, patch, sent=sent)
        with pytest.raises(httpx.ReadTimeout):
            _ = await kit.pipeline.save(state)
    check(
        name.value in state.unconfirmed_inserts and state.module(name).is_new,
        "a save whose insert was not answered did not leave the module new, with "
        "its name in state.unconfirmed_inserts",
    )
    check(
        len(inserted) == (1 if sent else 0),
        f"the save sent {len(inserted)} insert(s), expected {1 if sent else 0}",
    )
    return Unanswered(state, made, plan, inserted)


async def _check_refused(
    label: str, kit: Kit, lost: Unanswered, stored: AnyStoredModule, site: Site
) -> str:
    """``revert`` refuses, and leaves *stored* and the state as they are.

    Returns the refusal's text, for the test to look for words in: it names
    the lab, so it is never shown.
    """
    name = site.name
    run = run_of(kit.lab.client)
    written = run.sent
    kit.calls.deletes.clear()
    with pytest.raises(ValueError) as refused:  # noqa: PT011 (the refusal's text names the lab: the caller looks for words in it, and it is never shown)
        _ = await kit.pipeline.revert(lost.state, module=name.value)
    check(
        type(refused.value) is ValueError,
        f"{label}: revert raised {type(refused.value).__name__}, not its refusal",
    )
    check(
        not kit.calls.deletes and run.sent == written,
        f"{label}: the refused revert sent {run.sent - written} write(s)",
    )
    _check_untouched(label, stored, await _the_one(site, stored.id_))
    check(
        name.value in lost.state.unconfirmed_inserts and lost.state.module(name).is_new,
        f"{label}: the refused revert changed the state",
    )
    return str(refused.value)


async def _check_loaded_again(
    label: str, kit: Kit, target: Emptied, module_id: int
) -> None:
    """A way on that a refusal names: a state loaded again holds the stored module."""
    name = target.site.name
    fresh = await kit.load(target.layout)
    held = fresh.find_module(name)
    check(
        held is not None and not held.is_new,
        f"{label}: a state loaded again does not hold the stored module",
    )
    assert held is not None
    check_as_read(
        f"{label}, loaded again", held, await _the_one(target.site, module_id)
    )
    check(
        not _plans_a_change(fresh, name),
        f"{label}: a state loaded again plans a change of the {name.value} module",
    )


# ---------------------------------------------------------------------------
# 1. The insert was stored: revert deletes it
# ---------------------------------------------------------------------------


async def _revert_the_stored_insert(
    label: str, kit: Kit, lost: Unanswered, site: Site
) -> None:
    """``revert`` of a module whose insert this state sent and the Orchestrator stored.

    A refusal fails the test with where the module as stored holds more or
    other than was sent; a delete answered as not done, with the form of the
    answer.
    """
    deletes = kit.calls.deletes
    cause: BaseException | None = None
    try:
        _ = await kit.pipeline.revert(lost.state, module=site.name.value)
    except ValueError as error:
        if type(error) is not ValueError:
            raise
        # Failed outside the handler: the error must not be chained to the report.
        cause = error.__cause__
    except VcoOperationError:
        if not deletes:
            raise
    else:
        return
    if deletes:
        pytest.fail(
            f"{label}: revert found the module its own insert stored and sent "
            "the delete, and the answer said that nothing was deleted: "
            f"{_delete_form(deletes[-1][1])}",
            pytrace=False,
        )
    held = await _named(site)
    stored_as = (
        "Against the data sent, the module as stored has: "
        f"{describe(_beyond_the_assigned(lost.plan.data, held[0]))} (the keys the "
        "Orchestrator assigns to a new rule left out)"
        if len(held) == 1
        else "The module the insert stored is not the one module of the name there"
    )
    reason = failure_text(cause) if cause is not None else None
    pytest.fail(
        f"{label}: revert refused the module that this state's own insert "
        "stored, so the Orchestrator stored it with more or other than was "
        f"sent. The configuration holds {len(held)} {site.name.value} module(s). "
        f"{stored_as}. The refusal's cause: "
        f"{reason or 'none, the module holds more than was sent'}. Nothing was "
        "deleted. Until the library knows these paths as the Orchestrator's "
        "own, revert refuses every stored insert of this content on this "
        "Orchestrator",
        pytrace=False,
    )


@pytest.mark.live_write
@pytest.mark.parametrize("content", CONTENTS, ids=_ids(CONTENTS))
async def test_revert_deletes_a_new_module_whose_insert_was_stored(
    kit: Kit, capsys: pytest.CaptureFixture[str], content: Content
) -> None:
    """The module the lost insert stored is deleted, and everything is as before it.

    If ``revert`` refuses here, the Orchestrator stored the insert with more or
    other than was sent, at keys the library does not know as the
    Orchestrator's own: the failure names the paths.
    """
    name = content.name
    lab, deletes = kit.lab, kit.calls.deletes
    async with _emptied(kit, content) as target:
        site = target.site
        lost = await _save_unanswered(kit, target, content, sent=True)
        (stored_id,) = lost.inserted
        _ = await _the_one(site, stored_id)
        deletes.clear()

        await _revert_the_stored_insert(content.id, kit, lost, site)

        check(
            [module_id for module_id, _ in deletes] == [stored_id],
            f"{content.id}: revert sent {len(deletes)} delete(s), expected one, of "
            "the module the insert stored",
        )
        record(
            capsys,
            f"revert of a stored insert ({content.id}): the delete was answered "
            f"with {_delete_form(deletes[0][1])}",
        )
        check(
            not await _named(site),
            f"{content.id}: the edge still has a {name.value} module after revert",
        )
        state = lost.state
        check(
            name.value not in state.unconfirmed_inserts
            and state.find_module(name) is None,
            f"{content.id}: after revert the state still holds the module, or its "
            "name in state.unconfirmed_inserts",
        )
        check(
            not _plans_a_change(state, name),
            f"{content.id}: after revert the state plans a change of the module",
        )
        effective = await effective_data(
            lab.client, target.layout.edge_id, name, lab.enterprise_id
        )
        changes = changes_between(name, target.effective, effective)
        check(
            not changes,
            f"{content.id}: what the edge inherits is not as it was before the "
            f"insert was sent: {describe(changes)}",
        )
        check(
            (await kit.load(target.layout)).find_module(name) is None,
            f"{content.id}: a state loaded after revert holds a {name.value} module "
            "of the edge",
        )


# ---------------------------------------------------------------------------
# 2. The insert never left: revert sends nothing
# ---------------------------------------------------------------------------


@pytest.mark.live_write
@pytest.mark.parametrize("content", ONE_PER_MODULE, ids=_ids(ONE_PER_MODULE))
async def test_revert_of_an_insert_that_never_left_sends_nothing(
    kit: Kit, content: Content
) -> None:
    name = content.name
    lab, calls = kit.lab, kit.calls
    async with _emptied(kit, content) as target:
        run = run_of(lab.client)
        written = run.sent
        lost = await _save_unanswered(kit, target, content, sent=False)
        check(
            run.sent == written and not calls.inserts,
            f"{content.id}: the save whose insert never left sent a write",
        )
        answers = record_answers(lab.client, kit.monkeypatch)
        calls.deletes.clear()

        state = await kit.pipeline.revert(lost.state, module=name.value)

        check(
            run.sent == written,
            f"{content.id}: revert sent {run.sent - written} write(s), expected none",
        )
        check(
            not calls.deletes and not any(a.method == DELETE for a in answers),
            f"{content.id}: revert sent a delete though the edge has no module",
        )
        check(
            not await _named(target.site),
            f"{content.id}: the edge has a {name.value} module after revert",
        )
        check(
            name.value not in state.unconfirmed_inserts
            and state.find_module(name) is None,
            f"{content.id}: after revert the state still holds the module, or its "
            "name in state.unconfirmed_inserts",
        )
        check(
            not _plans_a_change(state, name),
            f"{content.id}: after revert the state plans a change of the module",
        )


# ---------------------------------------------------------------------------
# 3. Another writer changed the stored module: refused, nothing deleted
# ---------------------------------------------------------------------------


@pytest.mark.live_write
@pytest.mark.parametrize("variant", ["another-value", "more"])
@pytest.mark.parametrize("content", OTHER_WRITER, ids=_ids(OTHER_WRITER))
async def test_revert_does_not_delete_a_stored_insert_another_writer_changed(
    kit: Kit, content: Content, variant: str
) -> None:
    """A stored insert that is no longer what the state holds stays stored.

    ``another-value``: a value the state's module holds too was given another
    value (the flag turned off again, the rule renamed). ``more``: the stored
    module was given something the state's lacks (a flag the profile has, a
    second copy of the rule), and every value the state holds is as sent.
    """
    label = f"{content.id}, {variant}"
    async with _emptied(kit, content, more=variant == "more") as target:
        site = target.site
        lost = await _save_unanswered(kit, target, content, sent=True)
        (stored_id,) = lost.inserted
        stored = await _the_one(site, stored_id)
        made = lost.made
        change = made.another_value if variant == "another-value" else made.more
        assert change is not None  # each content of OTHER_WRITER says both
        data = _json_object(stored.raw_data)
        change(data, target.profile)
        await write_data(site, stored, data)
        changed = await _the_one(site, stored_id)
        check(
            bool(raw_changes(stored.raw_data, changed.raw_data, data_model(stored))),
            f"{label}: the other writer's change is not in the module as stored",
        )

        refusal = await _check_refused(label, kit, lost, changed, site)
        check(
            "was not deleted" in refusal,
            f"{label}: the refusal does not say that the module was not deleted",
        )
        await _check_loaded_again(label, kit, target, stored_id)


# ---------------------------------------------------------------------------
# 4. The state's module was emptied again: refused, nothing deleted
# ---------------------------------------------------------------------------


@pytest.mark.live_write
@pytest.mark.parametrize("content", ONE_PER_MODULE, ids=_ids(ONE_PER_MODULE))
async def test_revert_does_not_delete_a_stored_insert_whose_edit_the_state_took_back(
    kit: Kit, content: Content
) -> None:
    """A new module that plans nothing cannot be told from another writer's.

    The state's edit is taken back after the insert was stored, so the state's
    module holds nothing a save would insert, and nothing says the stored
    module is this state's: it stays, and the refusal says to load again.
    """
    name = content.name
    async with _emptied(kit, content) as target:
        site = target.site
        lost = await _save_unanswered(kit, target, content, sent=True)
        (stored_id,) = lost.inserted
        stored = await _the_one(site, stored_id)
        await lost.made.undo()
        plan = _planned(lost.state, name)
        check(
            plan.operation == "none",
            f"{content.id}: planned {plan.operation} with the edit taken back, "
            "expected none",
        )

        refusal = await _check_refused(content.id, kit, lost, stored, site)
        check(
            "load the state again" in refusal,
            f"{content.id}: the refusal does not say to load the state again",
        )
        await _check_loaded_again(content.id, kit, target, stored_id)


# ---------------------------------------------------------------------------
# 5. A save repeated after a lost answer inserts no second module
# ---------------------------------------------------------------------------


@pytest.mark.live_write
@pytest.mark.parametrize("content", ONE_PER_MODULE, ids=_ids(ONE_PER_MODULE))
async def test_a_save_repeated_after_a_lost_answer_inserts_no_second_module(
    kit: Kit, content: Content
) -> None:
    """The other way on that a refusal names: save again.

    The repeated save reads the configuration before it inserts, and goes on
    from the module it finds there.
    """
    name = content.name
    inserts = kit.calls.inserts
    async with _emptied(kit, content) as target:
        lost = await _save_unanswered(kit, target, content, sent=True)
        (stored_id,) = lost.inserted
        check(
            len(inserts) == 1,
            f"{content.id}: {len(inserts)} insert(s) sent, expected one",
        )

        # The wrapper that lost the answer is off: this save is answered.
        _ = await kit.pipeline.save(lost.state)

        check(
            len(inserts) == 1,
            f"{content.id}: the repeated save sent {len(inserts) - 1} more "
            "insert(s), expected none",
        )
        stored = await _the_one(target.site, stored_id)
        beyond = _beyond_the_assigned(lost.plan.data, stored)
        check(
            not beyond,
            f"{content.id}: the one module stored holds more or other than the "
            f"plan held: {describe(beyond)}",
        )
        state = lost.state
        held = state.module(name)
        check(
            name.value not in state.unconfirmed_inserts
            and not held.is_new
            and held.id_ == stored_id,
            f"{content.id}: after the repeated save the state does not hold the "
            "stored module, or still has its name in state.unconfirmed_inserts",
        )
        check_as_read(f"{content.id}, saved again", held, stored)


# ---------------------------------------------------------------------------
# 6. What the Orchestrator answers to a delete of a module that is gone
# ---------------------------------------------------------------------------


@pytest.mark.live_write
@pytest.mark.parametrize("content", ONE_PER_MODULE, ids=_ids(ONE_PER_MODULE))
async def test_record_the_answer_to_a_delete_of_a_module_that_is_gone(
    kit: Kit, capsys: pytest.CaptureFixture[str], content: Content
) -> None:
    """``revert`` reads an error, and a row count of 0, as "nothing was deleted".

    It takes any other answer as the delete done. So the delete of a module
    that is gone must be answered with one of those two, or ``revert`` cannot
    tell a delete that failed from one that was done. The module is one this
    run inserted and deleted; the second delete is sent by ``delete_again``.
    """
    name = content.name
    deletes = kit.calls.deletes
    async with _emptied(kit, content) as target:
        site = target.site
        lost = await _save_unanswered(kit, target, content, sent=True)
        (stored_id,) = lost.inserted
        deletes.clear()
        await delete_module(site, stored_id)
        check(
            not await _named(site),
            f"{content.id}: the inserted module is still there after its delete",
        )
        ((_, first),) = deletes
        check(
            _says_deleted(first),
            f"{content.id}: the delete of a module that was there, and is gone "
            f"now, was answered with {_delete_form(first)}, which revert reads as "
            "nothing deleted",
        )

        again: DeleteOutcome
        try:
            again = await delete_again(site, stored_id)
        except VcoOperationError as error:
            again = error
        record(
            capsys,
            f"delete of a {name.value} module: one that is there was answered with "
            f"{_delete_form(first)}; one that is gone with {_delete_form(again)}",
        )
        check(
            not _says_deleted(again),
            f"{content.id}: the delete of a module that is gone was answered with "
            f"{_delete_form(again)}, which revert reads as the delete done: on "
            "this Orchestrator it cannot tell a delete that failed from one that "
            "was done",
        )


# ---------------------------------------------------------------------------
# 7. Whether a configuration takes two modules of one name
# ---------------------------------------------------------------------------


async def _insert(site: Site, data: dict[str, Any]) -> int:
    """Insert a module with *data* through the client; the ID it is answered with."""
    enterprise = site.enterprise_id if site.enterprise_id is not None else MISSING
    request: InsertConfigurationModule
    if site.name is Name.FIREWALL:
        request = InsertFirewallConfigurationModule(
            configuration_id=site.configuration_id,
            data=copy.deepcopy(data),
            enterprise_id=enterprise,
        )
    else:
        request = InsertQosConfigurationModule(
            configuration_id=site.configuration_id,
            data=copy.deepcopy(data),
            enterprise_id=enterprise,
        )
    return (await site.client.insert_configuration_module(request)).id_


async def _delete_each(site: Site, module_ids: list[int]) -> int:
    """Delete each of *module_ids*; how many modules of the name are left then.

    Counted in the site's layer.
    """
    for module_id in dict.fromkeys(module_ids):
        # Whatever one delete raises, the next module is still tried; what is
        # left is counted below.
        with suppress(Exception, pytest.fail.Exception):
            await delete_module(site, module_id)
    return len(await _named(site))


def _left(name: Name, count: int) -> str:
    """What is true of an edge left with *count* *name* modules by the cleanup.

    It does not say that the block's restore puts them right: that deals with
    one module, the first a read gives it, and may fail as the cleanup did.
    """
    stays = (
        f"so at least {count - 1} will stay as the test left them"
        if count > 1
        else "so that one stays as the test left it if the restore fails too"
    )
    return (
        f"{count} {name.value} module(s) are still in the edge's own "
        "configuration after the test's deletes of what it inserted. The "
        "restore that follows deals with one module alone, the first a read "
        "gives it (it deletes that one, or writes the original's data over "
        f"it), {stays}. Read the edge's configuration and delete by hand what "
        "it should not have"
    )


TWO = 2


@pytest.mark.live_write
@pytest.mark.parametrize("content", ONE_PER_MODULE, ids=_ids(ONE_PER_MODULE))
async def test_record_whether_a_configuration_takes_two_modules_of_one_name(
    kit: Kit, capsys: pytest.CaptureFixture[str], content: Content
) -> None:
    """Insert the same small module twice, and record what the second insert does.

    **This test leaves a lab edge with two modules of one name for a moment**,
    where the Orchestrator stores the second: both are deleted again, by ID,
    before the original is put back, and the test fails if one is left. Of two
    modules of a name the library reads the one with the higher ID
    (``find_named_module``), as a repeated save and ``revert`` do; that is
    checked where there are two. The order recorded is the order of that read,
    ``configuration/getConfiguration``.

    **Where a delete of that cleanup fails too, modules of the name stay in the
    edge's own configuration**, and the failure says how many. Leaving the
    block does not put them right: ``restore`` deals with the first module a
    read gives it and with no other (it deletes that one, or writes the
    original's data over it), so with two left, one still holds what the test
    inserted afterwards. Read the edge's configuration and delete by hand what
    it should not have.
    """
    name = content.name
    lab = kit.lab
    async with _emptied(kit, content) as target:
        site = target.site
        state = await kit.load(target.layout)
        _ = await content.edit(kit.pipeline, state)
        plan = _planned(state, name)
        check(plan.operation == "insert", f"planned {plan.operation}, expected insert")
        inserted: list[int] = []
        left = 0
        try:
            inserted.append(await _insert(site, plan.data))
            refusal: str | None = None
            try:
                inserted.append(await _insert(site, plan.data))
            except VcoOperationError as error:
                refusal = f"{type(error).__name__}: {refusal_codes(error)}"
            held = await _ids_as_listed(site)
            if refusal is not None:
                outcome = f"second insert refused ({refusal})"
            elif len(held) == TWO and sorted(held) == sorted(inserted):
                first = "older" if held[0] == min(held) else "newer"
                outcome = (
                    "second insert stored; configuration/getConfiguration, the "
                    "read a repeated save and revert find a module with, lists "
                    f"the {first} of the two first"
                )
            else:
                outcome = (
                    "second insert answered, and the configuration holds "
                    f"{len(held)} module(s) of the name"
                )
            record(capsys, f"two {name.value} modules in one configuration: {outcome}")
            if len(held) > 1:
                found = await find_named_module(
                    lab.client,
                    state.module(name),
                    plan,
                    enterprise_id=lab.enterprise_id,
                )
                check(
                    found is not None and found.id_ == max(held),
                    f"{content.id}: of the {len(held)} modules of the name, "
                    "find_named_module does not give the one with the highest ID",
                )
        finally:
            left = await _delete_each(site, inserted)
            if left:
                record(
                    capsys,
                    f"two {name.value} modules in one configuration: "
                    f"{_left(name, left)}",
                )
        check(not left, f"{content.id}: {_left(name, left)}")
