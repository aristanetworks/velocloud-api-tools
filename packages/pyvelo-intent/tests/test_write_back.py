"""``ConfigPipeline.save`` continues the state from the modules the VCO returns.

Each test saves through a client over a ``ModuleStore`` seeded from the state
(``MockVcoClient.for_state``), so the client's own request building, parsing
and error mapping run. The edge configuration is 10 and its profile 20.
"""

import copy
import uuid
from collections.abc import Iterable, Iterator
from typing import Any, cast

import pytest
from pydantic import MISSING, ValidationError
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import (
    ConfigConflictError,
    DetachedObjectError,
    VcoOperationError,
    WriteNotAppliedError,
    WriteOutcomeUnknownError,
)
from pyvelo.client.models.configuration.device_settings.segment import SegmentBgp
from pyvelo.client.models.configuration.firewall.rules import FirewallOutboundRule
from pyvelo.client.models.configuration.firewall.segment import FirewallSegment
from pyvelo.client.models.configuration.qos.root import ServiceRateLimit
from pyvelo.client.schemas.configuration import GenericConfigurationModule
from pyvelo.core import VcoModel
from pyvelo.core.rebase import Key, Path, key_text
from pyvelo.core.response_check import PlannedChange, ServerChange
from pyvelo.testing import ModuleStore, make_module_json
from pyvelo_intent import add
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.config.state import ConfigState, save_progress
from pyvelo_intent.testing import MockVcoClient
from test_state_plan import (  # pyright: ignore[reportImplicitRelativeImport]
    EDGE_FW,
    desc,
    make_state,
)

FIREWALL = ConfigurationModuleName.FIREWALL
QOS = ConfigurationModuleName.QOS
U1 = uuid.UUID(int=1)
LINK: dict[str, Any] = {
    "internalId": str(U1),
    "name": "Internet",
    "type": "WIRED",
    "addressingVersion": "IPv4",
}
QOS_ACTION: dict[str, Any] = {
    "edge2EdgeRouteAction": {},
    "edge2DataCenterRouteAction": {},
    "edge2CloudRouteAction": {},
    "QoS": {},
}
LINK_DESCRIPTION = f"links[{key_text((str(U1), 'IPv4'))}].description"


def three_module_state() -> ConfigState:
    """An edge state with firewall (1), QoS (4) and WAN (5) rows of configuration 10."""
    qos: dict[str, Any] = {
        "segments": [{"segment": desc(0, "Global Segment"), "rules": []}]
    }
    return make_state(
        edge_modules=[
            make_module_json(
                "firewall",
                copy.deepcopy(EDGE_FW),
                module_id=1,
                configuration_id=10,
                version="e1",
            ),
            make_module_json(
                "QOS", qos, module_id=4, configuration_id=10, version="e1"
            ),
            make_module_json(
                "WAN",
                {"links": [dict(LINK)]},
                module_id=5,
                configuration_id=10,
                version="e1",
            ),
        ]
    )


def edit_all(state: ConfigState) -> None:
    """One edit in each module of ``three_module_state``, each shown by ``plan()``."""
    state.edge.firewall.firewall_enabled = False
    state.edge.qos.service_rate_limit = ServiceRateLimit(
        enabled=True, input_type="percent", value=50
    )
    links = state.edge.wan.links
    assert links is not MISSING
    links[0].description = "edited"


def methods(store: ModuleStore) -> list[str]:
    """The method of each request the store was sent, without its ``configuration/``."""
    return [method.rsplit("/", 1)[-1] for method, _ in store.requests]


def paths(xs: Iterable[PlannedChange | ServerChange]) -> list[str]:
    """The path of each entry, sorted: the order within one tuple is not asserted."""
    return sorted(str(x.path) for x in xs)


def changed(state: ConfigState) -> list[str]:
    return [m.name for m in state.plan().changed]


def data_of(store: ModuleStore, module_id: int) -> dict[str, Any]:
    return store.rows[module_id]["data"]


def other_writer_edit(row: dict[str, Any]) -> None:
    """Another writer's change to a key the caller does not edit."""
    row["data"]["syslog_forwarding"] = True


def rule(name: str) -> FirewallOutboundRule:
    return FirewallOutboundRule.model_validate(
        {"name": name, "match": {}, "action": {"allow_or_deny": "deny"}}
    )


def models_in(value: object) -> Iterator[VcoModel]:
    """Every model reachable from *value*: through fields, extra values, list items and dict values."""
    if isinstance(value, VcoModel):
        yield value
        for name in type(value).model_fields:
            yield from models_in(cast(object, getattr(value, name)))
        yield from models_in(list((value.model_extra or {}).values()))
    elif isinstance(value, list | tuple):
        for item in cast(Iterable[object], value):
            yield from models_in(item)
    elif isinstance(value, dict):
        for item in cast(dict[object, object], value).values():
            yield from models_in(item)


def assert_detached_and_replaced(
    state: ConfigState, seg: FirewallSegment, old_data: object
) -> None:
    """*seg* and *old_data*, held from before a save, are detached; the firewall module now in the state shares no model with them."""
    with pytest.raises(DetachedObjectError, match="FirewallSegment.firewall_logging"):
        seg.firewall_logging_enabled = True
    old = list(models_in(old_data))
    assert any(model is seg for model in old)
    assert all(model.is_detached for model in old)
    module = state.module("firewall")
    assert module.data is not old_data
    assert not any(model.is_detached for model in models_in(module))
    state.edge.firewall.segments[0].firewall_logging_enabled = True
    assert state.edge.firewall.segments[0].firewall_logging_enabled is True


async def test_three_modules_three_updates_and_a_version_read_for_the_one_with_segments():
    state = three_module_state()
    client = MockVcoClient.for_state(state)
    edit_all(state)

    result = await ConfigPipeline(client).save(state)

    # The QoS module has a segment, so its update sends refs and reads the
    # module's version first; nothing reads a configuration.
    assert methods(client.store) == [
        "updateConfigurationModule",
        "getConfigurationModules",
        "updateConfigurationModule",
        "updateConfigurationModule",
    ]
    assert result.modules_updated == [FIREWALL, QOS, ConfigurationModuleName.WAN]


async def test_plan_after_a_save_is_empty():
    state = make_state()
    client = MockVcoClient.for_state(state)
    state.edge.firewall.firewall_enabled = False

    _ = await ConfigPipeline(client).save(state)

    assert state.plan().changed == ()
    assert state.get_field_changes() == []


async def test_second_edit_and_save_carries_the_returned_version():
    state = make_state()
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)
    state.edge.firewall.firewall_enabled = False
    _ = await pipeline.save(state)
    answered = client.store.rows[1]["version"]

    state.edge.firewall.syslog_forwarding = True
    _ = await pipeline.save(state)

    first, second = client.update_requests
    assert first.version == "e1"
    assert second.version == answered
    assert second.version != "e1"
    assert data_of(client.store, 1)["syslog_forwarding"] is True


async def test_apply_works_after_a_save():
    state = make_state()
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state, module="firewall", patch={"firewall_enabled": False}
    )
    _ = await pipeline.save(state)

    _ = await pipeline.apply(
        state, module="firewall", patch={"syslog_forwarding": True}
    )
    _ = await pipeline.save(state)

    stored = data_of(client.store, 1)
    assert (stored["firewall_enabled"], stored["syslog_forwarding"]) == (False, True)
    assert len(client.update_requests) == 2


async def test_added_keys_are_reported_and_not_planned_again():
    state = make_state()
    client = MockVcoClient.for_state(state)
    client.store.on_store.append(lambda new, _: new["data"].update(added=1))
    state.edge.firewall.firewall_enabled = False

    result = await ConfigPipeline(client).save(state)

    assert paths(result.server_changes[FIREWALL]) == ["added"]
    assert state.plan().changed == ()
    raw = cast(dict[str, object], state.module("firewall").raw_data)
    assert raw["added"] == 1


async def test_left_out_changes_are_in_the_save_result():
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
    client = MockVcoClient.for_state(state)

    def keep_the_id(new: dict[str, Any], _: dict[str, Any] | None) -> None:
        new["data"]["segments"][0]["outbound"][0]["ruleLogicalId"] = "l1"

    client.store.on_store.append(keep_the_id)
    state.edge.firewall.segments[0].outbound[0].rule_logical_id = "l2"

    result = await ConfigPipeline(client).save(state)

    at = 'segments["Global Segment"].outbound[web].ruleLogicalId'
    (change,) = result.left_out[FIREWALL]
    assert str(change.path) == at
    assert (change.as_read, change.intended, change.stored) == ("l1", "l2", "l1")
    assert at not in paths(result.server_changes[FIREWALL])
    assert state.edge.firewall.segments[0].outbound[0].rule_logical_id == "l1"
    assert state.plan().changed == ()


async def test_a_module_a_save_cannot_write_raises_before_any_request():
    state = make_state(
        edge_modules=[
            make_module_json(
                "firewall",
                copy.deepcopy(EDGE_FW),
                module_id=1,
                configuration_id=10,
                version="e1",
            ),
            make_module_json(
                "analyticsSettings",
                {"enabled": False},
                module_id=6,
                configuration_id=10,
                version="e1",
            ),
        ]
    )
    client = MockVcoClient.for_state(state)
    state.edge.firewall.firewall_enabled = False
    analytics = state.module("analyticsSettings")
    assert isinstance(analytics, GenericConfigurationModule)
    analytics.data["enabled"] = True
    assert changed(state) == ["firewall", "analyticsSettings"]

    with pytest.raises(TypeError, match="GenericConfigurationModule"):
        _ = await ConfigPipeline(client).save(state)

    assert client.store.requests == []
    assert changed(state) == ["firewall", "analyticsSettings"]


async def test_request_two_of_three_fails():
    state = three_module_state()
    client = MockVcoClient.for_state(state)
    client.store.fail_writes = {2}
    pipeline = ConfigPipeline(client)
    edit_all(state)

    with pytest.raises(VcoOperationError) as exc:
        _ = await pipeline.save(state)

    assert exc.value.code == -32603
    progress = save_progress(exc.value)
    assert progress is not None
    assert [(w.name, w.operation) for w in progress.completed] == [
        ("firewall", "update")
    ]
    assert (progress.failed, progress.pending) == ("QOS", ("WAN",))
    assert state.module("firewall").version == client.store.rows[1]["version"]
    assert {m.name: m.operation for m in state.plan().modules}["firewall"] == "none"
    assert changed(state) == ["QOS", "WAN"]

    _ = await pipeline.save(state)

    # Each attempt at QoS, which has a segment, reads the module's version first.
    assert methods(client.store) == [
        "updateConfigurationModule",
        "getConfigurationModules",
        "updateConfigurationModule",
        "getConfigurationModules",
        "updateConfigurationModule",
        "updateConfigurationModule",
    ]
    assert [r.configuration_module_id for r in client.update_requests[2:]] == [4, 5]
    assert state.plan().changed == ()


@pytest.mark.parametrize("conditional_check", [False, True])
async def test_conflict_refreshes_the_state_and_carries_the_plan(
    conditional_check: bool,
):
    state = make_state()
    client = MockVcoClient.for_state(state, conditional_check=conditional_check)
    client.store.other_writer(1, other_writer_edit)
    state.edge.firewall.firewall_enabled = False

    with pytest.raises(ConfigConflictError) as exc:
        _ = await ConfigPipeline(client).save(state)

    error = exc.value
    assert error.plan is not None and error.plan.name == "firewall"
    assert state.module("firewall").version == client.store.rows[1]["version"]
    assert state.plan().changed == ()
    text = str(error)
    assert "  firewall_enabled: read (nothing), meant false, stored (nothing)" in text
    assert "  syslog_forwarding: read false, stored true" in text


@pytest.mark.parametrize("conditional_check", [False, True])
async def test_redo_after_a_conflict_saves_without_a_reload(conditional_check: bool):
    state = make_state()
    client = MockVcoClient.for_state(state, conditional_check=conditional_check)
    client.store.other_writer(1, other_writer_edit)
    pipeline = ConfigPipeline(client)
    state.edge.firewall.firewall_enabled = False
    with pytest.raises(ConfigConflictError):
        _ = await pipeline.save(state)

    state.edge.firewall.firewall_enabled = False
    _ = await pipeline.save(state)

    stored = data_of(client.store, 1)
    assert (stored["syslog_forwarding"], stored["firewall_enabled"]) == (True, False)
    read = ["getConfiguration"] if conditional_check else []
    assert methods(client.store) == [
        "updateConfigurationModule",
        *read,
        "updateConfigurationModule",
    ]


async def test_a_conflict_found_before_sending_puts_the_module_as_read_in_the_state():
    state = three_module_state()
    client = MockVcoClient.for_state(state)
    client.store.other_writer(4, lambda row: row["data"].update(futureKey=1))
    state.edge.qos.service_rate_limit = ServiceRateLimit(
        enabled=True, input_type="percent", value=50
    )

    with pytest.raises(ConfigConflictError) as exc:
        _ = await ConfigPipeline(client).save(state)

    error = exc.value
    assert (error.stated, error.sent) == (False, False)
    assert methods(client.store) == ["getConfigurationModules", "getConfiguration"]
    assert error.module is not None
    assert state.module("QOS") is error.module
    assert error.module.version == client.store.rows[4]["version"]
    assert [str(c.path) for c in error.lost_edits] == ["serviceRateLimit"]
    assert state.plan().changed == ()


async def test_a_module_gone_from_its_configuration_keeps_the_state_as_it_was():
    state = three_module_state()
    client = MockVcoClient.for_state(state)
    qos = state.module("QOS")
    del client.store.rows[4]
    state.edge.qos.service_rate_limit = ServiceRateLimit(
        enabled=True, input_type="percent", value=50
    )

    with pytest.raises(ConfigConflictError) as exc:
        _ = await ConfigPipeline(client).save(state)

    error = exc.value
    assert (error.stated, error.sent) == (False, False)
    assert error.module is None
    assert "updateConfigurationModule" not in methods(client.store)
    assert state.module("QOS") is qos
    assert qos.version == "e1"
    assert [m.name for m in state.plan().changed] == ["QOS"]


async def test_failure_at_module_two_reports_module_one():
    state = three_module_state()
    client = MockVcoClient.for_state(state)
    client.store.other_writer(4, lambda row: row["data"].update(futureKey=1))
    edit_all(state)

    with pytest.raises(ConfigConflictError) as exc:
        _ = await ConfigPipeline(client).save(state)

    assert [w.name for w in exc.value.completed] == ["firewall"]
    assert "written before this one: firewall (update)" in str(exc.value)
    (wan,) = state.plan().changed
    assert wan.name == "WAN"
    assert [str(c.path) for c in wan.changes] == [LINK_DESCRIPTION]


async def test_not_applied_refreshes_the_state_too():
    state = make_state()
    client = MockVcoClient.for_state(state)

    def put_back(new: dict[str, Any], old: dict[str, Any] | None) -> None:
        assert old is not None
        new["data"]["syslog_forwarding"] = old["data"]["syslog_forwarding"]

    client.store.on_store.append(put_back)
    state.edge.firewall.firewall_enabled = False
    state.edge.firewall.syslog_forwarding = True

    with pytest.raises(WriteNotAppliedError) as exc:
        _ = await ConfigPipeline(client).save(state)

    assert paths(exc.value.lost_edits) == ["syslog_forwarding"]
    assert state.module("firewall") is exc.value.module
    assert state.module("firewall").raw_data == data_of(client.store, 1)
    assert state.plan().changed == ()


async def test_objects_are_detached_after_a_save():
    state = make_state()
    client = MockVcoClient.for_state(state)
    seg = state.edge.firewall.segments[0]
    old_data = state.module("firewall").data
    state.edge.firewall.firewall_enabled = False

    _ = await ConfigPipeline(client).save(state)

    assert_detached_and_replaced(state, seg, old_data)


@pytest.mark.parametrize("conditional_check", [False, True])
async def test_objects_are_detached_after_a_save_that_raised(conditional_check: bool):
    # The module as stored is the row returned, or read after the refusal.
    state = make_state()
    client = MockVcoClient.for_state(state, conditional_check=conditional_check)
    client.store.other_writer(1, other_writer_edit)
    seg = state.edge.firewall.segments[0]
    old_data = state.module("firewall").data
    state.edge.firewall.firewall_enabled = False

    with pytest.raises(ConfigConflictError):
        _ = await ConfigPipeline(client).save(state)

    assert_detached_and_replaced(state, seg, old_data)


async def test_objects_of_an_unsaved_module_are_not_detached():
    state = three_module_state()
    client = MockVcoClient.for_state(state)
    links = state.edge.wan.links
    assert links is not MISSING
    link = links[0]
    state.edge.firewall.firewall_enabled = False

    _ = await ConfigPipeline(client).save(state)

    assert methods(client.store) == ["updateConfigurationModule"]
    link.description = "edited"
    assert link.is_detached is False
    (wan,) = state.plan().changed
    assert wan.name == "WAN"
    assert [str(c.path) for c in wan.changes] == [LINK_DESCRIPTION]


async def test_in_place_edit_on_a_detached_object_is_reported_by_plan():
    held = Path(("data", "segments", Key("Global Segment"), "outbound", Key("new")))

    async def saved() -> tuple[ConfigState, MockVcoClient, FirewallSegment]:
        """A state saved once with one edit, and a segment taken from it before, changed in place since."""
        state = make_state()
        client = MockVcoClient.for_state(state)
        seg = state.edge.firewall.segments[0]
        state.edge.firewall.firewall_enabled = False
        _ = await ConfigPipeline(client).save(state)
        seg.outbound.insert(0, rule("new"))
        return state, client, seg

    # The segment is held: only an object still alive is compared.
    state, _, seg = await saved()
    with pytest.raises(DetachedObjectError) as exc:
        _ = state.plan()
    assert exc.value.paths == [held]
    assert seg.is_detached

    state, client, seg = await saved()
    with pytest.raises(DetachedObjectError) as exc:
        _ = await ConfigPipeline(client).save(state)
    assert exc.value.paths == [held]
    assert methods(client.store) == ["updateConfigurationModule"]
    assert seg.is_detached


async def saved_insert() -> tuple[ConfigState, MockVcoClient, ConfigPipeline, int]:
    """A state whose edge had no QoS module, saved: the module is inserted; its ID."""
    state = make_state()  # the edge has no QoS module
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state,
        module="qos",
        segment="Global Segment",
        path="rules",
        patch={"voice": add({"match": {"dport_low": 5060}, "action": QOS_ACTION})},
    )
    result = await pipeline.save(state)
    assert methods(client.store) == ["insertConfigurationModule", "getConfiguration"]
    params = client.store.requests[0][1]
    assert params["returnData"] is True
    assert params["configurationId"] == state.target_config.id_ == 10
    assert state.module("qos").is_new is False
    assert result.modules_inserted == [QOS]
    (inserted,) = [
        i
        for i, row in client.store.rows.items()
        if row["name"] == "QOS" and row["configurationId"] == 10
    ]
    assert state.module("qos").id_ == inserted
    return state, client, pipeline, inserted


async def test_insert_refreshes_and_is_no_longer_new():
    _ = await saved_insert()


async def test_a_module_inserted_is_updated_by_the_next_save():
    state, client, pipeline, inserted = await saved_insert()
    state.edge.qos.service_rate_limit = ServiceRateLimit(
        enabled=True, input_type="percent", value=50
    )
    version = client.store.rows[inserted]["version"]
    second = await pipeline.save(state)

    (update,) = client.update_requests
    assert (update.configuration_module_id, update.version) == (inserted, version)
    assert second.modules_updated == [QOS]
    assert second.modules_inserted == []


async def test_dry_run_sends_nothing_and_detaches_nothing():
    state = make_state()
    client = MockVcoClient.for_state(state)
    seg = state.edge.firewall.segments[0]
    state.edge.firewall.firewall_enabled = False

    result = await ConfigPipeline(client).save(state, dry_run=True)

    assert result.modules_updated == [FIREWALL]
    assert client.store.requests == []
    seg.firewall_logging_enabled = True
    assert seg.is_detached is False
    assert state.module("firewall").is_detached is False
    assert changed(state) == ["firewall"]


async def test_a_save_whose_answer_does_not_parse_keeps_the_edits_in_the_state():
    def without_an_action(new: dict[str, Any], _old: dict[str, Any] | None) -> None:
        new["data"]["segments"][0]["outbound"] = [{"name": "a", "match": {}}]

    state = make_state()
    client = MockVcoClient.for_state(state)
    client.store.on_store.append(without_an_action)
    state.edge.firewall.firewall_enabled = False

    with pytest.raises(WriteOutcomeUnknownError) as exc:
        _ = await ConfigPipeline(client).save(state)

    error = exc.value
    assert error.answer == "row"
    assert error.module is None
    assert error.completed == ()
    assert isinstance(error.__cause__, ValidationError)
    assert client.store.rows[1]["data"]["firewall_enabled"] is False
    # The state is as it was: the module as read, at its version, with the edit.
    assert state.module("firewall").version == "e1"
    assert [m.name for m in state.plan().changed] == ["firewall"]


async def test_the_repr_of_a_save_result_holds_no_module_data():
    settings: dict[str, Any] = {
        "lan": {"networks": [], "interfaces": []},
        "segments": [
            {
                "segment": desc(0, "Global Segment"),
                "bgp": {
                    "enabled": True,
                    "override": True,
                    "ASN": "65000",
                    "neighbors": [
                        {
                            "neighborIp": "192.0.2.1",
                            "neighborAS": "65001",
                            "md5Password": "OLD-md5-made-up",
                        }
                    ],
                },
            }
        ],
    }
    state = make_state(
        edge_modules=[
            make_module_json(
                "firewall",
                {"firewall_enabled": True, "segments": []},
                module_id=1,
                configuration_id=10,
            ),
            make_module_json(
                "deviceSettings", settings, module_id=4, configuration_id=10
            ),
        ]
    )
    state.edge.firewall.firewall_enabled = False
    bgp = state.edge.device_settings.segments[0].bgp
    assert isinstance(bgp, SegmentBgp)
    bgp.asn = "65010"
    client = MockVcoClient.for_state(state)
    preview = await ConfigPipeline(client).save(state, dry_run=True)
    for name in ("firewall", "deviceSettings"):
        assert f"ModulePlan(name='{name}', operation='update', changes=1)" in repr(
            preview
        )
    result = await ConfigPipeline(client).save(state)
    assert client.store.rows[4]["data"]["segments"][0]["bgp"]["ASN"] == "65010"
    for text in (repr(preview), repr(result)):
        assert "OLD-md5-made-up" not in text and "neighborIp" not in text
        assert text.startswith("SaveResult(") and "modules_updated=[" in text
    assert "server_changes={'firewall': 0, 'deviceSettings': 0}" in repr(result)
    assert "left_out={'firewall': 0, 'deviceSettings': 0}" in repr(result)
    assert "ModuleWrite(name='deviceSettings', operation='update'" in repr(result)
