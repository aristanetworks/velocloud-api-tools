"""One state used from two tasks: ``save`` and ``apply`` run one after the other.

``Gated`` is a client whose module writes wait, as a request on the network
does, until the test lets them go.
"""

import asyncio
from typing import Self, override

import pytest
from pydantic import MISSING
from pyvelo.client.exceptions import DetachedObjectError
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    InsertConfigurationModule,
    InsertConfigurationModuleResult,
    UpdateConfigurationModule,
)
from pyvelo.core import ConfigContext
from pyvelo.core.rebase import Key, Path
from pyvelo_intent import add
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.config.state import ConfigState
from pyvelo_intent.testing import MockVcoClient
from test_state_plan import (  # pyright: ignore[reportImplicitRelativeImport]
    make_state,
)
from test_write_back import (  # pyright: ignore[reportImplicitRelativeImport]
    FIREWALL,
    QOS,
    QOS_ACTION,
    changed,
    data_of,
    edit_all,
    methods,
    three_module_state,
)


class Gated(MockVcoClient):
    """Each module write sets ``started``, yields to the loop, then waits for ``release``."""

    started: asyncio.Event
    release: asyncio.Event

    @classmethod
    def over(cls, state: ConfigState) -> Self:
        client = cls.for_state(state)
        client.started, client.release = asyncio.Event(), asyncio.Event()
        return client

    async def hold(self) -> None:
        self.started.set()
        await asyncio.sleep(0)  # a request always yields to the loop
        _ = await self.release.wait()

    @override
    async def update_configuration_module(
        self, request: UpdateConfigurationModule, *, level: ConfigContext | None = None
    ) -> AnyConfigurationModule | None:
        await self.hold()
        return await super().update_configuration_module(request, level=level)

    @override
    async def insert_configuration_module(
        self, request: InsertConfigurationModule, *, level: ConfigContext | None = None
    ) -> InsertConfigurationModuleResult | AnyConfigurationModule:
        await self.hold()
        return await super().insert_configuration_module(request, level=level)


async def test_apply_during_a_save_waits_and_its_edit_stays_planned():
    state = make_state()
    client = Gated.over(state)
    pipeline = ConfigPipeline(client)
    state.edge.firewall.firewall_enabled = False
    saving = asyncio.create_task(pipeline.save(state))
    _ = await client.started.wait()

    applying = asyncio.create_task(
        pipeline.apply(state, module="firewall", patch={"syslog_forwarding": True})
    )
    await asyncio.sleep(0)
    assert not applying.done()
    assert state.edge.firewall.syslog_forwarding is False
    client.release.set()
    result = await saving
    _ = await applying

    assert result.modules_updated == [FIREWALL]
    assert state.edge.firewall.syslog_forwarding is True
    (plan,) = state.plan().changed
    assert [str(c.path) for c in plan.changes] == ["syslog_forwarding"]
    _ = await pipeline.save(state)
    stored = data_of(client.store, 1)
    assert (stored["firewall_enabled"], stored["syslog_forwarding"]) == (False, True)


async def test_two_saves_at_once_write_each_module_once():
    state = three_module_state()
    client = Gated.over(state)
    client.release.set()  # a write only yields to the loop
    pipeline = ConfigPipeline(client)
    state.edge.firewall.firewall_enabled = False

    first, second = await asyncio.gather(pipeline.save(state), pipeline.save(state))

    assert methods(client.store) == ["updateConfigurationModule"]
    assert (first.modules_updated, second.modules_updated) == ([FIREWALL], [])
    assert state.plan().changed == ()


async def test_two_saves_at_once_insert_a_new_module_once():
    state = make_state()  # the edge has no QoS module
    client = Gated.over(state)
    client.release.set()
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state,
        module="qos",
        segment="Global Segment",
        path="rules",
        patch={"voice": add({"match": {"dport_low": 5060}, "action": QOS_ACTION})},
    )

    first, second = await asyncio.gather(pipeline.save(state), pipeline.save(state))

    assert methods(client.store) == ["insertConfigurationModule", "getConfiguration"]
    assert (first.modules_inserted, second.modules_inserted) == ([QOS], [])
    rows = [r for r in client.store.rows.values() if r["configurationId"] == 10]
    assert sorted(r["name"] for r in rows) == ["QOS", "firewall"]


async def test_a_cancelled_save_releases_the_state():
    state = make_state()
    client = Gated.over(state)
    pipeline = ConfigPipeline(client)
    state.edge.firewall.firewall_enabled = False
    saving = asyncio.create_task(pipeline.save(state))
    _ = await client.started.wait()

    _ = saving.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await saving

    assert not state.lock.locked()
    client.release.set()
    _ = await pipeline.apply(
        state, module="firewall", patch={"syslog_forwarding": True}
    )
    assert changed(state) == ["firewall"]


async def test_an_assignment_during_a_save_is_reported_by_the_save():
    state = make_state()
    client = Gated.over(state)
    pipeline = ConfigPipeline(client)
    state.edge.firewall.firewall_enabled = False
    saving = asyncio.create_task(pipeline.save(state))
    _ = await client.started.wait()

    state.edge.firewall.syslog_forwarding = True  # no lock, no apply
    client.release.set()
    with pytest.raises(DetachedObjectError) as exc:
        _ = await saving

    assert exc.value.paths == [Path(("data", "syslog_forwarding"))]
    assert "firewall: the module was edited while this save was writing it" in str(
        exc.value
    )
    stored = data_of(client.store, 1)
    assert (stored["firewall_enabled"], stored["syslog_forwarding"]) == (False, False)
    assert state.edge.firewall.syslog_forwarding is False
    assert state.module("firewall").version == client.store.rows[1]["version"]
    assert state.plan().changed == ()


async def test_a_lookup_during_a_save_is_reported_as_no_edit():
    state = make_state()  # the profile has a Guest segment the edge only inherits
    client = Gated.over(state)
    pipeline = ConfigPipeline(client)
    state.edge.firewall.firewall_enabled = False
    saving = asyncio.create_task(pipeline.save(state))
    _ = await client.started.wait()

    _ = state.edge.firewall.segment("Guest")  # adds a placeholder; edits nothing
    client.release.set()
    with pytest.raises(DetachedObjectError) as exc:
        _ = await saving

    assert exc.value.paths == [Path(("data", "segments", Key("Guest")))]
    assert "at: data.segments[Guest]" in str(exc.value)
    assert (
        "A path that is a placeholder a lookup added (segment(name), for a "
        "segment the edge only inherits) is no edit and needs no redo"
    ) in str(exc.value)
    assert data_of(client.store, 1)["firewall_enabled"] is False
    assert state.module("firewall").version == client.store.rows[1]["version"]
    assert state.plan().changed == ()


async def test_an_edit_of_a_later_module_during_a_save_is_reported_too():
    state = three_module_state()
    client = Gated.over(state)
    pipeline = ConfigPipeline(client)
    state.edge.firewall.firewall_enabled = False
    links = state.edge.wan.links
    assert links is not MISSING
    links[0].description = "first"
    saving = asyncio.create_task(pipeline.save(state))
    _ = await client.started.wait()  # the firewall write is out; WAN is planned

    links[0].description = "second"
    client.release.set()
    with pytest.raises(DetachedObjectError) as exc:
        _ = await saving

    link = Key(("00000000-0000-0000-0000-000000000001", "IPv4"))
    assert exc.value.paths == [Path(("data", "links", link, "description"))]
    assert methods(client.store) == ["updateConfigurationModule"] * 2
    assert data_of(client.store, 5)["links"][0]["description"] == "first"
    assert state.plan().changed == ()


async def test_the_error_names_the_modules_written_and_those_left():
    state = three_module_state()
    client = Gated.over(state)
    pipeline = ConfigPipeline(client)
    edit_all(state)
    saving = asyncio.create_task(pipeline.save(state))
    _ = await client.started.wait()  # the firewall write is out; QoS is planned

    rate = state.edge.qos.service_rate_limit
    assert rate is not MISSING
    rate.value = 60
    client.release.set()
    with pytest.raises(DetachedObjectError) as exc:
        _ = await saving

    (note,) = exc.value.__notes__
    assert "firewall (update), QOS (update)" in note
    assert "not written: WAN" in note
    assert methods(client.store) == [
        "updateConfigurationModule",
        "getConfigurationModules",  # QoS has a segment: its version is read first
        "updateConfigurationModule",
    ]
    assert changed(state) == ["WAN"]


async def test_an_assignment_under_the_lock_waits_for_the_save():
    state = make_state()
    client = Gated.over(state)
    pipeline = ConfigPipeline(client)
    state.edge.firewall.firewall_enabled = False
    saving = asyncio.create_task(pipeline.save(state))
    _ = await client.started.wait()

    async def edit() -> None:
        async with state.lock:
            state.edge.firewall.syslog_forwarding = True

    editing = asyncio.create_task(edit())
    await asyncio.sleep(0)
    client.release.set()
    _ = await saving
    await editing

    assert changed(state) == ["firewall"]
    assert state.edge.firewall.syslog_forwarding is True


async def test_apply_and_save_by_the_task_that_holds_the_lock_raise():
    state = make_state()
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)
    held = "this task already holds the lock of this state"

    async with state.lock:
        state.edge.firewall.firewall_enabled = False
        with pytest.raises(RuntimeError, match=held):
            _ = await pipeline.apply(
                state, module="firewall", patch={"syslog_forwarding": True}
            )
        with pytest.raises(RuntimeError, match=held):
            _ = await pipeline.save(state)
        with pytest.raises(RuntimeError, match=held):
            async with state.lock:
                pass
        assert state.lock.locked()  # a refusal does not release it

    assert not state.lock.locked()
    assert client.store.requests == []
    assert state.edge.firewall.syslog_forwarding is False
    result = await pipeline.save(state)
    assert result.modules_updated == [FIREWALL]


async def test_another_task_still_waits_for_the_lock():
    state = make_state()
    pipeline = ConfigPipeline(MockVcoClient.for_state(state))

    async with state.lock:
        applying = asyncio.create_task(
            pipeline.apply(state, module="firewall", patch={"syslog_forwarding": True})
        )
        await asyncio.sleep(0)
        assert not applying.done()
    _ = await applying

    assert state.edge.firewall.syslog_forwarding is True
