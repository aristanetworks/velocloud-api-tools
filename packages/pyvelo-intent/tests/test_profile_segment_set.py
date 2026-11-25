"""A profile's three modules hold the same segments: the order of the write, the rule that keeps them together, and what the plan carries.

Each state is built by ``profile_state``; the additions are made through
``add_segment_entry``, as the pipeline does.
"""

import copy
import json
from typing import Any

import pytest
from profile_state import (  # pyright: ignore[reportImplicitRelativeImport]
    module_store_pipeline,
    profile_state,
)
from pydantic import MISSING
from pyvelo.client.exceptions import SegmentSetError
from pyvelo.client.models.configuration.profile_segments import (
    default_rules,
    new_segment_entry,
)
from pyvelo.client.schemas.configuration import AnyConfigurationModule
from pyvelo.core.layers import Profile
from pyvelo.core.rebase import Path
from pyvelo.testing import make_module_json
from pyvelo_intent.config.state import ConfigPlan, ConfigState
from refs_helpers import QOS_ID
from segment_helpers import (
    GLOBAL,
    GUEST,
    ds_entry,
    firewall_entry,
    identity,
    qos_entry,
)
from test_state_plan import (  # pyright: ignore[reportImplicitRelativeImport]
    make_state,
)

DS = "deviceSettings"
QOS = "QOS"
FIREWALL = "firewall"
ASSIGN_GUEST = 'pipeline.assign_segment(state, "Guest")'
ADD_GUEST = '{"segments": {"Guest": {"_op": "add"}}}'


async def state_of(ds: list[str], qos: list[str], firewall: list[str]) -> ConfigState:
    """A profile state whose modules hold the segments named (``"Global"`` and ``"Guest"``)."""
    chosen = {"Global": GLOBAL, "Guest": GUEST}
    return await profile_state(
        [ds_entry(chosen[n]) for n in ds],
        [qos_entry(chosen[n]) for n in qos],
        [firewall_entry(chosen[n]) for n in firewall],
    )


async def in_all_three() -> ConfigState:
    return await state_of(["Global", "Guest"], ["Global", "Guest"], ["Global", "Guest"])


def add_guest(state: ConfigState, *modules: str) -> None:
    for name in modules:
        _ = state.module(name).add_segment_entry(
            new_segment_entry(name, identity(GUEST))
        )


def modules_of(state: ConfigState) -> list[AnyConfigurationModule[Profile]]:
    modules = state.profile_config.modules
    assert isinstance(modules, list)
    return modules


def written_names(plan: ConfigPlan, module: str) -> list[str]:
    """The names of the segments the plan of *module* writes."""
    (planned,) = [m for m in plan.modules if m.name == module]
    return [s["segment"]["name"] for s in planned.data["segments"]]


# --- the order -------------------------------------------------------------


async def test_device_settings_is_planned_first():
    state = await in_all_three()
    modules_of(state).reverse()
    assert [m.name for m in modules_of(state)] == [FIREWALL, QOS, DS]
    plan = state.plan()
    assert [m.name for m in plan.modules] == [DS, FIREWALL, QOS]


def test_an_edge_plan_puts_device_settings_first_too():
    ds_row = make_module_json(
        DS,
        {"lan": {"networks": [], "interfaces": []}, "segments": []},
        module_id=7,
        configuration_id=10,
    )
    firewall_row = make_module_json(
        FIREWALL, copy.deepcopy({"segments": []}), module_id=1, configuration_id=10
    )
    state = make_state(edge_modules=[firewall_row, ds_row])
    assert [m.name for m in state.plan().modules] == [DS, FIREWALL]


# --- the segment-set rule --------------------------------------------------


async def test_removing_a_segment_raises():
    state = await in_all_three()
    del state.profile.firewall.segments[1]

    with pytest.raises(SegmentSetError) as caught:
        _ = state.plan()

    error = caught.value
    assert error.segments == ("Guest",)
    assert error.modules == (FIREWALL,)
    assert "removes the segment 'Guest' from firewall" in str(error)
    assert "leave it in the data" in str(error)


async def test_removing_an_orphan_from_qos_is_accepted():
    state = await state_of(["Global"], ["Global", "Guest"], ["Global"])
    del state.profile.qos.segments[1]

    plan = state.plan()

    assert [m.name for m in plan.changed] == [QOS]
    assert written_names(plan, QOS) == ["Global Segment"]
    assert plan.segment_warnings == ()


async def test_removing_an_orphan_from_firewall_is_accepted():
    state = await state_of(["Global"], ["Global"], ["Global", "Guest"])
    del state.profile.firewall.segments[1]

    plan = state.plan()

    assert [m.name for m in plan.changed] == [FIREWALL]
    assert plan.segment_warnings == ()


async def test_removing_a_segment_from_device_settings_raises_even_when_qos_lacks_it():
    state = await state_of(["Global", "Guest"], ["Global"], ["Global", "Guest"])
    del state.profile.device_settings.segments[1]

    with pytest.raises(SegmentSetError) as caught:
        _ = state.plan()

    assert caught.value.segments == ("Guest",)
    assert caught.value.modules == (DS,)


async def test_an_addition_in_two_of_three_raises_naming_the_module_that_lacks_it():
    state = await state_of(["Global"], ["Global"], ["Global"])
    add_guest(state, DS, QOS)

    with pytest.raises(SegmentSetError) as caught:
        _ = state.plan()

    error = caught.value
    assert (error.segments, error.modules) == (("Guest",), (FIREWALL,))
    message = str(error)
    assert (
        "adds the segment 'Guest' to deviceSettings and QOS but not to firewall"
        in message
    )
    assert ADD_GUEST in message
    assert ASSIGN_GUEST in message


async def test_an_addition_in_all_three_plans_three_updates_device_settings_first():
    state = await state_of(["Global"], ["Global"], ["Global"])
    add_guest(state, FIREWALL, QOS, DS)

    plan = state.plan()

    assert [(m.name, m.operation) for m in plan.changed] == [
        (DS, "update"),
        (QOS, "update"),
        (FIREWALL, "update"),
    ]
    assert all(m.added_segments == ("Guest",) for m in plan.changed)
    assert plan.segment_warnings == ()
    assert plan.warnings == ()
    (row,) = plan.modules[0].ref_changes
    assert (row.added, row.name) == (True, "deviceSettings:segment")


async def test_a_disagreement_as_read_is_a_warning():
    state = await state_of(["Global", "Guest"], ["Global"], ["Global", "Guest"])

    plan = state.plan()

    (warning,) = plan.segment_warnings
    assert (warning.code, warning.path, warning.severity) == (
        "segments_disagree",
        'segments["Guest"]',
        "warning",
    )
    assert "in deviceSettings and firewall but not in QOS" in warning.message
    assert ADD_GUEST in warning.message
    assert ASSIGN_GUEST in warning.message
    assert plan.warnings[-1] is warning
    assert plan.changed == ()
    assert plan.render().splitlines()[-1] == f"warning: {warning.message}"


async def test_a_profile_without_all_three_modules_skips_the_rule():
    state = await in_all_three()
    state.profile_config.modules = [m for m in modules_of(state) if m.name != FIREWALL]
    del state.profile.qos.segments[1]

    plan = state.plan()

    assert plan.segment_warnings == ()
    assert [m.name for m in plan.changed] == [QOS]


# --- the repair --------------------------------------------------------------


def two_defaults() -> list[dict[str, Any]]:
    return default_rules()[:2]


async def lacking_defaults_state() -> ConfigState:
    """Guest's QoS segment holds two of the four defaults; the three modules agree."""
    return await profile_state(
        [ds_entry(GLOBAL), ds_entry(GUEST)],
        [qos_entry(GLOBAL), qos_entry(GUEST, defaults=two_defaults())],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
    )


def edit_global_qos(state: ConfigState) -> None:
    """A change to QoS that is not Guest's defaults, so the module is written."""
    defaults = state.profile.qos.segments[0].defaults
    assert isinstance(defaults, list)
    defaults[0].action.allow_conditional_bh = True


def guest_defaults(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    (guest,) = [s for s in data["segments"] if s["segment"]["name"] == "Guest"]
    return {d["name"]: d for d in guest["defaults"]}


async def test_the_repair_takes_conditional_backhaul_from_device_settings_as_it_is_now():
    state = await lacking_defaults_state()
    state.profile.device_settings.segments[1].vpn.conditional_backhaul = True
    edit_global_qos(state)

    plan = state.plan()

    qos_plan = next(m for m in plan.modules if m.name == QOS)
    (missing,) = qos_plan.missing_defaults
    assert (missing.segment, missing.repaired) == ("Guest", True)
    defaults = guest_defaults(qos_plan.data)
    repaired = [defaults[n] for n in missing.names]
    assert [d["action"]["allowConditionalBh"] for d in repaired] == [True, True]
    # The two it already held keep their action.
    assert "allowConditionalBh" not in defaults["Default-Internet-UDP"]["action"]


async def test_the_repair_of_a_segment_whose_device_settings_say_no_backhaul():
    state = await lacking_defaults_state()
    edit_global_qos(state)

    qos_plan = next(m for m in state.plan().modules if m.name == QOS)

    (missing,) = qos_plan.missing_defaults
    defaults = guest_defaults(qos_plan.data)
    assert [defaults[n]["action"]["allowConditionalBh"] for n in missing.names] == [
        False,
        False,
    ]


async def test_save_with_repair_defaults_false_sends_no_repair():
    state = await lacking_defaults_state()
    edit_global_qos(state)
    pipeline, store = module_store_pipeline(state)

    result = await pipeline.save(state, repair_defaults=False)

    assert [str(m) for m in result.modules_updated] == [QOS]
    assert list(guest_defaults(store.rows[QOS_ID]["data"])) == [
        "Default-Internet-UDP",
        "Default-Internet-Other",
    ]
    (warning,) = [w for w in result.warnings if w.code == "defaults_repaired"]
    assert "repair is off" in warning.message
    assert result.plan is not None
    (missing,) = next(m for m in result.plan.modules if m.name == QOS).missing_defaults
    assert missing.repaired is False


async def test_dry_run_and_save_plan_the_same_repaired_ids():
    state = await lacking_defaults_state()
    edit_global_qos(state)
    pipeline, store = module_store_pipeline(state)

    dry = await pipeline.save(state, dry_run=True)
    assert dry.plan is not None
    planned = guest_defaults(next(m for m in dry.plan.modules if m.name == QOS).data)
    assert len(planned) == 4
    repaired = ("Default-Any-UDP", "Default-Any-Other")
    ids = {n: planned[n]["ruleLogicalId"] for n in repaired}
    assert len(set(ids.values())) == 2

    _ = await pipeline.save(state)

    stored = guest_defaults(store.rows[QOS_ID]["data"])
    assert {n: stored[n]["ruleLogicalId"] for n in repaired} == ids


# --- what the plan lists -------------------------------------------------------


async def test_get_field_changes_lists_an_added_segment():
    state = await state_of(["Global"], ["Global"], ["Global"])
    add_guest(state, DS, QOS, FIREWALL)

    changes = state.get_field_changes()

    assert [(c.module, str(c.path)) for c in changes] == [
        (DS, "segments[Guest]"),
        (QOS, "segments[Guest]"),
        (FIREWALL, "segments[Guest]"),
    ]
    guest = state.get_field_changes(FIREWALL)[0]
    assert guest.path == Path.parse('segments["Guest"]')
    assert guest.before is MISSING
    (addition,) = state.module(FIREWALL).additions
    assert guest.after == addition.entry  # pyright: ignore[reportAttributeAccessIssue]


async def test_plan_warnings_reach_the_save_result():
    # Guest is in device settings and QoS but not in firewall (a disagreement
    # as read), and its QoS segment lacks two defaults, which the QoS edit repairs.
    state = await profile_state(
        [ds_entry(GLOBAL), ds_entry(GUEST)],
        [qos_entry(GLOBAL), qos_entry(GUEST, defaults=two_defaults())],
        [firewall_entry(GLOBAL)],
    )
    edit_global_qos(state)
    pipeline, _ = module_store_pipeline(state)

    result = await pipeline.save(state, dry_run=True)

    codes = [w.code for w in result.warnings]
    assert "defaults_repaired" in codes
    assert codes[-1] == "segments_disagree"
    assert json.dumps("Guest") in result.warnings[-1].path
