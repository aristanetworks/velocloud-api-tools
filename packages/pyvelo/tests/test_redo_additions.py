"""``redo_patch`` of a write that lost a segment the pipeline added to a profile module.

A lost added segment is redone as an ``add`` under its name, with the plan's
edits inside it, so that ``apply`` assigns it again. A lost repaired default is
neither redone nor listed: the next write of the module repairs it again.
"""

import copy
import json
from typing import Any

import pytest
from pydantic import MISSING
from pyvelo.client.exceptions import ModuleWriteError
from pyvelo.client.models.configuration.profile_segments import (
    new_segment_entry,
    with_additions,
)
from pyvelo.client.module_check import check_module_write
from pyvelo.client.module_write import ModulePlan, plan_module
from pyvelo.client.schemas.configuration import QosConfigurationModule
from pyvelo.core import Profile
from segment_helpers import GLOBAL, GUEST, ds_entry, identity, profile_rows, qos_entry


def qos_module(
    *, with_defaults: bool = True, data: object = None
) -> QosConfigurationModule[Profile]:
    """A profile QoS module with the Global Segment, which has the template's ``defaults`` unless *with_defaults* is false.

    With *data*, the module holds that data instead.
    """
    entry = qos_entry(GLOBAL) if with_defaults else qos_entry(GLOBAL, defaults=MISSING)
    row = profile_rows([ds_entry(GLOBAL)], [entry], [])[1]
    if data is not None:
        row["data"] = data
    return QosConfigurationModule[Profile].model_validate(row)


def with_guest(
    module: QosConfigurationModule[Profile], *, edited: bool = False
) -> ModulePlan:
    """The plan of *module* with Guest added, its first rule renamed to ``Box2`` when *edited*."""
    item: Any = module.add_segment_entry(new_segment_entry("QOS", identity(GUEST)))
    if edited:
        item.rules[0].name = "Box2"
    return plan_module(module, via_pipeline=True)


def lost_write(
    module: QosConfigurationModule[Profile], plan: ModulePlan, stored_raw: object
) -> ModuleWriteError:
    """The error of a write of *plan* that the VCO answered with *stored_raw*."""
    stored = qos_module(data=stored_raw)
    with pytest.raises(ModuleWriteError) as caught:
        _ = check_module_write(module, plan, stored)
    return caught.value


def test_a_lost_added_segment_is_redone_as_an_add_with_its_edits():
    module = qos_module()
    plan = with_guest(module, edited=True)
    original = new_segment_entry("QOS", identity(GUEST))["rules"][0]["name"]

    error = lost_write(module, plan, module.raw_data)

    patch = error.redo_patch()
    assert patch == {
        "segments": {
            "Guest": {"_op": "add", "rules": {original: {"name": "Box2"}}},
        }
    }
    assert json.loads(json.dumps(patch)) == patch
    assert error.not_redone() == ()


def test_a_lost_added_segment_with_no_edits_is_a_bare_add():
    module = qos_module()
    plan = with_guest(module)
    assert plan.changes == ()

    error = lost_write(module, plan, module.raw_data)

    assert error.redo_patch() == {"segments": {"Guest": {"_op": "add"}}}
    assert error.not_redone() == ()


def test_a_lost_repaired_default_is_neither_redone_nor_listed():
    module = qos_module(with_defaults=False)
    plan = with_guest(module)
    (missing,) = [m for m in plan.missing_defaults if m.repaired]
    assert missing.segment == "Global Segment"
    # The VCO stored the added segment and dropped the repaired defaults.
    stored = copy.deepcopy(with_additions(module.raw_data, module.additions))
    assert "defaults" not in stored["segments"][0]  # pyright: ignore[reportIndexIssue, reportOptionalSubscript]

    error = lost_write(module, plan, stored)

    assert [str(edit.path) for edit in error.lost_edits] != []
    assert all("defaults" in str(edit.path) for edit in error.lost_edits)
    assert error.redo_patch() == {}
    assert error.not_redone() == ()


def test_a_lost_repaired_default_of_a_list_that_held_some_is_not_redone_either():
    kept = [
        entry
        for entry in qos_entry(GLOBAL)["defaults"]
        if entry["name"] == "Default-Any-UDP"
    ]
    entry = qos_entry(GLOBAL, defaults=kept)
    row = profile_rows([ds_entry(GLOBAL)], [entry], [])[1]
    module = QosConfigurationModule[Profile].model_validate(row)
    plan = with_guest(module)
    (missing,) = [m for m in plan.missing_defaults if m.repaired]
    assert len(missing.names) == 3
    stored = copy.deepcopy(with_additions(module.raw_data, module.additions))

    error = lost_write(module, plan, stored)

    assert len(error.lost_edits) == 3  # the three repaired entries, by name
    assert error.redo_patch() == {}
    assert error.not_redone() == ()
