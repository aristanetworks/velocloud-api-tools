"""A write whose answer never arrived: the next save finds out what the VCO holds.

The state is ``test_state_plan.make_state()``, whose edge (configuration 10)
has a firewall module and no QoS module, so a QoS edit is saved as an insert.
"""

import asyncio
import copy
from typing import Any, override

import httpx
import pytest
from pyvelo.client.exceptions import (
    ConfigConflictError,
    VcoOperationError,
    WriteNotAppliedError,
    WriteOutcomeUnknownError,
)
from pyvelo.client.models.configuration.qos.root import ServiceRateLimit
from pyvelo.client.schemas.configuration import (
    AnyStoredModule,
    InsertConfigurationModule,
    InsertConfigurationModuleResult,
    UpdateConfigurationModule,
)
from pyvelo.config import add, save_progress
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.state import ConfigState
from pyvelo.core import ConfigContext
from pyvelo.testing import MockVcoClient, make_module_json
from test_concurrent_use import Gated  # pyright: ignore[reportImplicitRelativeImport]
from test_state_plan import (  # pyright: ignore[reportImplicitRelativeImport]
    EDGE_FW,
    make_state,
)
from test_write_back import (  # pyright: ignore[reportImplicitRelativeImport]
    FIREWALL,
    QOS,
    QOS_ACTION,
    methods,
    paths,
)


async def with_a_new_qos_rule(
    client: MockVcoClient, state: ConfigState
) -> ConfigPipeline:
    """A pipeline over *client*, after a patch that makes *state* plan a QoS insert."""
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state,
        module="qos",
        segment="Global Segment",
        path="rules",
        patch={"voice": add({"match": {"dport_low": 5060}, "action": QOS_ACTION})},
    )
    return pipeline


def edge_qos_rows(client: MockVcoClient) -> list[int]:
    """The IDs of the QoS rows the store holds for the edge configuration."""
    return [
        i
        for i, row in client.store.rows.items()
        if row["name"] == "QOS" and row["configurationId"] == 10
    ]


def rate_limit() -> ServiceRateLimit:
    return ServiceRateLimit(enabled=True, input_type="percent", value=50)


async def test_an_insert_whose_answer_was_lost_is_not_sent_again() -> None:
    state = make_state()
    client = MockVcoClient.for_state(state)
    client.store.lose_answers = {1}
    pipeline = await with_a_new_qos_rule(client, state)

    with pytest.raises(httpx.ReadTimeout):
        _ = await pipeline.save(state)

    assert state.module("qos").is_new
    assert state.unconfirmed_inserts == {"QOS"}
    (stored_id,) = edge_qos_rows(client)

    result = await pipeline.save(state)

    assert methods(client.store) == ["insertConfigurationModule", "getConfiguration"]
    assert edge_qos_rows(client) == [stored_id]
    assert result.modules_inserted == [QOS]
    module = state.module("qos")
    assert (module.is_new, module.id_) == (False, stored_id)
    assert module.version == client.store.rows[stored_id]["version"]
    assert state.unconfirmed_inserts == set()
    assert state.plan().changed == ()


async def test_an_insert_that_was_not_stored_is_sent_again_after_a_read() -> None:
    state = make_state()
    client = MockVcoClient.for_state(state)
    client.store.fail_writes = {1}
    pipeline = await with_a_new_qos_rule(client, state)

    with pytest.raises(VcoOperationError):
        _ = await pipeline.save(state)

    assert edge_qos_rows(client) == []
    assert state.unconfirmed_inserts == {"QOS"}

    _ = await pipeline.save(state)

    assert methods(client.store) == [
        "insertConfigurationModule",
        "getConfiguration",
        "insertConfigurationModule",
        "getConfiguration",
    ]
    (stored_id,) = edge_qos_rows(client)
    assert state.module("qos").id_ == stored_id
    assert state.unconfirmed_inserts == set()


async def test_an_insert_the_vco_counted_that_cannot_be_read_back_is_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = make_state()
    client = MockVcoClient.for_state(state)
    client.store.return_data = False  # the insert is answered with an ID only
    pipeline = await with_a_new_qos_rule(client, state)
    serve = client.store.handle

    def without_qos(method: str, params: dict[str, Any]) -> object:
        """Answer as the store does.

        But a configuration read leaves its QoS module out.
        """
        answer: Any = serve(method, params)
        if method == "configuration/getConfiguration":
            answer["modules"] = [m for m in answer["modules"] if m["name"] != "QOS"]
        return answer

    with monkeypatch.context() as patched:
        patched.setattr(client.store, "handle", without_qos)
        with pytest.raises(WriteOutcomeUnknownError) as exc:
            _ = await pipeline.save(state)

    (stored_id,) = edge_qos_rows(client)
    assert (exc.value.answer, exc.value.module_id) == ("counted", stored_id)
    assert exc.value.module is None
    assert state.module("qos").is_new
    assert state.unconfirmed_inserts == {"QOS"}

    _ = await pipeline.save(state)

    assert methods(client.store) == [
        "insertConfigurationModule",
        "getConfiguration",
        "getConfiguration",
    ]
    assert edge_qos_rows(client) == [stored_id]
    assert state.module("qos").id_ == stored_id
    assert state.unconfirmed_inserts == set()


async def test_edits_made_after_the_lost_answer_are_lost_edits_of_the_next_save() -> (
    None
):
    state = make_state()
    client = MockVcoClient.for_state(state)
    client.store.lose_answers = {1}
    pipeline = await with_a_new_qos_rule(client, state)
    with pytest.raises(httpx.ReadTimeout):
        _ = await pipeline.save(state)
    state.edge.qos.service_rate_limit.set_local(rate_limit())

    with pytest.raises(WriteNotAppliedError) as exc:
        _ = await pipeline.save(state)

    assert paths(exc.value.lost_edits) == ["serviceRateLimit"]
    assert methods(client.store) == ["insertConfigurationModule", "getConfiguration"]
    (stored_id,) = edge_qos_rows(client)
    module = state.module("qos")
    assert (module.is_new, module.id_) == (False, stored_id)
    assert state.unconfirmed_inserts == set()
    assert state.plan().changed == ()

    state.edge.qos.service_rate_limit.set_local(rate_limit())
    again = await pipeline.save(state)
    assert again.modules_updated == [QOS]
    assert edge_qos_rows(client) == [stored_id]


async def test_a_dry_run_after_a_lost_answer_reads_nothing_and_warns() -> None:
    state = make_state()
    client = MockVcoClient.for_state(state)
    client.store.lose_answers = {1}
    pipeline = await with_a_new_qos_rule(client, state)
    with pytest.raises(httpx.ReadTimeout):
        _ = await pipeline.save(state)

    result = await pipeline.save(state, dry_run=True)

    assert methods(client.store) == ["insertConfigurationModule"]
    assert result.modules_inserted == [QOS]
    assert [(w.path, w.code) for w in result.warnings] == [
        ("QOS", "insert_unconfirmed")
    ]
    assert state.unconfirmed_inserts == {"QOS"}


@pytest.mark.parametrize("conditional_check", [False, True])
async def test_an_update_whose_answer_was_lost_is_found_stored_by_the_next_save(
    *,
    conditional_check: bool,
) -> None:
    state = make_state()
    client = MockVcoClient.for_state(state, conditional_check=conditional_check)
    client.store.lose_answers = {1}
    pipeline = ConfigPipeline(client)
    state.edge.firewall.firewall_enabled.set_local(False)

    with pytest.raises(httpx.ReadTimeout):
        _ = await pipeline.save(state)

    stored_version = client.store.rows[1]["version"]
    assert state.module("firewall").version == "e1" != stored_version
    assert state.unconfirmed_inserts == set()

    result = await pipeline.save(state)

    read = ["getConfiguration"] if conditional_check else []
    assert methods(client.store) == ["updateConfigurationModule"] * 2 + read
    assert client.store.rows[1]["version"] == stored_version  # not written twice
    assert result.modules_updated == [FIREWALL]
    assert state.module("firewall").version == stored_version
    assert state.plan().changed == ()


@pytest.mark.parametrize("conditional_check", [False, True])
async def test_an_update_retried_with_a_change_the_vco_stores_its_own_way(
    *,
    conditional_check: bool,
) -> None:
    web = {"name": "web", "action": {"allow_or_deny": "allow"}, "match": {}}
    raw = copy.deepcopy(EDGE_FW)
    raw["segments"][0]["outbound"] = [{**web, "ruleLogicalId": "l1"}]
    state = make_state(
        edge_modules=[
            make_module_json(
                "firewall", raw, module_id=1, configuration_id=10, version="e1"
            )
        ]
    )
    client = MockVcoClient.for_state(state, conditional_check=conditional_check)

    def keep_the_id(new: dict[str, Any], _: dict[str, Any] | None) -> None:
        new["data"]["segments"][0]["outbound"][0]["ruleLogicalId"] = "l1"

    client.store.on_store.append(keep_the_id)
    client.store.lose_answers = {1}
    pipeline = ConfigPipeline(client)
    state.edge.firewall.firewall_enabled.set_local(False)
    state.edge.firewall.segments[0].outbound[0].rule_logical_id = "l2"
    owned = 'segments["Global Segment"].outbound[web].ruleLogicalId'

    with pytest.raises(httpx.ReadTimeout):
        _ = await pipeline.save(state)
    stored_version = client.store.rows[1]["version"]

    if conditional_check:
        # The VCO says it refused the retry, so the change it did not take is lost.
        with pytest.raises(ConfigConflictError) as exc:
            _ = await pipeline.save(state)
        assert exc.value.stated is True
        assert paths(exc.value.applied) == ["firewall_enabled", "segments[Guest]"]
        assert paths(exc.value.lost_edits) == [owned]
    else:
        # The VCO answers with the module and says nothing: the save passes.
        result = await pipeline.save(state)
        assert paths(result.left_out[FIREWALL]) == [owned]

    assert client.store.rows[1]["version"] == stored_version  # not written twice
    assert state.module("firewall").version == stored_version
    assert state.edge.firewall.segments[0].outbound[0].rule_logical_id == "l1"
    assert state.plan().changed == ()


async def test_a_save_cancelled_while_an_insert_is_out_marks_it_unconfirmed() -> None:
    state = make_state()
    client = Gated.over(state)
    pipeline = await with_a_new_qos_rule(client, state)
    saving = asyncio.create_task(pipeline.save(state))
    _ = await client.started.wait()  # the insert is out

    _ = saving.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await saving

    assert state.unconfirmed_inserts == {"QOS"}
    assert state.module("qos").is_new
    assert not state.lock.locked()

    client.release.set()
    result = await pipeline.save(state)

    assert methods(client.store) == [
        "getConfiguration",
        "insertConfigurationModule",
        "getConfiguration",
    ]
    assert result.modules_inserted == [QOS]
    (stored_id,) = edge_qos_rows(client)
    assert state.module("qos").id_ == stored_id
    assert state.unconfirmed_inserts == set()


class RemovesTheModuleDuringTheWrite(MockVcoClient):
    """After the VCO stores a write, the module leaves the state it was planned from."""

    state: ConfigState

    @override
    async def insert_configuration_module(
        self, request: InsertConfigurationModule, *, level: ConfigContext | None = None
    ) -> InsertConfigurationModuleResult | AnyStoredModule:
        stored = await super().insert_configuration_module(request, level=level)
        self.state.drop_module(self.state.module("qos"))
        return stored

    @override
    async def update_configuration_module(
        self, request: UpdateConfigurationModule, *, level: ConfigContext | None = None
    ) -> AnyStoredModule | None:
        stored = await super().update_configuration_module(request, level=level)
        assert self.state.edge_config is not None
        self.state.edge_config.modules = []
        return stored


async def test_a_stored_insert_the_state_could_not_take_back_is_written_and_unconfirmed() -> (  # noqa: E501 (a test's name)
    None
):
    state = make_state()
    client = RemovesTheModuleDuringTheWrite.for_state(state)
    client.state = state
    pipeline = await with_a_new_qos_rule(client, state)

    with pytest.raises(ValueError, match="not in the target config") as exc:
        _ = await pipeline.save(state)

    (stored_id,) = edge_qos_rows(client)
    progress = save_progress(exc.value)
    assert progress is not None
    assert [(w.name, w.operation) for w in progress.completed] == [("QOS", "insert")]
    assert (progress.failed, progress.pending) == (None, ())
    assert state.unconfirmed_inserts == {"QOS"}
    assert edge_qos_rows(client) == [stored_id]


async def test_a_stored_update_the_state_could_not_take_back_is_written() -> None:
    state = make_state()
    client = RemovesTheModuleDuringTheWrite.for_state(state)
    client.state = state
    state.edge.firewall.firewall_enabled.set_local(False)

    with pytest.raises(ValueError, match="not in the target config") as exc:
        _ = await ConfigPipeline(client).save(state)

    progress = save_progress(exc.value)
    assert progress is not None
    assert [(w.name, w.operation) for w in progress.completed] == [
        ("firewall", "update")
    ]
    assert (progress.failed, progress.pending) == (None, ())
