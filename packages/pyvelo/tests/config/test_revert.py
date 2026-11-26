"""``ConfigPipeline.revert`` takes back the edits to one module of a state.

The module is put back as the VCO last gave it, from the state's snapshot;
the other modules keep their edits, and the next ``plan()`` judges them.
"""

import pytest
from profile_state import profile_state
from pyvelo.client.exceptions import (
    ConfigConflictError,
    DetachedObjectError,
    LossyFieldError,
    SegmentSetError,
)
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.state import ConfigState
from pyvelo.testing import MockVcoClient
from segment_helpers import GLOBAL, ds_entry, firewall_entry, qos_entry
from test_state_plan import make_state  # pyright: ignore[reportImplicitRelativeImport]

PIPELINE = ConfigPipeline(client=None)
LOGGING = {"firewall_logging_enabled": True}
COS = {"realtime": {"high": {"value": 40}}}


async def test_revert_puts_the_module_back_as_read() -> None:
    state = make_state()
    held = state.edge.firewall.segment("Global Segment")
    _ = await PIPELINE.apply(
        state, module="firewall", patch={"syslog_forwarding": True}
    )
    assert [m.name for m in state.plan().changed] == ["firewall"]

    returned = await PIPELINE.revert(state, module="firewall")

    assert returned is state
    assert state.plan().changed == ()
    assert state.edge.firewall.syslog_forwarding.stored is False
    assert held.is_detached
    with pytest.raises(DetachedObjectError, match="a write or a revert"):
        held.firewall_logging_enabled = True


async def test_revert_releases_a_state_that_no_plan_would_take() -> None:
    state = make_state()
    state.edge.firewall.inbound = []
    with pytest.raises(LossyFieldError):
        _ = state.plan()

    _ = await PIPELINE.revert(state, module="firewall")

    assert state.plan().changed == ()


async def test_revert_removes_a_module_this_state_created() -> None:
    state = make_state(edge_modules=[])
    _ = await PIPELINE.apply(state, module="firewall", patch=LOGGING)
    assert [m.operation for m in state.plan().changed] == ["insert"]

    _ = await PIPELINE.revert(state, module="firewall")

    assert state.find_module("firewall") is None
    assert state.plan().changed == ()


async def test_revert_of_a_module_this_state_created_detaches_what_was_taken() -> None:
    state = make_state(edge_modules=[])
    _ = await PIPELINE.apply(state, module="firewall", patch=LOGGING)
    held = state.edge.firewall

    _ = await PIPELINE.revert(state, module="firewall")

    assert held.is_detached
    with pytest.raises(DetachedObjectError, match="a write or a revert"):
        held.syslog_forwarding.set_local(False)
    assert state.plan().changed == ()


async def test_revert_of_a_module_whose_insert_may_be_stored_is_refused() -> None:
    state = make_state(edge_modules=[])
    _ = await PIPELINE.apply(state, module="firewall", patch=LOGGING)
    state.unconfirmed_inserts.add("firewall")

    with pytest.raises(ValueError, match="may have reached the VCO"):
        _ = await PIPELINE.revert(state, module="firewall")

    assert state.find_module("firewall") is not None
    assert "firewall" in state.unconfirmed_inserts


async def test_revert_of_one_module_of_an_assigned_segment_leaves_the_others() -> None:
    state = await profile_state(
        [ds_entry(GLOBAL)], [qos_entry(GLOBAL)], [firewall_entry(GLOBAL)]
    )
    _ = await PIPELINE.assign_segment(state, "Guest")

    _ = await PIPELINE.revert(state, module="qos")

    with pytest.raises(SegmentSetError) as error:
        _ = state.plan()
    assert (error.value.segments, error.value.modules) == (("Guest",), ("QOS",))
    _ = await PIPELINE.revert(state, module="deviceSettings")
    _ = await PIPELINE.revert(state, module="firewall")
    assert state.plan().changed == ()


async def test_revert_of_a_module_the_target_lacks_changes_nothing() -> None:
    state = make_state()  # the edge has no QoS module

    _ = await PIPELINE.revert(state, module="qos")

    assert state.find_module("QOS") is None
    assert state.plan().changed == ()


async def test_revert_takes_the_lock_as_apply_does() -> None:
    state = make_state()
    async with state.lock:
        with pytest.raises(RuntimeError, match="already holds the lock"):
            _ = await PIPELINE.revert(state, module="firewall")


async def test_revert_of_an_unknown_module_is_refused() -> None:
    state = make_state()

    with pytest.raises(ValueError, match="Unknown module"):
        _ = await PIPELINE.revert(state, module="no_such_module")


async def test_revert_without_a_snapshot_is_refused_and_keeps_the_edit() -> None:
    built = make_state()
    state = ConfigState(
        edge_config=built.edge_config,
        profile_config=built.profile_config,
        target_type="edge",
        resolver_context=built.resolver_context,
    )
    _ = await PIPELINE.apply(
        state, module="firewall", patch={"syslog_forwarding": True}
    )

    with pytest.raises(ValueError, match="take_snapshot"):
        _ = await PIPELINE.revert(state, module="firewall")

    assert state.edge.firewall.syslog_forwarding.stored is True
    assert [m.name for m in state.plan().changed] == ["firewall"]


async def test_revert_of_one_of_two_edited_modules_keeps_the_other() -> None:
    state = await profile_state(
        [ds_entry(GLOBAL)], [qos_entry(GLOBAL)], [firewall_entry(GLOBAL)]
    )
    segment = {"segments": {"Global Segment": LOGGING}}
    _ = await PIPELINE.apply(state, module="firewall", patch=segment)
    _ = await PIPELINE.apply(
        state,
        module="qos",
        patch={"segments": {"Global Segment": {"cos_mapping": COS}}},
    )
    assert [m.name for m in state.plan().changed] == ["QOS", "firewall"]

    _ = await PIPELINE.revert(state, module="qos")

    assert [m.name for m in state.plan().changed] == ["firewall"]


async def test_revert_after_a_save_puts_the_module_back_as_saved() -> None:
    state = make_state()
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state, module="firewall", patch={"syslog_forwarding": True}
    )
    _ = await pipeline.save(state)
    saved = state.module("firewall").version
    assert saved != "e1"
    _ = await pipeline.apply(
        state, module="firewall", patch={"syslog_forwarding": False}
    )

    _ = await pipeline.revert(state, module="firewall")

    assert state.module("firewall").version == saved
    assert state.edge.firewall.syslog_forwarding.stored is True
    assert state.plan().changed == ()
    client.store.requests.clear()
    result = await pipeline.save(state)
    assert (result.written, client.store.requests) == ((), [])


async def test_revert_after_a_conflict_gives_the_module_as_stored() -> None:
    state = make_state()
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)
    client.store.other_writer(1, lambda row: row["data"].update(futureKey=2))
    _ = await pipeline.apply(
        state, module="firewall", patch={"syslog_forwarding": True}
    )
    with pytest.raises(ConfigConflictError) as failed:
        _ = await pipeline.save(state)
    stored = failed.value.module
    assert stored is not None
    assert state.module("firewall") is stored
    _ = await pipeline.apply(state, module="firewall", patch=failed.value.redo_patch())

    _ = await pipeline.revert(state, module="firewall")

    assert state.module("firewall").version == stored.version != "e1"
    assert state.plan().changed == ()


async def test_revert_keeps_an_edge_module_bound_to_its_profile() -> None:
    expected = make_state().edge.firewall.segment("Guest")
    state = make_state()
    _ = await PIPELINE.apply(
        state, module="firewall", patch={"syslog_forwarding": True}
    )

    _ = await PIPELINE.revert(state, module="firewall")

    guest = state.edge.firewall.segment("Guest")  # copied from the profile
    assert guest.segment.name == "Guest"
    assert guest.model_dump() == expected.model_dump()
