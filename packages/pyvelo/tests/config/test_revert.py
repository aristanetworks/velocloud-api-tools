"""``ConfigPipeline.revert`` takes back the edits to one module of a state.

The module is put back as the VCO last gave it, from the state's snapshot;
the other modules keep their edits, and the next ``plan()`` judges them.
"""

import copy
from collections.abc import Callable
from typing import Any, override

import httpx
import pytest
from profile_state import profile_state
from pyvelo.client.exceptions import (
    ConfigConflictError,
    DetachedObjectError,
    LossyFieldError,
    SegmentSetError,
    VcoOperationError,
)
from pyvelo.client.operations.configuration import check_found_insert
from pyvelo.client.schemas.configuration import DeleteConfigurationModule
from pyvelo.config import add, delete
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.state import ConfigState
from pyvelo.core import RowsModifiedConfirmation
from pyvelo.testing import MockVcoClient
from segment_helpers import GLOBAL, ds_entry, firewall_entry, qos_entry
from test_state_plan import make_state  # pyright: ignore[reportImplicitRelativeImport]
from test_unconfirmed_insert import (  # pyright: ignore[reportImplicitRelativeImport] (a test module beside this one, on the test path)
    edge_qos_rows,
    with_a_new_qos_rule,
)
from test_write_back import (  # pyright: ignore[reportImplicitRelativeImport] (a test module beside this one, on the test path)
    methods,
)

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


async def test_revert_without_a_client_of_a_module_whose_insert_may_be_stored_is_refused() -> (  # noqa: E501 (a test's name)
    None
):
    state = make_state(edge_modules=[])
    _ = await PIPELINE.apply(state, module="firewall", patch=LOGGING)
    state.unconfirmed_inserts.add("firewall")

    with pytest.raises(ValueError, match="may have reached the VCO") as refused:
        _ = await PIPELINE.revert(state, module="firewall")

    assert "client" in str(refused.value)
    assert "read the configuration and delete" in str(refused.value)
    assert state.find_module("firewall") is not None
    assert "firewall" in state.unconfirmed_inserts


async def test_revert_module_of_a_module_whose_insert_may_be_stored_names_the_pipeline() -> (  # noqa: E501 (a test's name)
    None
):
    state = make_state(edge_modules=[])
    _ = await PIPELINE.apply(state, module="firewall", patch=LOGGING)
    state.unconfirmed_inserts.add("firewall")

    with pytest.raises(ValueError, match=r"pipeline\.revert\(state, module="):
        state.revert_module("firewall")

    assert state.find_module("firewall") is not None
    assert "firewall" in state.unconfirmed_inserts


async def lost_insert(client: MockVcoClient, state: ConfigState) -> ConfigPipeline:
    """A pipeline over *client*, after a save whose QoS insert was stored unanswered."""
    client.store.lose_answers = {1}
    pipeline = await with_a_new_qos_rule(client, state)
    with pytest.raises(httpx.ReadTimeout):
        _ = await pipeline.save(state)
    assert state.unconfirmed_inserts == {"QOS"}
    client.store.requests.clear()
    return pipeline


async def test_revert_deletes_a_new_module_whose_insert_was_stored() -> None:
    state = make_state()
    client = MockVcoClient.for_state(state)
    pipeline = await lost_insert(client, state)
    (stored_id,) = edge_qos_rows(client)

    _ = await pipeline.revert(state, module="qos")

    assert methods(client.store) == ["getConfiguration", "deleteConfigurationModule"]
    assert client.store.requests[-1][1]["id"] == stored_id
    assert edge_qos_rows(client) == []
    assert state.find_module("QOS") is None
    assert state.unconfirmed_inserts == set()
    assert state.plan().changed == ()
    client.store.requests.clear()
    result = await pipeline.save(state)
    assert result.modules_inserted == []
    assert "insertConfigurationModule" not in methods(client.store)
    assert edge_qos_rows(client) == []


async def test_revert_of_a_new_module_whose_insert_was_not_stored_deletes_nothing() -> (
    None
):
    state = make_state()
    client = MockVcoClient.for_state(state)
    client.store.fail_writes = {1}
    pipeline = await with_a_new_qos_rule(client, state)
    with pytest.raises(VcoOperationError):
        _ = await pipeline.save(state)
    assert state.unconfirmed_inserts == {"QOS"}
    client.store.requests.clear()

    _ = await pipeline.revert(state, module="qos")

    assert methods(client.store) == ["getConfiguration"]
    assert state.find_module("QOS") is None
    assert state.unconfirmed_inserts == set()
    assert state.plan().changed == ()


def drop_the_rules(row: dict[str, Any]) -> None:
    """Another writer's QoS module of the same name: every segment without rules."""
    for segment in row["data"]["segments"]:
        segment["rules"] = []


async def test_revert_does_not_delete_a_module_of_the_name_not_this_states() -> None:
    state = make_state()
    client = MockVcoClient.for_state(state)
    pipeline = await lost_insert(client, state)
    (stored_id,) = edge_qos_rows(client)
    client.store.other_writer(stored_id, drop_the_rules)
    held = copy.deepcopy(client.store.rows)
    module = state.module("qos")

    with pytest.raises(ValueError, match="was not deleted") as refused:
        _ = await pipeline.revert(state, module="qos")

    assert "load" in str(refused.value)
    assert "save again" in str(refused.value)
    assert methods(client.store) == ["getConfiguration"]
    assert client.store.rows == held
    assert state.module("qos") is module
    assert module.is_new
    assert state.unconfirmed_inserts == {"QOS"}


async def test_a_revert_whose_delete_fails_changes_nothing_and_can_be_redone() -> None:
    state = make_state()
    client = MockVcoClient.for_state(state)
    pipeline = await lost_insert(client, state)
    (stored_id,) = edge_qos_rows(client)
    client.store.fail_calls = {"configuration/deleteConfigurationModule"}
    module = state.module("qos")

    with pytest.raises(VcoOperationError, match="simulated failure"):
        _ = await pipeline.revert(state, module="qos")

    assert methods(client.store) == ["getConfiguration", "deleteConfigurationModule"]
    assert client.store.requests[-1][1]["id"] == stored_id  # the delete was sent
    assert edge_qos_rows(client) == [stored_id]
    assert state.module("qos") is module
    assert state.unconfirmed_inserts == {"QOS"}

    _ = await pipeline.revert(state, module="qos")

    assert edge_qos_rows(client) == []
    assert state.find_module("QOS") is None
    assert state.unconfirmed_inserts == set()


class AnswersTheDelete(MockVcoClient):
    """A VCO that answers a module delete with ``answer``, and deletes nothing."""

    answer: RowsModifiedConfirmation = RowsModifiedConfirmation()

    @override
    async def delete_configuration_module(
        self, request: DeleteConfigurationModule
    ) -> RowsModifiedConfirmation:
        self.calls.append(("delete_configuration_module", request))
        return self.answer


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (RowsModifiedConfirmation(error="refused here"), "refused here"),
        (RowsModifiedConfirmation(rows=0), "no row was deleted"),
    ],
    ids=["an error", "a count of 0"],
)
async def test_a_revert_whose_delete_is_answered_as_failed_changes_nothing(
    answer: RowsModifiedConfirmation, message: str
) -> None:
    state = make_state()
    client = AnswersTheDelete.for_state(state)
    client.answer = answer
    pipeline = await lost_insert(client, state)
    (stored_id,) = edge_qos_rows(client)
    module = state.module("qos")

    with pytest.raises(VcoOperationError, match=message):
        _ = await pipeline.revert(state, module="qos")

    assert [name for name, _ in client.calls][-1] == "delete_configuration_module"
    assert edge_qos_rows(client) == [stored_id]
    assert state.module("qos") is module
    assert state.unconfirmed_inserts == {"QOS"}


async def test_a_revert_whose_delete_is_answered_with_no_count_is_taken_as_done() -> (
    None
):
    state = make_state()
    client = AnswersTheDelete.for_state(state)
    client.answer = RowsModifiedConfirmation()
    pipeline = await lost_insert(client, state)

    _ = await pipeline.revert(state, module="qos")

    assert [name for name, _ in client.calls][-1] == "delete_configuration_module"
    assert state.find_module("QOS") is None
    assert state.unconfirmed_inserts == set()


def failed_insert(client: MockVcoClient) -> dict[str, Any]:
    """The params of the insert the store refused, the first request it was sent."""
    method, params = client.store.requests[0]
    assert method == "configuration/insertConfigurationModule"
    return copy.deepcopy(params)


def without_the_voice_rule() -> dict[str, Any]:
    return {"voice": delete()}


async def test_revert_does_not_delete_another_writers_module_when_this_plans_nothing() -> (  # noqa: E501 (a test's name)
    None
):
    state = make_state()
    client = MockVcoClient.for_state(state)
    client.store.fail_writes = {1}
    pipeline = await with_a_new_qos_rule(client, state)
    with pytest.raises(VcoOperationError):
        _ = await pipeline.save(state)
    other = failed_insert(client)
    for segment in other["data"]["segments"]:
        segment["rules"] = []
    _ = client.store.handle("configuration/insertConfigurationModule", other)
    (other_id,) = edge_qos_rows(client)
    _ = await pipeline.apply(
        state,
        module="qos",
        segment="Global Segment",
        path="rules",
        patch=without_the_voice_rule(),
    )
    module = state.module("qos")
    assert state.plan_module(module).operation == "none"
    held = copy.deepcopy(client.store.rows)
    client.store.requests.clear()

    with pytest.raises(
        ValueError, match="holds nothing a save would insert"
    ) as refused:
        _ = await pipeline.revert(state, module="qos")

    text = str(refused.value)
    assert f"module {other_id}" in text
    assert "was not deleted" in text
    assert "load the state again" in text
    assert "save again" not in text
    assert "apply" not in text
    assert methods(client.store) == ["getConfiguration"]
    assert client.store.rows == held
    assert state.module("qos") is module
    assert state.unconfirmed_inserts == {"QOS"}


async def test_revert_does_not_delete_a_stored_insert_another_writer_changed_when_this_plans_nothing() -> (  # noqa: E501 (a test's name)
    None
):
    state = make_state()
    client = MockVcoClient.for_state(state)
    pipeline = await lost_insert(client, state)
    (stored_id,) = edge_qos_rows(client)
    client.store.other_writer(stored_id, drop_the_rules)
    _ = await pipeline.apply(
        state,
        module="qos",
        segment="Global Segment",
        path="rules",
        patch=without_the_voice_rule(),
    )
    module = state.module("qos")
    held = copy.deepcopy(client.store.rows)
    client.store.requests.clear()

    with pytest.raises(
        ValueError, match="holds nothing a save would insert"
    ) as refused:
        _ = await pipeline.revert(state, module="qos")

    assert "save again" not in str(refused.value)
    assert methods(client.store) == ["getConfiguration"]
    assert client.store.rows == held
    assert state.module("qos") is module
    assert state.unconfirmed_inserts == {"QOS"}


async def test_check_found_insert_refuses_a_plan_that_inserts_nothing() -> None:
    state = make_state(edge_modules=[])
    module = state.module("firewall", create=True)
    plan = state.plan_module(module)
    assert plan.operation == "none"

    with pytest.raises(ValueError, match="not an insert"):
        _ = check_found_insert(module, plan, module)


async def test_revert_of_a_new_module_that_plans_nothing_and_was_not_stored() -> None:
    state = make_state()
    client = MockVcoClient.for_state(state)
    client.store.fail_writes = {1}
    pipeline = await with_a_new_qos_rule(client, state)
    with pytest.raises(VcoOperationError):
        _ = await pipeline.save(state)
    _ = await pipeline.apply(
        state,
        module="qos",
        segment="Global Segment",
        path="rules",
        patch=without_the_voice_rule(),
    )
    client.store.requests.clear()

    _ = await pipeline.revert(state, module="qos")

    assert methods(client.store) == ["getConfiguration"]
    assert state.find_module("QOS") is None
    assert state.unconfirmed_inserts == set()


@pytest.mark.parametrize(
    ("more", "named"),
    [
        (
            {
                "inbound": [
                    {"name": "ssh", "match": {}, "action": {"type": "port_forwarding"}}
                ]
            },
            "inbound",
        ),
        ({"syslog_forwarding": True, "firewall_enabled": True}, "syslog_forwarding"),
    ],
    ids=["an inbound rule", "two more flags"],
)
async def test_revert_does_not_delete_a_module_that_holds_more_than_this_one(
    more: dict[str, Any], named: str
) -> None:
    state = make_state(edge_modules=[])
    client = MockVcoClient.for_state(state)
    client.store.fail_writes = {1}
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(state, module="firewall", patch=LOGGING)
    with pytest.raises(VcoOperationError):
        _ = await pipeline.save(state)
    other = failed_insert(client)
    other["data"].update(copy.deepcopy(more))
    _ = client.store.handle("configuration/insertConfigurationModule", other)
    module = state.module("firewall")
    held = copy.deepcopy(client.store.rows)
    client.store.requests.clear()

    with pytest.raises(ValueError, match="was not deleted") as refused:
        _ = await pipeline.revert(state, module="firewall")

    assert named in str(refused.value)
    assert methods(client.store) == ["getConfiguration"]
    assert client.store.rows == held
    assert state.module("firewall") is module
    assert state.unconfirmed_inserts == {"firewall"}


WEB_RULE: dict[str, Any] = {"match": {}, "action": {"allow_or_deny": "allow"}}


async def stored_firewall_insert(
    client: MockVcoClient, state: ConfigState
) -> tuple[ConfigPipeline, int]:
    """A pipeline after a save whose firewall insert (one outbound rule) was stored.

    Its answer was lost; the store gave the rule a ``ruleLogicalId`` the state did
    not send.
    """
    client.store.issue_rule_ids = True
    client.store.lose_answers = {1}
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state,
        module="firewall",
        segment="Global Segment",
        path="outbound",
        patch={"web": add(copy.deepcopy(WEB_RULE))},
    )
    with pytest.raises(httpx.ReadTimeout):
        _ = await pipeline.save(state)
    (stored_id,) = [
        i
        for i, row in client.store.rows.items()
        if row["name"] == "firewall" and row["configurationId"] == 10
    ]
    (segment,) = [
        s
        for s in client.store.rows[stored_id]["data"]["segments"]
        if s["segment"]["name"] == "Global Segment"
    ]
    assert segment["outbound"][0]["ruleLogicalId"]
    client.store.requests.clear()
    return pipeline, stored_id


async def test_revert_deletes_a_stored_insert_whose_rule_got_its_id_from_the_vco() -> (
    None
):
    state = make_state(edge_modules=[])
    client = MockVcoClient.for_state(state)
    pipeline, stored_id = await stored_firewall_insert(client, state)

    _ = await pipeline.revert(state, module="firewall")

    assert methods(client.store) == ["getConfiguration", "deleteConfigurationModule"]
    assert stored_id not in client.store.rows
    assert state.find_module("firewall") is None
    assert state.unconfirmed_inserts == set()


def another_rule(row: dict[str, Any]) -> None:
    for segment in row["data"]["segments"]:
        if segment["segment"]["name"] == "Global Segment":
            segment["outbound"].append(
                {"name": "theirs", **WEB_RULE, "ruleLogicalId": "rule-id-theirs"}
            )


def another_segment(row: dict[str, Any]) -> None:
    """A whole segment only the stored module has, with a rule of its own."""
    segments: list[dict[str, Any]] = row["data"]["segments"]
    extra = copy.deepcopy(segments[0])
    extra["segment"] = {
        "segmentId": 7,
        "name": "Lab Segment",
        "type": "REGULAR",
        "segmentLogicalId": "00000000-0000-0000-0000-000000000007",
    }
    extra["outbound"] = [{"name": "lab", **WEB_RULE, "ruleLogicalId": "rule-id-lab"}]
    segments.append(extra)


def another_value(row: dict[str, Any]) -> None:
    row["data"]["syslog_forwarding"] = True


@pytest.mark.parametrize(
    ("edit", "named"),
    [
        (another_rule, 'segments["Global Segment"]'),
        (another_value, "syslog_forwarding"),
        (another_segment, "Lab Segment"),
    ],
    ids=[
        "another rule",
        "a value at a key the VCO does not own",
        "a whole segment only the stored module has",
    ],
)
async def test_revert_does_not_delete_a_stored_insert_with_more_than_the_vcos_own_ids(
    edit: Callable[[dict[str, Any]], None], named: str
) -> None:
    state = make_state(edge_modules=[])
    client = MockVcoClient.for_state(state)
    pipeline, stored_id = await stored_firewall_insert(client, state)
    client.store.other_writer(stored_id, edit)
    held = copy.deepcopy(client.store.rows)
    module = state.module("firewall")

    with pytest.raises(ValueError, match="was not deleted") as refused:
        _ = await pipeline.revert(state, module="firewall")

    assert named in str(refused.value)
    assert "ruleLogicalId" not in str(refused.value)
    assert methods(client.store) == ["getConfiguration"]
    assert client.store.rows == held
    assert state.module("firewall") is module
    assert state.unconfirmed_inserts == {"firewall"}


async def test_revert_of_a_new_module_that_cannot_be_planned_is_refused() -> None:
    state = make_state(edge_modules=[])
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(state, module="firewall", patch=LOGGING)
    state.edge.firewall.inbound = []
    state.unconfirmed_inserts.add("firewall")
    module = state.module("firewall")
    client.store.requests.clear()

    with pytest.raises(ValueError, match="cannot be planned") as refused:
        _ = await pipeline.revert(state, module="firewall")

    assert isinstance(refused.value.__cause__, LossyFieldError)
    assert "load the state again" in str(refused.value)
    assert client.store.requests == []
    assert state.module("firewall") is module
    assert state.unconfirmed_inserts == {"firewall"}


async def test_revert_of_other_modules_sends_nothing() -> None:
    state = make_state()
    client = MockVcoClient.for_state(state)
    pipeline = await with_a_new_qos_rule(client, state)
    _ = await pipeline.apply(
        state, module="firewall", patch={"syslog_forwarding": True}
    )
    client.store.requests.clear()

    _ = await pipeline.revert(state, module="firewall")
    _ = await pipeline.revert(state, module="qos")

    assert client.store.requests == []
    assert state.find_module("QOS") is None
    assert state.plan().changed == ()


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
