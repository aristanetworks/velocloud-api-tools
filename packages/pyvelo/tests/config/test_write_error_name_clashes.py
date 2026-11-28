"""A redo patch keeps an item's key where its name is the key of an added item.

``ModuleWriteError.redo_patch()`` names each stored item as the stored list
shows it. A name can read as the key of an item the same patch adds: a network
named ``40``, beside a network added with the VLAN ID 40. Under one selector
the two entries would be one, and ``apply`` would write something other than
the lost edits. There the stored item stays under its key or position, and
the patch redoes what the write planned.

A name is given to a stored item only. A segment of which nothing is stored
keeps the plan's key, and so do the items in it: its key may read in the
stored list as another segment's ID (a segment named ``4``, beside ``Voice``
with the ID 4), and under that segment's name its edits would be written to
that segment.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, cast

import pytest
from profile_state import module_store_pipeline, profile_state
from pyvelo.client.exceptions import ConfigConflictError, ModuleWriteError
from pyvelo.client.models.configuration.profile_segments import new_segment_entry
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import QosConfigurationModule
from pyvelo.config import add, at
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.core import Profile
from pyvelo.core.identity import NetworkServiceIdentity
from pyvelo.core.patch import PatchError
from pyvelo.testing import MockVcoClient
from refs_helpers import (
    DNS_A,
    QOS_ID,
    dns,
    ds_module,
    segment_entry,
    segment_row,
    table_registry,
)
from refs_helpers import SEGMENT as SEGMENT_REF
from segment_helpers import (
    GLOBAL,
    VOICE,
    Segment,
    ds_entry,
    firewall_entry,
    identity,
    profile_rows,
    qos_entry,
)
from test_patch_real_models import (  # pyright: ignore[reportImplicitRelativeImport]
    SEGMENT,
    desc,
    firewall,
    fw_rule,
    make_state,
)
from test_profile_segment_save import (  # pyright: ignore[reportImplicitRelativeImport]
    rule as template_rule,
)
from test_shown_plan import (  # pyright: ignore[reportImplicitRelativeImport]
    lan_state,
    net,
)
from test_write_back_real_models import (  # pyright: ignore[reportImplicitRelativeImport]
    DENY,
    MODULE,
    other_writer_edit,
    outbound_ops,
)
from test_write_error_names import (  # pyright: ignore[reportImplicitRelativeImport]
    Lost,
    as_read,
    build_name,
    failed,
    firewall_conflict,
    in_the_vcos_form,
    lan_error,
    raw_copy,
    redone,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from pyvelo.client.models.configuration.device_settings.lan import LanNetwork
    from pyvelo.client.schemas.configuration import AnyStoredModule
    from pyvelo.config.state import ConfigState

    type Edit = Callable[[list[LanNetwork]], None]

NAMED_40 = (net(10, "40"), net(20, "Voice"), net(30, "Guests"))
"""VLAN 10 is named ``40``: its name reads as the key of a network with the
VLAN ID 40, which each write below adds."""


def lab(networks: list[LanNetwork], vlan_id: int = 40) -> LanNetwork:
    """A new network ``Lab`` with the VLAN ID *vlan_id*."""
    return type(networks[0]).model_validate(net(vlan_id, "Lab"))


def planned(state: ConfigState) -> list[tuple[object, ...]]:
    """What *state* plans, as ``redone`` gives it."""
    return [
        (module.name, module.changes, module.ref_changes, module.render())
        for module in state.plan().changed
    ]


async def networks_lost(
    networks: tuple[dict[str, Any], ...], edit: Edit
) -> tuple[Lost, list[tuple[object, ...]]]:
    """A write of *edit* to *networks* of which the VCO stored nothing.

    Its error with a state that holds the module as stored, and what the write
    planned.
    """
    state = await lan_state(*networks)
    edit(state.profile.device_settings.lan.networks)
    written = planned(state)
    error = lan_error(state, as_read(state))
    fresh = await lan_state(*networks)
    fresh.replace_module(
        fresh.module("deviceSettings"), cast("AnyStoredModule", error.module)
    )
    return Lost(error, fresh), written


def network_ops(lost: Lost) -> dict[str, dict[str, Any]]:
    """The item operations the redo patch of *lost* has for ``lan.networks``."""
    lan = cast("dict[str, dict[str, dict[str, Any]]]", lost.error.redo_patch()["lan"])
    return lan["networks"]


def added_in_front_and_edited(networks: list[LanNetwork]) -> None:
    networks.insert(0, lab(networks))
    networks[1].cidr_prefix = 25


def edited_and_added_behind(networks: list[LanNetwork]) -> None:
    networks[0].cidr_prefix = 25
    networks.append(lab(networks))


def added_in_front_and_renamed(networks: list[LanNetwork]) -> None:
    networks.insert(0, lab(networks))
    networks[1].name = "Users"


def added_in_front_and_deleted(networks: list[LanNetwork]) -> None:
    networks.insert(0, lab(networks))
    del networks[1]


def deleted_and_added_behind(networks: list[LanNetwork]) -> None:
    del networks[0]
    networks.append(lab(networks))


def added_in_front_and_moved_last(networks: list[LanNetwork]) -> None:
    networks.append(networks.pop(0))
    networks.insert(0, lab(networks))


def moved_last_and_added_behind(networks: list[LanNetwork]) -> None:
    networks.append(networks.pop(0))
    networks.append(lab(networks))


def added_in_front_and_another_after_it(networks: list[LanNetwork]) -> None:
    """VLAN 50 is added after VLAN 10, whose name is the key of the other add."""
    networks.insert(0, lab(networks))
    networks.insert(2, lab(networks, 50))


def added_in_front_and_one_moved_after_it(networks: list[LanNetwork]) -> None:
    """``Guests`` is moved after VLAN 10, whose name is the key of the add."""
    networks.insert(1, networks.pop(2))
    networks.insert(0, lab(networks))


CLASHES = [
    added_in_front_and_edited,
    added_in_front_and_renamed,
    added_in_front_and_deleted,
    deleted_and_added_behind,
    added_in_front_and_moved_last,
    moved_last_and_added_behind,
]
"""Writes that have an entry for VLAN 10 and add VLAN 40: under the name of
VLAN 10 the two are one entry."""
NO_CLASH = [
    edited_and_added_behind,
    added_in_front_and_another_after_it,
    added_in_front_and_one_moved_after_it,
]
"""Writes that are redone under either selector of VLAN 10. Where its entry is
written before the add, ``patch_of`` gives an add whose selector is taken a new
item's selector; and the item another goes after is read in the list as
stored, which has no VLAN 40."""


@pytest.mark.parametrize("edit", [*CLASHES, *NO_CLASH], ids=build_name)
async def test_an_item_named_as_the_key_of_an_added_item_is_redone_with_the_add(
    edit: Edit,
) -> None:
    """Each write is made twice, and one state takes the patch in the VCO's form."""
    (named, written), (in_keys, _) = (
        await networks_lost(NAMED_40, edit),
        await networks_lost(NAMED_40, edit),
    )

    redo = await redone(named, named.error.redo_patch())

    assert named.error.not_redone() == ()
    assert redo == written
    assert redo == await redone(in_keys, in_the_vcos_form(in_keys.error))


@pytest.mark.parametrize(
    "edit", [added_in_front_and_edited, edited_and_added_behind], ids=build_name
)
async def test_an_item_named_as_the_key_of_an_added_item_stays_under_its_key(
    edit: Edit,
) -> None:
    """Whichever of the two the patch writes first."""
    lost, _ = await networks_lost(NAMED_40, edit)

    ops = network_ops(lost)

    assert sorted(ops) == ["10", "40"]
    assert ops["10"] == {"cidr_prefix": 25}
    assert (ops["40"]["_op"], ops["40"]["name"], ops["40"]["cidr_prefix"]) == (
        "add",
        "Lab",
        24,
    )


async def test_a_patch_names_one_item_by_its_key_and_the_others_by_name() -> None:
    """Only the item whose name is the added item's key is under its key."""

    def edit(networks: list[LanNetwork]) -> None:
        networks.insert(0, lab(networks))
        networks[1].cidr_prefix = 25
        networks[2].cidr_prefix = 26
        networks.append(networks.pop(1))

    lost, written = await networks_lost(NAMED_40, edit)

    ops = network_ops(lost)

    assert sorted(ops) == ["10", "40", "Voice"]
    assert ops["10"] == {"cidr_prefix": 25, "_position": {"after": "Guests"}}
    assert ops["Voice"] == {"cidr_prefix": 26}
    assert await redone(lost, lost.error.redo_patch()) == written


async def test_a_name_that_is_the_key_of_a_stored_item_is_never_its_selector() -> None:
    """VLAN 10 is named ``30``, the key of a stored network: it is under ``10``.

    So is VLAN 30 under its key, since its name is the key of the network
    added. No two of the four entries share a selector.
    """
    networks = (net(10, "30"), net(20, "Voice"), net(30, "40"))

    def edit(networks: list[LanNetwork]) -> None:
        networks.insert(0, lab(networks))
        networks[1].cidr_prefix = 25
        networks[2].cidr_prefix = 26
        networks[3].cidr_prefix = 27

    lost, written = await networks_lost(networks, edit)

    ops = network_ops(lost)

    assert sorted(ops) == ["10", "30", "40", "Voice"]
    assert [ops[key].get("cidr_prefix") for key in ("10", "Voice", "30", "40")] == [
        25,
        26,
        27,
        24,
    ]
    assert await redone(lost, lost.error.redo_patch()) == written


async def twin_edited_and_a_rule_added_under_its_id() -> Lost:
    """A rule ``l2`` added first, and an edit to the twin whose logical ID is ``l2``."""
    raw = firewall(
        fw_rule("d", ruleLogicalId="l1"),
        fw_rule("d", ruleLogicalId="l2"),
        fw_rule("x", ruleLogicalId="l3"),
    )
    patch = {
        "+": add({**DENY, "name": "l2"}, first=True),
        at(1): {"logging_enabled": True},
    }
    return await firewall_conflict(raw, patch, other_writer_edit)


async def test_a_twin_rule_keeps_its_position_beside_a_rule_added_under_its_id() -> (
    None
):
    """A rule that shares its name is named by its logical ID, here ``l2``.

    A rule added with the name ``l2`` has that selector, so the twin stays
    under its position, in a list two levels down.
    """
    lost = await twin_edited_and_a_rule_added_under_its_id()

    ops = outbound_ops(lost.error.redo_patch())

    assert sorted(ops) == ["#1", "l2"]
    assert ops["#1"] == {"logging_enabled": True}
    assert (ops["l2"]["_op"], ops["l2"]["logging_enabled"]) == ("add", False)


async def test_an_add_under_another_rules_id_is_refused_as_in_the_vcos_form() -> None:
    """``apply`` takes no add under a selector that is a stored rule's logical ID.

    So this redo is refused, as the same patch in the VCO's keys is: the
    twin's edit is not set on the new rule.
    """
    named = await twin_edited_and_a_rule_added_under_its_id()
    in_keys = await twin_edited_and_a_rule_added_under_its_id()

    with pytest.raises(PatchError) as refused:
        _ = await redone(named, named.error.redo_patch())
    with pytest.raises(PatchError) as refused_in_keys:
        _ = await redone(in_keys, in_the_vcos_form(in_keys.error))

    assert "'l2' is already in the list as an alt key" in str(refused.value)
    assert str(refused.value) == str(refused_in_keys.value)


VOICE_TWIN: Segment = (6, "Voice", "66666666-6666-4666-8666-666666666666", 506)
"""A second segment named ``Voice``, which is therefore named by its ID, ``6``."""
SIX: Segment = (9, "6", "99999999-9999-4999-8999-999999999999", 509)
"""A segment whose name is the ID of ``VOICE_TWIN``."""
FOUR: Segment = (9, "4", "99999999-9999-4999-8999-999999999999", 509)
"""A segment whose name is the ID of ``VOICE``."""


async def qos_state(
    entries: list[dict[str, Any]], more: tuple[Segment, ...]
) -> ConfigState:
    """A profile whose QoS module has *entries*; its registry knows *more* too."""
    return await profile_state(
        [ds_entry(GLOBAL)], entries, [firewall_entry(GLOBAL)], segments=(GLOBAL, *more)
    )


def as_stored_in(state: ConfigState, error: ModuleWriteError) -> Lost:
    """*error* with *state*, which is given the module as stored for its QoS."""
    state.replace_module(state.module("QOS"), cast("AnyStoredModule", error.module))
    return Lost(error, state)


def qos_text(planned: list[tuple[object, ...]]) -> object:
    """The text of the QoS module's plan in what ``redone`` gives."""
    (text,) = [text for name, *_, text in planned if name == "QOS"]
    return text


async def test_a_twin_segment_keeps_its_position_beside_a_segment_added_under_its_id() -> (  # noqa: E501 (a test's name)
    None
):
    """The redo adds a lost added segment again under its name, here ``6``."""
    row = profile_rows([ds_entry(GLOBAL)], [qos_entry(GLOBAL)], [])[1]
    twins = [
        qos_entry(segment, rules=[template_rule("Video")])
        for segment in (VOICE, VOICE_TWIN)
    ]
    row["data"] = {"segments": [qos_entry(GLOBAL), *twins]}
    module = QosConfigurationModule[Profile].model_validate(row)
    module.data.segments[2].rules[0].name = "Video2"
    entry = new_segment_entry("QOS", identity(SIX))
    new: Any = module.add_segment_entry(entry)
    new.rules[0].name = "New"
    plan = plan_module(module, via_pipeline=True)
    error = failed(module, plan, raw_copy(module))

    segments = cast("dict[str, dict[str, Any]]", error.redo_patch()["segments"])

    assert sorted(segments) == ["#2", "6"]
    assert sorted(segments["#2"]["rules"]) == ["Video", "Video2"]
    assert segments["6"] == {
        "_op": "add",
        "rules": {entry["rules"][0]["name"]: {"name": "New"}},
    }
    assert error.not_redone() == ()
    stored = await qos_state([qos_entry(GLOBAL), *twins], (VOICE, VOICE_TWIN, SIX))
    lost = as_stored_in(stored, error)
    assert qos_text(await redone(lost, error.redo_patch())) == plan.render()


def segments_of(error: ModuleWriteError) -> dict[str, dict[str, Any]]:
    """The entries the redo patch of *error* has for ``segments``."""
    return cast("dict[str, dict[str, Any]]", error.redo_patch()["segments"])


def rule_names(row: dict[str, Any]) -> dict[str, list[str]]:
    """The names of the rules of each segment of the QoS *row* of a store."""
    return {
        entry["segment"]["name"]: [rule["name"] for rule in entry["rules"]]
        for entry in row["data"]["segments"]
    }


def without_segment_nine(new: dict[str, Any], old: dict[str, Any] | None) -> None:
    """The VCO stores the QoS row without the segment of ID 9."""
    del old
    if new["name"] == "QOS":
        new["data"]["segments"] = [
            entry
            for entry in new["data"]["segments"]
            if entry["segment"]["segmentId"] != FOUR[0]
        ]


async def test_the_rules_of_a_lost_added_segment_are_redone_in_it() -> None:
    """A segment named ``4`` is added by text beside ``Voice``, whose ID is 4.

    The VCO stores none of it. Its key reads in the stored list as ``Voice``,
    which has the template's rules: the redo names it ``4`` all the same, so
    its two rules are written to it and ``Voice`` keeps its own.
    """
    registry = (GLOBAL, VOICE, FOUR)
    template = new_segment_entry("QOS", identity(VOICE))["rules"]
    state = await profile_state(
        [ds_entry(GLOBAL), ds_entry(VOICE)],
        [qos_entry(GLOBAL), qos_entry(VOICE, rules=template)],
        [firewall_entry(GLOBAL), firewall_entry(VOICE)],
        segments=registry,
    )
    pipeline, store = module_store_pipeline(state, segments=registry)
    rules = [template_rule("Box"), template_rule("Video")]
    _ = await pipeline.apply(
        state,
        module="business_policy",
        patch={"segments": {"4": add({"rules": rules})}},
    )
    store.on_store.append(without_segment_nine)
    with pytest.raises(ModuleWriteError) as caught:
        _ = await pipeline.save(state)
    error = caught.value
    store.on_store.remove(without_segment_nine)

    segments = segments_of(error)

    assert sorted(segments) == ["4"]
    assert segments["4"]["_op"] == "add"
    assert error.not_redone() == ()
    _ = await pipeline.apply(state, module=error.module_name, patch=error.redo_patch())
    _ = await pipeline.save(state)
    assert rule_names(store.rows[QOS_ID]) == {
        "Global Segment": [],
        "Voice": [rule["name"] for rule in template],
        "4": ["Box", "Video"],
    }


async def test_an_edit_of_the_segment_of_that_id_stays_beside_the_added_segments() -> (
    None
):
    """``Voice`` and the lost segment ``4`` each had a rule renamed.

    Under one entry the second rename would overwrite the first.
    """
    entries = [qos_entry(GLOBAL), new_segment_entry("QOS", identity(VOICE))]
    state = await qos_state(entries, (VOICE, FOUR))
    module = state.module("QOS")
    data: Any = module.data
    data.segments[1].rules[0].name = "StoredEdited"
    entry = new_segment_entry("QOS", identity(FOUR))
    new: Any = module.add_segment_entry(entry)
    new.rules[0].name = "NewEdited"
    plan = plan_module(module, via_pipeline=True)
    error = failed(module, plan, raw_copy(module))
    first = entry["rules"][0]["name"]

    assert error.redo_patch() == {
        "segments": {
            "Voice": {"rules": {first: {"name": "StoredEdited"}}},
            "4": {"_op": "add", "rules": {first: {"name": "NewEdited"}}},
        }
    }
    assert error.not_redone() == ()
    lost = as_stored_in(await qos_state(entries, (VOICE, FOUR)), error)
    assert qos_text(await redone(lost, error.redo_patch())) == plan.render()


def edge_segments(*segments: tuple[int, str, str]) -> dict[str, Any]:
    """An edge's firewall data: each segment by ID and name, with one rule."""
    return {
        "segments": [
            {"segment": desc(segment_id, name), "outbound": [fw_rule(rule)]}
            for segment_id, name, rule in segments
        ]
    }


def profile_segments(*segments: tuple[int, str]) -> dict[str, Any]:
    """A profile's firewall data: each segment by ID and name, with no rule."""
    return {
        "segments": [
            {"segment": desc(segment_id, name), "outbound": []}
            for segment_id, name in segments
        ]
    }


async def a_rule_added_in_a_reconciled_segment(
    edge: dict[str, Any], profile: dict[str, Any], *, edited: int
) -> Lost:
    """A rule ``r`` added in the profile's last segment, which the edge lacks.

    The first rule of the edge's segment at *edited* is edited too, and
    another writer's key is stored. The new segment is reached through the
    state: no text selects a segment whose name reads as another one's ID.
    """
    state = make_state({"firewall": edge}, {"firewall": profile})
    client = MockVcoClient.for_state(state, conditional_check=True)
    data = state.edge.firewall
    rule = type(data.segments[0].outbound[0]).model_validate(fw_rule("r"))
    data.segment(profile["segments"][-1]["segment"]["name"]).outbound.append(rule)
    data.segments[edited].outbound[0].logging_enabled = True
    client.store.other_writer(MODULE, other_writer_edit)
    with pytest.raises(ConfigConflictError) as caught:
        _ = await ConfigPipeline(client).save(state)
    return Lost(caught.value, state)


async def named_as_the_global_segments_id() -> Lost:
    """The edge has the Global Segment; the profile also a segment named ``0``."""
    return await a_rule_added_in_a_reconciled_segment(
        edge_segments((0, SEGMENT, "a")),
        profile_segments((0, SEGMENT), (2, "0")),
        edited=0,
    )


async def named_as_the_id_of_voice() -> Lost:
    """The edge has ``Voice`` with the ID 4; the profile also a segment named ``4``."""
    return await a_rule_added_in_a_reconciled_segment(
        edge_segments((0, SEGMENT, "a"), (4, "Voice", "v")),
        profile_segments((0, SEGMENT), (4, "Voice"), (9, "4")),
        edited=1,
    )


async def named_as_the_id_of_a_twin() -> Lost:
    """The edge has two segments named ``Voice``; the profile also one named ``6``.

    The second ``Voice`` has the ID 6, and is shown by it.
    """
    return await a_rule_added_in_a_reconciled_segment(
        edge_segments((0, SEGMENT, "a"), (4, "Voice", "v"), (6, "Voice", "w")),
        profile_segments((0, SEGMENT), (4, "Voice"), (6, "Voice"), (9, "6")),
        edited=2,
    )


RECONCILED = [
    pytest.param(
        named_as_the_global_segments_id, "0", SEGMENT, "a", "Global Segment", id="0"
    ),
    pytest.param(named_as_the_id_of_voice, "4", "Voice", "v", "Voice", id="4"),
    pytest.param(named_as_the_id_of_a_twin, "6", "#2", "w", "Voice", id="6"),
]
"""A write, the key of its reconciled segment, the selector of the stored
segment that has its ID, the rule edited there, and that segment's name."""


@pytest.mark.parametrize(("build", "new", "stored", "rule", "name"), RECONCILED)
async def test_a_reconciled_segment_named_as_a_stored_segments_id_keeps_its_key(
    build: Callable[[], Awaitable[Lost]], new: str, stored: str, rule: str, name: str
) -> None:
    """Its rule is not put into the entry of the stored segment.

    ``apply`` reads the key as that segment and refuses two selectors for it,
    as it refuses this patch in the VCO's keys: nothing is written.
    """
    lost = await build()

    segments = segments_of(lost.error)

    assert sorted(segments) == sorted([new, stored])
    assert segments[stored] == {"outbound": {rule: {"logging_enabled": True}}}
    assert list(segments[new]) == ["outbound"]
    assert [(key, op["_op"]) for key, op in segments[new]["outbound"].items()] == [
        ("r", "add")
    ]
    with pytest.raises(PatchError) as refused:
        _ = await redone(lost, lost.error.redo_patch())
    assert f"names segment {name!r} too: one selector per segment" in str(refused.value)


def provider_stub(segment: Segment) -> dict[str, Any]:
    """A device settings entry of *segment* with a primary provider not set."""
    segment_id, name, logical_id, _ = segment
    return segment_entry(segment_id, name, logical_id, dns=dns(primary=True))


async def provider_lost(
    stored: tuple[Segment, ...],
    new: Segment,
    *,
    set_in: int,
    also_stored: tuple[Segment, ...] = (),
) -> ModuleWriteError:
    """A provider and a DNS source interface set in one segment; nothing is stored.

    The device settings have the segments *stored*, and *new* is added to
    them. Both values are set in the segment at *set_in*. The module as
    stored has the segments *also_stored* in front of the last of *stored*:
    another writer's.
    """
    rows = [segment_row(segment[0], segment[3], segment[2]) for segment in stored]
    module = ds_module(
        [provider_stub(segment) for segment in stored], {SEGMENT_REF: rows}
    )
    entry = new_segment_entry("deviceSettings", identity(new))
    entry["dns"] = dns(primary=True)
    _ = module.add_segment_entry(entry)
    section: Any = module.data.segments[set_in].dns
    section.primary_provider.service = uuid.UUID(DNS_A)
    section.source_interface = "GE1"
    service = NetworkServiceIdentity("dns-a", uuid.UUID(DNS_A), 701, "dns")
    resolvers = await table_registry(
        service, *(identity(segment) for segment in (*stored, new))
    )
    plan = plan_module(module, via_pipeline=True, resolvers=resolvers)
    raw = raw_copy(module)
    raw["segments"][-1:-1] = [provider_stub(segment) for segment in also_stored]
    return failed(module, plan, raw)


PROVIDER_SET = {"source_interface": "GE1", "primary_provider": {"service": DNS_A}}


async def test_a_provider_of_a_lost_added_segment_is_set_in_it() -> None:
    """Its field is set in the entry that adds the segment, not in ``Voice``."""
    error = await provider_lost((GLOBAL, VOICE), FOUR, set_in=2)

    assert error.redo_patch() == {
        "segments": {"4": {"_op": "add", "dns": PROVIDER_SET}}
    }


async def test_a_provider_is_set_under_the_position_its_twin_segment_keeps() -> None:
    """Another writer stored a second ``Voice``, so the one edited is shown by its ID.

    That is ``6``, the name of the segment the write added: the segment stays
    under its position, and its provider is set there, not on the new segment.
    """
    error = await provider_lost(
        (GLOBAL, VOICE_TWIN), SIX, set_in=1, also_stored=(VOICE,)
    )

    assert error.redo_patch() == {
        "segments": {"#2": {"dns": PROVIDER_SET}, "6": {"_op": "add"}}
    }
