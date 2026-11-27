"""A redo patch keeps an item's key where its name is the key of an added item.

``ModuleWriteError.redo_patch()`` names each stored item as the stored list
shows it. A name can read as the key of an item the same patch adds: a network
named ``40``, beside a network added with the VLAN ID 40. Under one selector
the two entries would be one, and ``apply`` would write something other than
the lost edits. There the stored item stays under its key or position, and
the patch redoes what the write planned.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest
from pyvelo.client.models.configuration.profile_segments import new_segment_entry
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import QosConfigurationModule
from pyvelo.config import add, at
from pyvelo.core import Profile
from pyvelo.core.patch import PatchError
from segment_helpers import (
    GLOBAL,
    VOICE,
    Segment,
    ds_entry,
    identity,
    profile_rows,
    qos_entry,
)
from test_patch_real_models import (  # pyright: ignore[reportImplicitRelativeImport]
    firewall,
    fw_rule,
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
    from collections.abc import Callable

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


def test_a_twin_segment_keeps_its_position_beside_a_segment_added_under_its_id() -> (
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
    error = failed(module, plan_module(module, via_pipeline=True), raw_copy(module))

    segments = cast("dict[str, dict[str, Any]]", error.redo_patch()["segments"])

    assert sorted(segments) == ["#2", "6"]
    assert sorted(segments["#2"]["rules"]) == ["Video", "Video2"]
    assert segments["6"] == {
        "_op": "add",
        "rules": {entry["rules"][0]["name"]: {"name": "New"}},
    }
    assert error.not_redone() == ()
