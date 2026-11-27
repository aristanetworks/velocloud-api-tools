"""``apply`` and ``assign_segment`` refuse a module that holds a detached object.

A detached object (one a write replaced) put back in a live module as it is
would stop an edit half-way; the calls refuse it before anything changes.
"""

import copy
from typing import Any

import pytest
from profile_state import profile_state
from pydantic import MISSING
from pyvelo.client.exceptions import DetachedObjectError
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.state import ConfigState
from segment_helpers import GLOBAL, GUEST, ds_entry, firewall_entry, qos_entry


def put_back_detached(layer: Any, index: int) -> None:
    """Put the segment a write replaced where the live one is, as it is."""
    old = copy.deepcopy(layer)
    held = old.segments[index]
    _ = old.mark_detached()
    layer.segments[index] = held


def dumps(state: ConfigState) -> list[object]:
    modules = state.profile_config.modules
    assert modules is not MISSING
    return [m.model_dump(mode="json", by_alias=True) for m in modules]


async def test_apply_changes_nothing_when_the_module_holds_a_detached_model() -> None:
    state = await profile_state(
        [ds_entry(GLOBAL)], [qos_entry(GLOBAL)], [firewall_entry(GLOBAL)]
    )
    put_back_detached(state.profile.firewall, 0)
    before = dumps(state)
    patch = {
        "firewall_enabled": False,
        "segments": {"Global Segment": {"firewall_logging_enabled": True}},
    }

    with pytest.raises(DetachedObjectError, match="apply: the FirewallSegment"):
        _ = await ConfigPipeline(client=None).apply(
            state, module="firewall", patch=patch
        )

    assert dumps(state) == before
    assert state.plan().changed == ()


@pytest.mark.parametrize("call", ["assign_segment", "apply"])
async def test_an_assignment_with_a_detached_model_changes_no_module(call: str) -> None:
    # Guest is in the device settings and QoS (without its defaults), not the firewall.
    state = await profile_state(
        [ds_entry(GLOBAL), ds_entry(GUEST)],
        [qos_entry(GLOBAL), qos_entry(GUEST, defaults=MISSING)],
        [firewall_entry(GLOBAL)],
    )
    put_back_detached(state.profile.qos, 1)
    before = dumps(state)
    pipeline = ConfigPipeline(client=None)

    if call == "assign_segment":
        attempt = pipeline.assign_segment(state, "Guest")
    else:
        add = {"segments": {"Guest": {"_op": "add"}}}
        attempt = pipeline.apply(state, module="firewall", patch=add)

    with pytest.raises(
        DetachedObjectError,
        match=r"assign_segment: the QosSegment\[Profile\] at segments\[#1\] is a "
        "detached object",
    ):
        _ = await attempt

    assert dumps(state) == before
