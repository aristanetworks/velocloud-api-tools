"""What a caller can tell from a save: its result, and how far it got when it raised.

``three_module_state`` has edge firewall, QoS and WAN modules, which a save
writes in that order; ``edit_all`` makes one edit in each.
"""

import asyncio

import pytest
from pyvelo.client.exceptions import (
    ConfigConflictError,
    DetachedObjectError,
    VcoOperationError,
)
from pyvelo.config import SaveProgress, SaveValidationError, save_progress
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.validation import (
    ConfigStateLike,
    ValidationError,
    ValidationResult,
    ValidatorRegistry,
    validator,
)
from pyvelo.testing import MockVcoClient
from test_concurrent_use import (  # pyright: ignore[reportImplicitRelativeImport]
    Gated,
)
from test_state_plan import (  # pyright: ignore[reportImplicitRelativeImport]
    make_state,
)
from test_write_back import (  # pyright: ignore[reportImplicitRelativeImport]
    FIREWALL,
    changed,
    edit_all,
    three_module_state,
)


def failing_registry() -> ValidatorRegistry:
    """A registry whose one write-time validator always reports an error."""
    registry = ValidatorRegistry()

    @validator(modules=[], phases=["write"], registry=registry)
    def always_fail(_state: ConfigStateLike) -> ValidationResult:
        return ValidationResult(
            errors=[ValidationError(path="firewall", message="blocked", code="test")]
        )

    assert always_fail.phases == ["write"]
    return registry


async def test_a_save_blocked_by_a_validator_raises_and_sends_nothing() -> None:
    state = make_state()
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client, validator_registry=failing_registry())
    state.edge.firewall.firewall_enabled.set_local(False)

    with pytest.raises(SaveValidationError) as exc:
        _ = await pipeline.save(state)

    assert [(e.path, e.message, e.code) for e in exc.value.errors] == [
        ("firewall", "blocked", "test")
    ]
    assert client.store.requests == []
    assert changed(state) == ["firewall"]
    assert save_progress(exc.value) is None  # nothing was sent


async def test_a_dry_run_returns_the_validator_errors_with_what_it_would_write() -> (
    None
):
    state = make_state()
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client, validator_registry=failing_registry())
    state.edge.firewall.firewall_enabled.set_local(False)

    result = await pipeline.save(state, dry_run=True)

    assert (result.dry_run, result.is_valid) == (True, False)
    assert [e.code for e in result.validation_errors] == ["test"]
    assert result.modules_updated == [FIREWALL]
    assert result.written == ()
    with pytest.raises(SaveValidationError):
        result.raise_for_errors()


async def test_the_result_says_whether_it_was_a_dry_run_and_what_was_written() -> None:
    state = three_module_state()
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)
    state.edge.firewall.firewall_enabled.set_local(False)

    nothing = await ConfigPipeline(client).save(make_state())
    preview = await pipeline.save(state, dry_run=True)
    result = await pipeline.save(state)

    assert (nothing.dry_run, nothing.written) == (False, ())
    assert (preview.dry_run, preview.written) == (True, ())
    assert preview.modules_updated == [FIREWALL]
    assert result.dry_run is False
    assert [(w.name, w.operation) for w in result.written] == [("firewall", "update")]
    assert result.written[0].module is state.module("firewall")
    assert result.modules_updated == [FIREWALL]


async def test_a_request_error_carries_what_was_written_and_what_was_not() -> None:
    state = three_module_state()
    client = MockVcoClient.for_state(state)
    client.store.fail_writes = {2}
    edit_all(state)

    with pytest.raises(VcoOperationError) as exc:
        _ = await ConfigPipeline(client).save(state)

    progress = save_progress(exc.value)
    assert isinstance(progress, SaveProgress)
    assert [(w.name, w.operation) for w in progress.completed] == [
        ("firewall", "update")
    ]
    assert (progress.failed, progress.pending) == ("QOS", ("WAN",))
    assert progress.completed[0].module is state.module("firewall")
    assert not hasattr(exc.value, "__notes__")
    assert changed(state) == ["QOS", "WAN"]


async def test_a_write_error_carries_the_same_and_keeps_completed() -> None:
    state = three_module_state()
    client = MockVcoClient.for_state(state)
    client.store.other_writer(4, lambda row: row["data"].update(futureKey=1))
    edit_all(state)

    with pytest.raises(ConfigConflictError) as exc:
        _ = await ConfigPipeline(client).save(state)

    progress = save_progress(exc.value)
    assert progress is not None
    assert progress.completed == exc.value.completed
    assert [w.name for w in progress.completed] == ["firewall"]
    assert (progress.failed, progress.pending) == ("QOS", ("WAN",))


async def test_a_cancelled_save_carries_them_too() -> None:
    state = three_module_state()
    client = Gated.over(state)
    edit_all(state)
    saving = asyncio.create_task(ConfigPipeline(client).save(state))
    _ = await client.started.wait()  # the firewall write is out

    _ = saving.cancel()
    with pytest.raises(asyncio.CancelledError) as exc:
        _ = await saving

    progress = save_progress(exc.value)
    assert progress is not None
    assert (progress.completed, progress.failed, progress.pending) == (
        (),
        "firewall",
        ("QOS", "WAN"),
    )


async def test_a_timeout_around_a_save_leads_to_the_same_progress() -> None:
    state = three_module_state()
    client = Gated.over(state)
    edit_all(state)

    with pytest.raises(TimeoutError) as exc:
        async with asyncio.timeout(0.01):
            _ = await ConfigPipeline(client).save(state)

    progress = save_progress(exc.value)  # found on the cancellation behind it
    assert progress is not None
    assert (progress.failed, progress.pending) == ("firewall", ("QOS", "WAN"))


async def test_an_edit_during_the_save_counts_its_module_as_written() -> None:
    state = three_module_state()
    client = Gated.over(state)
    edit_all(state)
    saving = asyncio.create_task(ConfigPipeline(client).save(state))
    _ = await client.started.wait()
    state.edge.firewall.syslog_forwarding.set_local(True)
    client.release.set()

    with pytest.raises(DetachedObjectError) as exc:
        _ = await saving

    progress = save_progress(exc.value)
    assert progress is not None
    assert [w.name for w in progress.completed] == ["firewall"]
    assert (progress.failed, progress.pending) == (None, ("QOS", "WAN"))


def test_an_error_that_is_not_from_a_save_has_no_progress() -> None:
    assert save_progress(ValueError("not from a save")) is None
