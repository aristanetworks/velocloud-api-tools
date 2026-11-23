"""Offline checks of the support code of the live save tests (tests/support/live_support.py).

The live tests are deselected without ``-m integration`` and need an Orchestrator;
what they rely on to restore a lab and to keep its names out of their output is
checked here with no lab.
"""

import contextlib
import copy
import json
import pathlib
import re
from collections.abc import Callable
from typing import Any, ClassVar

import httpx
import live_support as live
import pytest
from pydantic import MISSING, BaseModel, ValidationError
from pyvelo.client import (
    ConfigConflictError,
    ModuleWriteError,
    VcoClient,
    VcoOperationError,
    WriteNotAppliedError,
    WriteOutcomeUnknownError,
)
from pyvelo.client.enums.configuration import ConfigurationModuleName as Name
from pyvelo.client.exceptions import WriteAnswer
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import FirewallConfigurationModule
from pyvelo.core.rebase import Key, Path, RawChange
from pyvelo.core.response_check import PlannedChange, ServerChange
from pyvelo.testing import (
    ModuleStore,
    make_jsonrpc_response,
    make_module_json,
    make_transport,
)

DS = Name.DEVICE_SETTINGS


def segment(segment_id: int, **extra: object) -> dict[str, Any]:
    return {"segment": {"segmentId": segment_id, "name": f"s{segment_id}"}, **extra}


class TestSettle:
    """``routeMapsVersion`` is accepted drift only where the write added it."""

    def test_a_key_the_write_added_to_an_entry_is_dropped(self):
        before = {"segments": [segment(0)]}
        after = {"segments": [segment(0, routeMapsVersion="1")]}
        assert live.settle(DS, before, after) == before
        assert live.changes_between(DS, before, after) == []

    def test_a_key_the_entry_already_had_is_compared(self):
        before = {"segments": [segment(0, routeMapsVersion="1")]}
        after = {"segments": [segment(0, routeMapsVersion="2")]}
        assert live.settle(DS, before, after) == after
        assert len(live.changes_between(DS, before, after)) == 1

    def test_a_key_on_a_new_entry_is_dropped(self):
        before = {"segments": [segment(0)]}
        after = {"segments": [segment(0), segment(2, routeMapsVersion="1")]}
        assert live.settle(DS, before, after) == {"segments": [segment(0), segment(2)]}

    def test_only_device_settings_segments_are_settled(self):
        before = {"segments": [segment(0)]}
        after = {"segments": [segment(0, routeMapsVersion="1")]}
        assert live.settle(Name.FIREWALL, before, after) == after
        nested = {"segments": [segment(0)], "other": {"routeMapsVersion": "1"}}
        assert live.settle(DS, before, nested) == nested

    def test_the_inputs_are_not_changed(self):
        after = {"segments": [segment(0, routeMapsVersion="1")]}
        _ = live.settle(DS, {"segments": [segment(0)]}, after)
        assert "routeMapsVersion" in after["segments"][0]


def test_has_key_looks_at_any_depth():
    assert live.has_key({"a": [{"b": {"k": 1}}]}, "k")
    assert not live.has_key({"a": [{"b": {"j": 1}}]}, "k")
    assert not live.has_key([1, "k", None], "k")


def test_a_redacted_path_shows_no_key_but_a_synthetic_one():
    path = Path(
        ("segments", Key("Lab Segment"), "outbound", Key(f"{live.SYNTHETIC_PREFIX}x"))
    )
    assert live.redact(path) == f"segments[*].outbound[{live.SYNTHETIC_PREFIX}x]"


def test_an_unreadable_edge_is_reported_by_error_type_alone():
    class Model(BaseModel):
        value: int

    with pytest.raises(ValidationError) as caught:
        _ = Model.model_validate({"value": "secret-lab-value"})
    message = str(live._unreadable(3, caught.value))  # pyright: ignore[reportPrivateUsage]
    assert message == (
        "edge #3 does not load: 1 validation error(s) of type int_parsing"
    )


class TestBackup:
    def original(self) -> live.Original:
        return live.Original(
            module_id=7,
            data={"k": [1]},
            refs={"r": {"id": 1}, "deviceSettings:clientConnector:config": {"t": "x"}},
        )

    def site(self, name: Name = Name.FIREWALL) -> live.Site:
        async def layer() -> list[Any]:
            return []

        return live.Site(None, None, 11, name, layer)  # pyright: ignore[reportArgumentType]

    @pytest.fixture(autouse=True)
    def backups(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(live.BACKUP_DIR_ENV, str(tmp_path))
        monkeypatch.setattr(live, "_backup_dir", None)

    def test_a_backup_is_a_private_file_with_the_data(self, tmp_path: pathlib.Path):
        path = live.save_backup(self.site(), self.original())
        assert path.parent == tmp_path
        assert path.stat().st_mode & 0o777 == 0o600
        assert json.loads(path.read_text()) == {
            "module": "firewall",
            "configuration_id": 11,
            "module_id": 7,
            "data": {"k": [1]},
            "refs": None,  # a firewall insert takes no refs
        }

    def test_only_a_qos_backup_holds_refs_and_never_a_client_connector_one(self):
        qos = live.save_backup(self.site(Name.QOS), self.original())
        assert json.loads(qos.read_text())["refs"] == {"r": {"id": 1}}
        ds = live.save_backup(self.site(Name.DEVICE_SETTINGS), self.original())
        assert json.loads(ds.read_text())["refs"] is None

    def test_the_file_name_holds_no_lab_identifier(self):
        path = live.save_backup(self.site(), self.original())
        assert re.fullmatch(r"[0-9a-f]{32}-firewall\.json", path.name)

    def test_a_backup_directory_inside_the_repository_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        repository = live._repository_root()  # pyright: ignore[reportPrivateUsage]
        assert repository is not None
        monkeypatch.setenv(live.BACKUP_DIR_ENV, str(repository / "packages" / "x"))
        monkeypatch.setattr(live, "_backup_dir", None)
        with pytest.raises(RuntimeError, match="outside the repository"):
            _ = live.save_backup(self.site(), self.original())


class FakeClient:
    """A client whose updates are refused, with text that names a lab entity."""

    async def update_configuration_module(
        self, request: object, *, level: object = None
    ) -> None:
        raise VcoOperationError("update", "refused for secret-lab-name")


class RefusingClient(FakeClient):
    async def insert_configuration_module(
        self, request: object, *, level: object = None
    ) -> None:
        raise VcoOperationError(
            "insert",
            "validationError",
            data={
                "error": [
                    {
                        "code": "FW00000023",
                        "path": "match.ipVersion",
                        "message": "for secret-lab-name",
                    }
                ]
            },
        )


async def test_a_refused_write_carries_its_codes_and_not_the_names_in_its_text():
    client = RefusingClient()
    live.redact_refusals(client)  # pyright: ignore[reportArgumentType]
    live.redact_refusals(client)  # pyright: ignore[reportArgumentType]  # once is enough
    with pytest.raises(VcoOperationError) as update:
        await client.update_configuration_module(None)
    with pytest.raises(VcoOperationError) as insert:
        await client.insert_configuration_module(None)
    assert "secret-lab-name" not in str(update.value) + str(insert.value)
    assert "FW00000023" in str(insert.value)


def firewall(data: dict[str, Any]) -> FirewallConfigurationModule:
    return FirewallConfigurationModule.model_validate(
        make_module_json("firewall", data)
    )


@pytest.fixture
def backups(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    monkeypatch.setenv(live.BACKUP_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(live, "_backup_dir", None)
    return tmp_path


async def test_a_restore_that_works_leaves_no_backup(backups: pathlib.Path):
    held = {"module": firewall({"segments": []})}

    async def layer() -> list[Any]:
        return [held["module"]]

    site = live.Site(FakeClient(), None, 1, Name.FIREWALL, layer)  # pyright: ignore[reportArgumentType]
    async with live.restoring(site):
        assert len(list(backups.iterdir())) == 1  # taken before the test writes
    assert list(backups.iterdir()) == []


async def test_a_restore_that_fails_names_the_backup_and_never_the_data(
    backups: pathlib.Path,
):
    original = {"segments": [{"segment": {"name": "secret-lab-name"}}]}
    held = {"module": firewall(original)}

    async def layer() -> list[Any]:
        return [held["module"]]

    site = live.Site(FakeClient(), None, 1, Name.FIREWALL, layer)  # pyright: ignore[reportArgumentType]
    with pytest.raises(pytest.fail.Exception) as caught:
        async with live.restoring(site):
            held["module"] = firewall({"segments": []})  # the test's write
    message = str(caught.value)
    (backup,) = backups.iterdir()
    assert str(backup) in message
    assert "secret-lab-name" not in message
    assert json.loads(backup.read_text())["data"] == original


class TestNoOverrideFlagCheck:
    CLEAN: ClassVar[dict[str, Any]] = {"segments": [segment(0)]}
    STRAY: ClassVar[dict[str, Any]] = {"segments": [segment(0, outboundOverride=True)]}

    def test_clean_data_passes(self):
        live.check_no_override_flag("x", self.CLEAN, self.CLEAN, self.CLEAN)
        live.check_no_override_flag("x", None, self.CLEAN, self.CLEAN)

    def test_a_key_the_module_did_not_have_is_reported_as_added_by_the_write(self):
        with pytest.raises(pytest.fail.Exception) as caught:
            live.check_no_override_flag("x", self.CLEAN, self.CLEAN, self.STRAY)
        message = str(caught.value)
        assert "read back carries" in message and "write added it" in message

    def test_a_key_the_module_already_had_is_reported_as_pre_existing(self):
        with pytest.raises(pytest.fail.Exception) as caught:
            live.check_no_override_flag("x", self.STRAY, self.CLEAN, self.STRAY)
        message = str(caught.value)
        assert "already had it before the write" in message
        assert "write added it" not in message


class PortalClient:
    async def portal(self, *args: object, **kwargs: object) -> None:
        raise VcoOperationError(
            "portal",
            "validationError",
            data={
                "error": [{"code": "E1", "path": "p", "message": "for secret-lab-name"}]
            },
        )


async def test_a_refused_delete_and_a_refused_effective_read_carry_codes_alone():
    site = live.Site(PortalClient(), None, 1, Name.FIREWALL, None)  # pyright: ignore[reportArgumentType]
    with pytest.raises(VcoOperationError) as delete:
        await live.delete_module(site, 5)
    with pytest.raises(VcoOperationError) as effective:
        _ = await live.effective_data(PortalClient(), 1, Name.FIREWALL, None)  # pyright: ignore[reportArgumentType]
    for caught in (delete, effective):
        assert "secret-lab-name" not in str(caught.value)
        assert "E1" in str(caught.value)


SECRET = Key("secret-lab-name")


def planned(
    path: Path, as_read: object, intended: object, stored: object
) -> PlannedChange:
    return PlannedChange(path, as_read, intended, stored)


def test_where_redacts_paths_and_marks_refs():
    changes = [
        planned(Path(("segments", SECRET, "outbound")), 1, 2, 1),
        PlannedChange(Path(("a", Key(5))), 1, 2, 1, part="refs"),
    ]
    assert live.where(changes) == "segments[*].outbound, refs a[*]"
    assert live.where([]) == "none"


def test_stored_otherwise_goes_to_the_deepest_difference():
    lost = [
        planned(
            Path(("links", SECRET)),
            {"mtu": 1500},
            {"mtu": 1400, "description": "a"},
            {"mtu": 1500, "description": "a"},
        )
    ]
    assert [str(p) for p in live.stored_otherwise(lost)] == [
        "links[secret-lab-name].mtu"
    ]


def test_outcome_names_the_class_and_redacted_paths_only():
    error = WriteNotAppliedError(
        1,
        "v1",
        module_name="firewall",
        plan=None,  # pyright: ignore[reportArgumentType]
        module=None,  # pyright: ignore[reportArgumentType]
        lost_edits=[
            planned(Path(("segments", SECRET, "x")), "lab-a", "lab-b", "lab-c")
        ],
        applied=[planned(Path(("firewall_enabled",)), True, False, False)],
        server_changes=[ServerChange(Path(("future",)), 1, 2)],
    )
    text = live.outcome(error)
    assert text == (
        "WriteNotAppliedError on the firewall module: lost edits segments[*].x; "
        "stored otherwise than sent at segments[*].x; applied firewall_enabled; "
        "server changes future"
    )
    conflict = ConfigConflictError(1, "v1", "the VCO refused the version")
    assert live.outcome(conflict).startswith(
        "ConfigConflictError (the VCO refused the version) on the unnamed module: "
    )


def test_same_value_ignores_the_keys_the_orchestrator_assigns():
    assert live.same_value({"name": "a", "ruleLogicalId": "x"}, {"name": "a"})
    assert not live.same_value({"name": "a"}, {"name": "b"})
    assert live.same_value(3, 3)


def test_value_at_follows_names_only():
    document = {"qoe": {"voice": {"latency": {"red": 5}}}, "links": [{"a": 1}]}
    assert live.value_at(document, Path(("qoe", "voice", "latency", "red"))) == 5
    assert live.value_at(document, Path(("qoe", "video"))) is MISSING
    assert live.value_at(document, Path(("links", Key("x")))) is MISSING


def test_the_checks_fail_with_redacted_paths():
    change = RawChange(Path(("segments", SECRET, "x")), 1, 2)
    live.check_paths("edit", "the plan", [change], [change.path])
    with pytest.raises(pytest.fail.Exception) as caught:
        live.check_paths("edit", "the plan", [change], [Path(("y",))])
    assert str(caught.value) == "edit: the plan lists ~ segments[*].x; expected y"
    entry = planned(Path(("segments", SECRET, "x")), 1, 2, 1)
    live.check_entries("save", "lost_edits", [entry], [entry.path])
    with pytest.raises(pytest.fail.Exception) as caught:
        live.check_entries("save", "lost_edits", [entry], [])
    assert str(caught.value) == "save: lost_edits lists segments[*].x; expected none"


def test_check_as_read_compares_version_and_raw_data():
    def module(version: str, **data: object) -> FirewallConfigurationModule:
        return FirewallConfigurationModule.model_validate(
            make_module_json("firewall", {"segments": [], **data}, version=version)
        )

    live.check_as_read("x", module("v1"), module("v1"))
    with pytest.raises(pytest.fail.Exception, match="another version"):
        live.check_as_read("x", module("v1"), module("v2"))
    with pytest.raises(pytest.fail.Exception) as caught:
        live.check_as_read("x", module("v1", future=1), module("v1"))
    assert str(caught.value).endswith("a read made right after: + future")


HOST = "vco.example.invalid"


FIREWALL: dict[str, Any] = {"segments": [], "firewall_enabled": True, "future": 1}
"""A firewall module's data; ``future`` is a key the models do not know."""


def client_over(
    handler: Callable[[httpx.Request], httpx.Response], host: str = HOST
) -> VcoClient:
    """A client connected to *host*, whose requests go to *handler*."""
    return VcoClient(host, "test-token", transport=make_transport(handler))


def firewall_disabled() -> FirewallConfigurationModule:
    module = FirewallConfigurationModule.model_validate(
        make_module_json("firewall", copy.deepcopy(FIREWALL), configuration_id=10)
    )
    module.data.firewall_enabled = False
    return module


def held_elsewhere(store: ModuleStore) -> Callable[[httpx.Request], httpx.Response]:
    """*store*, but a read of configuration 10 finds no module in it."""

    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["method"] != "configuration/getConfiguration":
            return store(request)
        config = {**store.configurations[10], "modules": []}
        return httpx.Response(200, json=make_jsonrpc_response(result=config))

    return handler


async def write_error(
    *, stale: bool, read_back: bool = True, **knobs: bool
) -> ModuleWriteError:
    """The error of writing ``firewall_disabled`` to a store with *knobs*."""
    module = firewall_disabled()
    store = ModuleStore(**knobs)
    store.add_configuration({"id": 10, "name": "Profile 10"})
    store.rows[1] = make_module_json(
        "firewall", copy.deepcopy(FIREWALL), configuration_id=10
    )
    if stale:
        store.other_writer(1, lambda row: row["data"].update(future=2))
    client = client_over(store if read_back else held_elsewhere(store))
    with pytest.raises(ModuleWriteError) as caught:
        _ = await client.write_module(module, plan_module(module))
    return caught.value


async def test_refusal_form_of_a_stale_write_whose_module_was_read():
    form = live.refusal_form
    precondition = await write_error(stale=True, conditional_check=True)
    assert isinstance(precondition, ConfigConflictError) and precondition.stated
    assert form(precondition) == "refused with the precondition error"
    zero_rows = await write_error(stale=True, return_data=False)
    assert isinstance(zero_rows, ConfigConflictError) and zero_rows.stated
    assert form(zero_rows) == "refused with a row count of zero"
    inferred = await write_error(stale=True)
    assert isinstance(inferred, ConfigConflictError) and not inferred.stated
    assert form(inferred) == "refused with the module row as stored"


def unknown(
    answer: WriteAnswer,
    refusal: ConfigConflictError | None = None,
    detail: str = "its configuration does not hold the module",
) -> WriteOutcomeUnknownError:
    """The error of a write whose stored module could not be read, raised while handling *refusal*."""
    error = WriteOutcomeUnknownError(
        1,
        "v1",
        detail,
        answer=answer,
        module_name="firewall",
        plan=plan_module(firewall_disabled()),
    )
    error.__context__ = refusal
    return error


def test_refusal_form_of_a_write_whose_module_could_not_be_read():
    form = live.refusal_form
    unread = ", and the module as stored could not be read"
    assert form(unknown("counted")) == "not refused: the write was counted" + unread
    zero_rows = ConfigConflictError(1, "v1", "no rows were modified")
    assert form(unknown("refused", zero_rows)) == (
        "refused with a row count of zero" + unread
    )
    assert form(unknown("refused")) == "refused" + unread
    assert form(unknown("row")) == (
        "answered with a row that could not be taken as this module"
    )


def test_outcome_of_a_write_whose_module_could_not_be_read():
    assert live.outcome(unknown("counted")) == (
        "WriteOutcomeUnknownError (its configuration does not hold the module; "
        "answer counted) on the firewall module: sent ~ firewall_enabled"
    )


def test_outcome_of_a_write_whose_module_could_not_be_read_shows_no_number():
    error = unknown("counted", detail="configuration 4711 does not hold module 8123")
    assert live.outcome(error) == (
        "WriteOutcomeUnknownError (configuration # does not hold module #; "
        "answer counted) on the firewall module: sent ~ firewall_enabled"
    )


def test_where_marks_the_order_of_a_list():
    path = Path(("segments", SECRET, "outbound"))
    order = PlannedChange(path, ["a", "b"], ["b", "a"], ["a", "b"], moves=())
    assert live.where([order]) == "order of segments[*].outbound"


class PassThrough:
    """A capture whose ``disabled`` changes nothing, so that ``capsys`` sees what is printed past it."""

    def disabled(self) -> contextlib.AbstractContextManager[None]:
        return contextlib.nullcontext()


async def test_a_counted_write_that_was_not_read_back_is_not_a_refusal(
    capsys: pytest.CaptureFixture[str],
):
    error = await write_error(stale=False, return_data=False, read_back=False)
    assert isinstance(error, WriteOutcomeUnknownError) and error.answer == "counted"
    live.record_refusal(PassThrough(), "a save", error)  # pyright: ignore[reportArgumentType]
    assert capsys.readouterr().out == (
        "\na save: not refused: the write was counted, and the module as stored "
        "could not be read (WriteOutcomeUnknownError)\n"
    )
