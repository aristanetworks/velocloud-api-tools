"""The save of a segment assigned to a profile: three writes, device settings first, on a ``ModuleStore``.

Each state is built by ``profile_state`` and written through
``module_store_pipeline``. A change another writer makes after the device
settings write is an ``on_store`` closure that calls ``store.other_writer``
once.
"""

import copy
from collections.abc import Callable
from typing import Any

import pytest
from profile_state import (  # pyright: ignore[reportImplicitRelativeImport]
    module_store_pipeline,
    profile_state,
)
from pydantic import MISSING
from pyvelo.client.exceptions import (
    ConfigConflictError,
    SegmentSetError,
    WriteNotAppliedError,
)
from pyvelo.client.models.configuration.profile_segments import default_rules
from pyvelo.core import add
from pyvelo.testing import ModuleStore
from pyvelo_intent.config.state import ConfigState
from refs_helpers import QOS_ID, SEG0_OBJECT, SEG1_OBJECT
from segment_helpers import (
    FW_ID,
    GLOBAL,
    GUEST,
    ds_entry,
    firewall_entry,
    qos_entry,
)

DS_ID = 10
DS = "deviceSettings"
QOS = "QOS"
FIREWALL = "firewall"
UPDATE = "configuration/updateConfigurationModule"
GUEST_ID = GUEST[0]


async def global_only(*, with_defaults: bool = True) -> ConfigState:
    """A profile with the Global Segment in each module; its QoS ``defaults`` are the template's unless *with_defaults* is false."""
    entry = qos_entry(GLOBAL) if with_defaults else qos_entry(GLOBAL, defaults=MISSING)
    return await profile_state([ds_entry(GLOBAL)], [entry], [firewall_entry(GLOBAL)])


def rule(name: str) -> dict[str, Any]:
    """A QoS rule as the wire has it, from the first template rule renamed."""
    made = default_rules()[0]
    made["name"] = name
    return made


def updates(store: ModuleStore) -> list[dict[str, Any]]:
    """The params of every update request, in the order they were sent."""
    return [params for method, params in store.requests if method == UPDATE]


def guest_entry(data: dict[str, Any]) -> dict[str, Any]:
    """The entry of Guest in the module data *data*."""
    (entry,) = [e for e in data["segments"] if e["segment"]["segmentId"] == GUEST_ID]
    return entry


def without_default_ids(entry: dict[str, Any]) -> dict[str, Any]:
    """*entry* with the ``ruleLogicalId`` of each of its ``defaults`` taken out."""
    entry = copy.deepcopy(entry)
    for default in entry["defaults"]:
        del default["ruleLogicalId"]
    return entry


def planned_guest(state: ConfigState, module: str) -> dict[str, Any]:
    """The entry of Guest in the plan of *module*."""
    (planned,) = [m for m in state.plan().modules if m.name == module]
    return copy.deepcopy(guest_entry(planned.data))


def after_device_settings(
    store: ModuleStore, *writes: tuple[int, Callable[[dict[str, Any]], None]]
) -> dict[int, str]:
    """Have *writes* (module ID, edit) made once, by another writer, when the device settings row is stored.

    Returns the versions the modules have after them, by module ID, once made.
    """
    versions: dict[int, str] = {}
    done: list[bool] = []

    def hook(new: dict[str, Any], old: dict[str, Any] | None) -> None:
        del old
        if new["name"] != DS or done:
            return
        done.append(True)
        for module_id, edit in writes:
            store.other_writer(module_id, edit)
            versions[module_id] = store.rows[module_id]["version"]

    store.on_store.append(hook)
    return versions


def a_new_version(row: dict[str, Any]) -> None:
    """Another writer's change that leaves the data as it is."""


def rate_enabled(row: dict[str, Any]) -> None:
    """Another writer's change to a key nobody here edits."""
    row["data"]["serviceRateLimit"] = {
        "enabled": True,
        "inputType": "percent",
        "value": 1,
    }


def guest_stripped(new: dict[str, Any], old: dict[str, Any] | None) -> None:
    """The VCO stores the QoS row without Guest: the write is not applied."""
    del old
    if new["name"] == QOS:
        new["data"]["segments"] = [
            e for e in new["data"]["segments"] if e["segment"]["segmentId"] != GUEST_ID
        ]


async def test_assign_and_save_sends_three_updates_device_settings_first():
    state = await global_only()
    pipeline, store = module_store_pipeline(state)
    _ = await pipeline.assign_segment(state, "Guest")
    planned = planned_guest(state, QOS)
    global_row = copy.deepcopy(store.rows[DS_ID]["refs"]["deviceSettings:segment"])

    result = await pipeline.save(state)

    sent = updates(store)
    assert [store.rows[p["id"]]["name"] for p in sent] == [DS, QOS, FIREWALL]
    assert [str(m) for m in result.modules_updated] == [DS, QOS, FIREWALL]
    first = sent[0]["_update"]
    assert [e["segment"]["name"] for e in first["data"]["segments"]] == [
        "Global Segment",
        "Guest",
    ]
    rows = first["refs"]["deviceSettings:segment"]
    assert [r["enterpriseObjectId"] for r in rows] == [SEG0_OBJECT, SEG1_OBJECT]
    assert rows[0] == global_row  # the row as read
    assert state.plan().changed == ()
    assert guest_entry(store.rows[QOS_ID]["data"]) == planned
    ref_rows = store.rows[DS_ID]["refs"]["deviceSettings:segment"]
    assert sorted(r["enterpriseObjectId"] for r in ref_rows) == [
        SEG0_OBJECT,
        SEG1_OBJECT,
    ]


async def test_a_rebased_module_keeps_its_added_segment_and_repairs():
    state = await global_only(with_defaults=False)
    pipeline, store = module_store_pipeline(state)
    _ = await pipeline.assign_segment(state, "Guest")
    plan = state.plan()
    (qos_plan,) = [m for m in plan.modules if m.name == QOS]
    (repair,) = qos_plan.missing_defaults
    assert repair.repaired and len(repair.names) == 4
    planned = copy.deepcopy(qos_plan.data["segments"])
    versions = after_device_settings(
        store, (QOS_ID, rate_enabled), (FW_ID, a_new_version)
    )
    read = {i: store.rows[i]["version"] for i in (QOS_ID, FW_ID)}

    result = await pipeline.save(state)

    assert [str(m) for m in result.modules_updated] == [DS, QOS, FIREWALL]
    assert versions[QOS_ID] != read[QOS_ID] and versions[FW_ID] != read[FW_ID]
    _, qos_sent, fw_sent = updates(store)
    assert (qos_sent["version"], fw_sent["version"]) == (
        versions[QOS_ID],
        versions[FW_ID],
    )
    assert qos_sent["_update"]["data"]["segments"] == planned  # Guest, and the repair
    assert qos_sent["_update"]["data"]["serviceRateLimit"]["enabled"] is True
    assert guest_entry(qos_sent["_update"]["data"]) == guest_entry(
        store.rows[QOS_ID]["data"]
    )
    stored_global = store.rows[QOS_ID]["data"]["segments"][0]
    assert [d["name"] for d in stored_global["defaults"]] == list(repair.names)
    assert guest_entry(fw_sent["_update"]["data"])["segment"]["name"] == "Guest"
    assert state.plan().changed == ()


async def test_a_fresh_module_that_already_has_the_segment_is_a_conflict():
    state = await global_only()
    pipeline, store = module_store_pipeline(state)
    _ = await pipeline.assign_segment(state, "Guest")

    def holds_guest(row: dict[str, Any]) -> None:
        row["data"]["segments"].append(qos_entry(GUEST))

    _ = after_device_settings(store, (QOS_ID, holds_guest))

    with pytest.raises(ConfigConflictError) as caught:
        _ = await pipeline.save(state)

    error = caught.value
    assert (error.stated, error.sent) == (False, False)
    assert error.module_name == QOS
    assert "holds segment 'Guest'" in error.detail
    assert [store.rows[p["id"]]["name"] for p in updates(store)] == [DS]
    assert [w.name for w in error.completed] == [DS]


async def test_a_qos_write_not_applied_leaves_a_plan_that_raises_segment_set_error():
    # The repaired defaults of Global are stored, so the write is applied in part.
    state = await global_only(with_defaults=False)
    pipeline, store = module_store_pipeline(state)
    _ = await pipeline.assign_segment(state, "Guest")
    store.on_store.append(guest_stripped)

    with pytest.raises(WriteNotAppliedError) as caught:
        _ = await pipeline.save(state)

    assert [w.name for w in caught.value.completed] == [DS]
    assert [store.rows[p["id"]]["name"] for p in updates(store)] == [DS, QOS]
    with pytest.raises(SegmentSetError) as raised:
        _ = state.plan()
    assert QOS in raised.value.modules
    assert raised.value.segments == ("Guest",)


async def test_the_redo_of_a_lost_added_segment_assigns_it_again():
    state = await global_only(with_defaults=False)
    pipeline, store = module_store_pipeline(state)
    _ = await pipeline.apply(
        state,
        module="qos",
        patch={"segments": {"Guest": add({"rules": [rule("Box"), rule("Video")]})}},
    )
    planned = planned_guest(state, QOS)
    store.on_store.append(guest_stripped)
    with pytest.raises(WriteNotAppliedError) as caught:
        _ = await pipeline.save(state)
    error = caught.value
    store.on_store.remove(guest_stripped)
    store.requests.clear()

    patch = error.redo_patch()
    assert patch["segments"]["Guest"]["_op"] == "add"  # pyright: ignore[reportIndexIssue]
    assert error.not_redone() == ()
    _ = await pipeline.apply(state, module=error.module_name, patch=patch)
    result = await pipeline.save(state)

    assert [str(m) for m in result.modules_updated] == [QOS, FIREWALL]
    assert [store.rows[p["id"]]["name"] for p in updates(store)] == [QOS, FIREWALL]
    stored = guest_entry(store.rows[QOS_ID]["data"])
    assert [r["name"] for r in stored["rules"]] == ["Box", "Video"]
    # The assignment is made again, so its defaults have new IDs; the rules the
    # redo carried keep theirs.
    assert without_default_ids(stored) == without_default_ids(planned)
    assert state.plan().changed == ()
