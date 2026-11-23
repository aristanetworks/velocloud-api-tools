"""Offline checks of the support code of the live save tests (tests/support/live_support.py).

The live tests are deselected without ``-m integration`` and need an Orchestrator;
what they rely on to restore a lab and to keep its names out of their output is
checked here with no lab.
"""

import ast
import asyncio
import contextlib
import copy
import inspect
import json
import pathlib
import re
import signal
from collections.abc import Callable, Generator
from typing import Any, ClassVar, Literal

import httpx
import live_support as live
import pytest
import pyvelo
from pydantic import MISSING, BaseModel, ValidationError
from pyvelo.client import (
    ConfigConflictError,
    ModuleWriteError,
    VcoClient,
    VcoHttpError,
    VcoOperationError,
    WriteNotAppliedError,
    WriteOutcomeUnknownError,
)
from pyvelo.client.enums.configuration import ConfigurationModuleName as Name
from pyvelo.client.exceptions import LossyFieldError, WriteAnswer
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import (
    ConfigurationModuleUpdate,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
    InsertQosConfigurationModule,
    UpdateConfigurationModule,
)
from pyvelo.client.schemas.edge import DeleteEdge
from pyvelo.client.schemas.enterprise import (
    GetEnterprise,
    GetEnterpriseEdges,
    GetEnterpriseEdgesItem,
)
from pyvelo.client.schemas.profiles import GetEnterpriseConfigurations
from pyvelo.core import PatchError, RowsModifiedConfirmation
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
    class Row(BaseModel):
        value: int

    with pytest.raises(ValidationError) as caught:
        _ = Row.model_validate({"value": "secret-lab-value"})
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


@pytest.fixture(autouse=True)
def backups(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[pathlib.Path]:
    """Backups go to a temporary directory, the opt-in is unset, and the test is a writer."""
    monkeypatch.setenv(live.BACKUP_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(live, "_backup_dir", None)
    monkeypatch.setattr(live, "_LEFT", {})
    monkeypatch.delenv(live.WRITE_HOST_ENV, raising=False)
    monkeypatch.delenv(live.WRITE_ENTERPRISE_ENV, raising=False)
    with live.writer_test(True):
        yield tmp_path


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
    *,
    stale: bool,
    read_back: bool = True,
    clash: bool = False,
    handler: Callable[[ModuleStore], Callable[[httpx.Request], httpx.Response]]
    | None = None,
    **knobs: bool,
) -> ModuleWriteError:
    """The error of writing ``firewall_disabled`` to a store with *knobs*.

    With *clash* the write also changes ``future`` (1 to 2) and the other
    writer sets it to 3. *handler* wraps the store in place of *read_back*.
    """
    module = firewall_disabled()
    plan = plan_module(module)
    if clash:
        plan = plan_module(module, {**FIREWALL, "future": 2, "firewall_enabled": False})
    store = ModuleStore(**knobs)
    store.add_configuration({"id": 10, "name": "Profile 10"})
    store.rows[1] = make_module_json(
        "firewall", copy.deepcopy(FIREWALL), configuration_id=10
    )
    if stale:
        future = 3 if clash else 2
        store.other_writer(1, lambda row: row["data"].update(future=future))
    if handler is not None:
        client = client_over(handler(store))
    else:
        client = client_over(store if read_back else held_elsewhere(store))
    with pytest.raises(ModuleWriteError) as caught:
        _ = await client.write_module(module, plan)
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


async def test_refusal_form_of_a_write_stored_otherwise_than_sent_is_no_refusal():
    error = await write_error(stale=True, clash=True)
    assert isinstance(error, WriteNotAppliedError)
    assert live.refusal_form(error) == "not refused: stored otherwise than sent"


async def test_refusal_form_of_a_refusal_whose_read_back_did_not_parse():
    """Pydantic's ``ValidationError`` carries no ``__context__``, so the refusal is not in this chain: the clause says only ``refused``."""

    def unparsable(store: ModuleStore) -> Callable[[httpx.Request], httpx.Response]:
        def handler(request: httpx.Request) -> httpx.Response:
            if (
                json.loads(request.content)["method"]
                != "configuration/getConfiguration"
            ):
                return store(request)
            config = {**store.configurations[10], "modules": "not a list"}
            return httpx.Response(200, json=make_jsonrpc_response(result=config))

        return handler

    error = await write_error(stale=True, handler=unparsable, conditional_check=True)
    assert isinstance(error, WriteOutcomeUnknownError) and error.answer == "refused"
    assert isinstance(error.__cause__, ValidationError)
    assert live.refusal_form(error) == (
        "refused, and the module as stored could not be read"
    )


def test_refusal_form_walks_the_chain_to_the_refusal():
    refusal = ConfigConflictError(1, "v1", "no rows were modified")
    middle = ValueError("read back")
    middle.__context__ = refusal
    error = unknown("refused")
    error.__cause__ = middle
    unread = ", and the module as stored could not be read"
    assert live.refusal_form(error) == "refused with a row count of zero" + unread
    refusal.__cause__ = RuntimeError("the Orchestrator's precondition error")
    assert live.refusal_form(error) == ("refused with the precondition error" + unread)
    middle.__context__ = error  # a chain that loops back ends
    assert live.refusal_form(error) == "refused" + unread


async def test_refusal_form_finds_the_refusal_a_read_that_found_no_module_was_raised_in():
    unread = ", and the module as stored could not be read"
    error = await write_error(stale=True, conditional_check=True, read_back=False)
    assert live.refusal_form(error) == ("refused with the precondition error" + unread)


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


class Model(BaseModel):
    value: int


def validation_error() -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        _ = Model.model_validate({"value": "secret-lab-value"})
    return caught.value


class ValidatorError:
    def __init__(self, code: str) -> None:
        self.code: str = code
        self.message: str = "secret-lab-name is wrong"


class SaveBlocked(Exception):
    """Shaped as pyvelo-intent's ``SaveValidationError``: ``errors`` with a code each."""

    def __init__(self) -> None:
        self.errors: list[ValidatorError] = [ValidatorError("B2"), ValidatorError("A1")]
        super().__init__("secret-lab-name is wrong")


def test_a_validation_error_shows_its_types_and_no_input():
    assert live.failure_text(validation_error()) == (
        "ValidationError for Model: 1 error(s) of type int_parsing"
    )


def test_a_refusal_shows_its_codes():
    error = VcoOperationError(
        "configuration/getConfiguration",
        "validationError",
        data={"error": [{"code": "E1", "path": "p", "message": "secret-lab-name"}]},
    )
    assert live.failure_text(error) == (
        "VcoOperationError in configuration/getConfiguration: E1 p"
    )


def test_a_refusal_the_client_already_redacted_still_shows_its_codes():
    error = VcoOperationError(
        "configuration/updateConfigurationModule",
        "validationError",
        data={
            "error": [
                {"code": "E1", "path": "a.b[0]", "message": "secret-lab-name"},
                {"code": "E_2", "path": "", "message": "secret-lab-name"},
            ]
        },
    )
    redacted_error = live._redacted(error)  # pyright: ignore[reportPrivateUsage]
    assert "secret-lab-name" not in str(redacted_error)
    assert (
        live.refusal_codes(redacted_error)
        == live.refusal_codes(error)
        == ("E1 a.b[0]; E_2")
    )
    assert live.failure_text(redacted_error) == (
        "DataRefused in configuration/updateConfigurationModule: E1 a.b[0]; E_2"
    )


def test_a_bracketed_name_in_a_refusal_is_not_read_as_a_code():
    named = "[HQ] Munich-Branch-7"
    in_text = VcoOperationError("edge/getEdge", f"no such edge {named}")
    in_message = VcoOperationError(
        "edge/getEdge",
        "validationError",
        data={"error": [{"code": "E1", "path": "a", "message": f"{named} clash"}]},
    )
    for error in (in_text, in_message, live._redacted(in_message)):  # pyright: ignore[reportPrivateUsage]
        for shown in (live.failure_text(error), live.refusal_codes(error)):
            assert shown is not None and "HQ" not in shown and "Munich" not in shown
        assert "HQ" not in str(live._redacted(error))  # pyright: ignore[reportPrivateUsage]
    assert live.refusal_codes(in_text) == "no error code"
    assert live.refusal_codes(in_message) == "E1 a"
    assert live.refusal_codes(live._redacted(in_message)) == "E1 a"  # pyright: ignore[reportPrivateUsage]


def test_a_name_in_brackets_in_a_refusal_path_is_hidden():
    path = "segments[Munich-Branch-7].rules[12].outbound[HQ]"
    error = VcoOperationError(
        "edge/getEdge",
        "validationError",
        data={"error": [{"code": "E1", "path": path}]},
    )
    expected = "E1 segments[*].rules[12].outbound[*]"
    assert live.refusal_codes(error) == expected
    assert live.refusal_codes(live._redacted(error)) == expected  # pyright: ignore[reportPrivateUsage]
    odd = VcoOperationError(
        "edge/getEdge",
        "validationError",
        data={"error": [{"code": "E1", "path": "a.Munich Branch-7"}]},
    )
    assert live.refusal_codes(odd) == "E1 *"


def test_an_http_error_shows_its_status():
    error = VcoHttpError(502, "<html>secret-lab-name</html>")
    assert live.failure_text(error) == "VcoHttpError: HTTP 502"


def test_a_refused_patch_shows_its_kind_and_where():
    path = Path(("segments", SECRET, "outbound"))
    error = PatchError("secret-lab-name is already in the list", path, "exists")
    assert live.failure_text(error) == "PatchError (exists) at segments[*].outbound"


def test_an_edit_a_save_cannot_write_shows_where():
    error = LossyFieldError("firewall", [Path(("segments", SECRET, "x"))])
    assert live.failure_text(error) == (
        "LossyFieldError in the firewall module at segments[*].x"
    )


def test_a_write_error_shows_its_outcome():
    error = ConfigConflictError(1, "v1", "the VCO refused the version")
    assert live.failure_text(error) == live.outcome(error)


def test_validator_errors_show_their_codes():
    assert live.failure_text(SaveBlocked()) == (
        "SaveBlocked: 2 error(s) with code A1, B2"
    )


def test_any_other_error_shows_its_class():
    assert live.failure_text(KeyError("secret-lab-name")) == "KeyError"
    assert live.failure_text(AssertionError("secret-lab-name")) == "AssertionError"


def test_notes_arguments_and_causes_are_never_shown():
    cause = ValueError("secret-lab-name in a cause")
    for error in (
        KeyError("secret-lab-name"),
        PatchError("secret-lab-name", Path(("x",)), "exists"),
        VcoHttpError(502, "secret-lab-name"),
        SaveBlocked(),
    ):
        error.add_note("secret-lab-name in a note")
        error.__cause__ = cause
        error.__context__ = cause
        assert "secret-lab-name" not in str(live.failure_text(error))


def test_what_the_suite_words_itself_is_left():
    with pytest.raises(pytest.fail.Exception) as failed:
        pytest.fail("a message the test wrote", pytrace=False)
    with pytest.raises(pytest.skip.Exception) as skipped:
        pytest.skip("no target")
    assert live.failure_text(failed.value) is None
    assert live.failure_text(skipped.value) is None


def test_an_error_of_the_suite_is_its_own_message():
    error = live.LiveSuiteError("edge #3 does not load")
    assert live.failure_text(error) == "edge #3 does not load"


def test_an_unreadable_edge_is_an_error_of_the_suite():
    assert issubclass(live.LabUnreadable, live.LiveSuiteError)


class FakeItem:
    """A test item: its marks, and the fixtures it uses."""

    def __init__(self, marks: tuple[str, ...], fixtures: tuple[str, ...]) -> None:
        self.marks: tuple[str, ...] = marks
        self.fixturenames: list[str] = list(fixtures)

    def get_closest_marker(self, name: str) -> object:
        return name if name in self.marks else None


class FakeReport:
    def __init__(self, failed: bool) -> None:
        self.failed: bool = failed
        self.longrepr: object = "as pytest made it"


def raising(error: BaseException) -> Callable[[], object]:
    def body() -> None:
        raise error

    return body


def redacted(
    body: Callable[[], object],
    *,
    marks: tuple[str, ...] = ("integration",),
    fixtures: tuple[str, ...] = ("lab", "capsys"),
    failed: bool = True,
    when: Literal["setup", "call", "teardown"] = "call",
) -> str:
    """What ``redact_report`` leaves as the report of a test whose body is *body*."""
    report = FakeReport(failed)
    call = pytest.CallInfo.from_call(body, when)
    live.redact_report(FakeItem(marks, fixtures), call, report)  # pyright: ignore[reportArgumentType]
    assert isinstance(report.longrepr, str)
    return report.longrepr


HERE = r" \(raised at test_live_support\.py:\d+\)"


def test_a_live_failure_is_its_text_and_where_it_was_raised():
    shown = redacted(raising(KeyError("secret-lab-name")))
    assert re.fullmatch("KeyError" + HERE, shown)


def test_the_line_is_the_one_that_raised():
    def body() -> None:
        frame = inspect.currentframe()
        assert frame is not None
        raised_at.append(frame.f_lineno + 1)
        raise KeyError("secret-lab-name")

    raised_at: list[int] = []
    shown = redacted(body)
    assert shown == f"KeyError (raised at test_live_support.py:{raised_at[0]})"


def test_a_failure_in_a_fixture_is_redacted_too():
    error = raising(live.LabUnreadable("edge #3 does not load"))
    for when in ("setup", "teardown"):
        shown = redacted(error, when=when)
        assert re.fullmatch("edge #3 does not load" + HERE, shown)
        shown = redacted(raising(KeyError("secret-lab-name")), when=when)
        assert re.fullmatch("KeyError" + HERE, shown)


def test_the_place_is_the_last_frame_in_the_tests():
    # Raised inside pydantic: the report names the line of this file that called it.
    shown = redacted(lambda: Model.model_validate({"value": "secret-lab-value"}))
    text = r"ValidationError for Model: 1 error\(s\) of type int_parsing"
    assert re.fullmatch(text + HERE, shown)


def test_the_place_is_in_the_test_and_not_in_the_support_code():
    # Raised inside live_support, which is in a tests directory too: the report
    # names the line of this file that called it.
    shown = redacted(lambda: live.redact(1))  # pyright: ignore[reportArgumentType]
    assert re.fullmatch("AttributeError" + HERE, shown)


def test_an_error_of_the_suite_keeps_its_message():
    shown = redacted(raising(live.LiveSuiteError("edge #3 does not load")))
    assert re.fullmatch("edge #3 does not load" + HERE, shown)


def test_other_tests_and_worded_failures_are_left():
    def worded() -> None:
        pytest.fail("a message the test wrote", pytrace=False)

    error = raising(KeyError("x"))
    assert redacted(error, marks=()) == "as pytest made it"
    # A live test that does not read configuration: the analytics tests.
    assert redacted(error, fixtures=("analytics",)) == "as pytest made it"
    assert redacted(worded) == "as pytest made it"
    assert redacted(lambda: None, failed=False) == "as pytest made it"


ENTERPRISE = 7


QOS: dict[str, Any] = {
    "segments": [],
    "serviceRateLimit": {"enabled": False, "inputType": "percent", "value": 1},
}
"""A QoS module's data; the tests edit ``serviceRateLimit.value``."""


REFUSAL: dict[str, Any] = {
    "code": -32003,
    "message": "validationError",
    "data": {
        "error": [
            {
                "code": "DS00000001",
                "path": "lan.networks",
                "message": "for secret-lab-name",
            }
        ]
    },
}
"""The Orchestrator's refusal of data it does not take; its text names a lab entity."""


class FakeLab:
    """A fake Orchestrator for a live run: a ``ModuleStore``, plus the edges, profiles and enterprise it lists.

    It is an ``httpx`` handler, as the store is: a client built over it runs
    its own request building and parsing. Module updates, inserts,
    ``getConfiguration`` and the configuration stack of an edge go to the
    store; the listings, the enterprise and the delete of a module are
    answered here.
    """

    def __init__(self) -> None:
        self.store: ModuleStore = ModuleStore()
        self.edges: dict[int, dict[str, Any]] = {}
        self.profiles: dict[int, dict[str, Any]] = {}
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.refused: set[int] = set()
        """The IDs of the modules whose every update is refused (``REFUSAL``)."""
        self.refused_methods: set[str] = set()
        """The methods that are refused the same way, whatever they ask."""
        self.enterprise_error: bool = False
        self.enterprise_failure: Literal["http", "empty", "no_id"] | None = None
        """How ``enterprise/getEnterprise`` fails apart from a refusal: an HTTP status, no result, a result with no ID."""
        self.before: Callable[[str], None] | None = None
        """Called with the method of each request before it is answered."""

    def add_profile(
        self, profile_id: int, *, description: str | None = None, edge_count: int = 0
    ) -> None:
        name = f"Profile {profile_id}"
        self.profiles[profile_id] = {
            "id": profile_id,
            "name": name,
            "description": description,
            "edgeCount": edge_count,
        }
        self.store.add_configuration({"id": profile_id, "name": name})

    def add_edge(
        self,
        edge_id: int,
        configuration_id: int,
        profile_id: int,
        *,
        description: str | None = None,
    ) -> None:
        self.edges[edge_id] = {
            "id": edge_id,
            "name": f"edge-{edge_id}",
            "description": description,
        }
        self.store.add_configuration(
            {"id": configuration_id, "name": "Edge Specific Profile"}
        )
        self.store.add_stack(configuration_id, profile_id, edge_id=edge_id)

    def module(
        self, module_id: int, configuration_id: int, name: str, data: dict[str, Any]
    ) -> None:
        self.store.rows[module_id] = make_module_json(
            name,
            copy.deepcopy(data),
            module_id=module_id,
            configuration_id=configuration_id,
        )

    def data(self, module_id: int) -> dict[str, Any]:
        return self.store.rows[module_id]["data"]

    def methods(self) -> list[str]:
        """The method of each request, without its namespace."""
        return [method.rsplit("/", 1)[-1] for method, _ in self.requests]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] = json.loads(request.content)
        method: str = body["method"]
        params: dict[str, Any] = body.get("params") or {}
        self.requests.append((method, params))
        if self.before is not None:
            self.before(method)
        error: dict[str, Any] | None = None
        result: object = None
        update = method == "configuration/updateConfigurationModule"
        if method in self.refused_methods or (update and params["id"] in self.refused):
            error = REFUSAL
        elif method == "enterprise/getEnterprise":
            if self.enterprise_failure == "http":
                return httpx.Response(503, text="secret-lab-name is down")
            if self.enterprise_failure == "empty":
                return httpx.Response(200, json=make_jsonrpc_response())
            if self.enterprise_error:
                error = {"code": -32000, "message": "invalid enterprise context"}
            result = {"id": params.get("enterpriseId", ENTERPRISE)}
            if self.enterprise_failure == "no_id":
                result = {}
        elif method == "enterprise/getEnterpriseEdges":
            result = {"metaData": {"more": False}, "data": list(self.edges.values())}
        elif method == "enterprise/getEnterpriseConfigurations":
            result = list(self.profiles.values())
        elif method == "configuration/deleteConfigurationModule":
            del self.store.rows[params["id"]]
            result = {"rows": 1}
        else:
            return self.store(request)
        if error is not None:
            return httpx.Response(200, json=make_jsonrpc_response(error=error))
        return httpx.Response(200, json=make_jsonrpc_response(result=result))


OPT_IN = {live.WRITE_HOST_ENV: HOST, live.WRITE_ENTERPRISE_ENV: str(ENTERPRISE)}


MARKED = "lab edge, pyvelo-it-target"
"""The description of an edge the owner marked for the suite."""


REPLACEABLE = "pyvelo-it-target pyvelo-it-replace-modules"
"""The description of an edge whose modules the suite may also delete and insert."""


def lab_fake() -> FakeLab:
    """Profile 20 (marked, no edge counted) with QoS module 2; edge 5 (marked) with QoS module 1; edge 6, unmarked, with QoS module 3."""
    fake = FakeLab()
    fake.add_profile(20, description="pyvelo-it-target")
    fake.module(2, 20, "QOS", QOS)
    fake.add_edge(5, 10, 20, description=MARKED)
    fake.module(1, 10, "QOS", QOS)
    fake.add_edge(6, 11, 20)
    fake.module(3, 11, "QOS", QOS)
    return fake


async def opened(fake: FakeLab, monkeypatch: pytest.MonkeyPatch) -> live.Lab:
    """The lab of *fake*, discovered by a client that has opted in to write to it."""
    for name, value in OPT_IN.items():
        monkeypatch.setenv(name, value)
    return await live.discover_lab(client_over(fake), None)


async def raise_limit(site: live.Site, value: int) -> None:
    """Write the QoS module of *site* with its rate limit value set to *value*, as a test would."""
    module = await site.read_present()
    data = copy.deepcopy(module.raw_data)
    assert isinstance(data, dict)
    data["serviceRateLimit"]["value"] = value
    await live.write_data(site, module, data)


async def access_for(
    environ: dict[str, str],
    *,
    connected: str = HOST,
    enterprise_id: int | None = None,
    enterprise_error: bool = False,
    enterprise_failure: Literal["http", "empty", "no_id"] | None = None,
) -> tuple[live.WriteAccess, list[tuple[str, dict[str, Any]]]]:
    """The access *environ* gives a client connected to *connected*, and what it asked the Orchestrator."""
    fake = FakeLab()
    fake.enterprise_error = enterprise_error
    fake.enterprise_failure = enterprise_failure
    access = await live.check_write_access(
        client_over(fake, connected), enterprise_id, environ
    )
    return access, fake.requests


async def test_an_unset_opt_in_is_off_and_asks_nothing():
    access, requests = await access_for({})
    assert access == live.WriteAccess(False, live.WRITES_OFF, False)
    assert access.refusal == (
        "live write tests are off: set PYVELO_IT_WRITE_HOST to the host name "
        "of the Orchestrator this suite may write to and "
        "PYVELO_IT_WRITE_ENTERPRISE_ID to the ID of the enterprise, then run again"
    )
    assert requests == []


async def test_the_host_and_the_enterprise_of_the_client_are_granted():
    access, requests = await access_for(OPT_IN)
    assert access == live.WriteAccess(granted=True, refusal="")
    assert requests == [("enterprise/getEnterprise", {})]


async def test_the_host_is_compared_without_case_or_a_last_dot():
    environ = {**OPT_IN, live.WRITE_HOST_ENV: " VCO.Example.Invalid. "}
    access, _ = await access_for(environ)
    assert access.granted


async def test_the_enterprise_the_requests_name_is_the_one_read():
    environ = {**OPT_IN, live.WRITE_ENTERPRISE_ENV: "9"}
    access, requests = await access_for(environ, enterprise_id=9)
    assert access.granted
    assert requests == [("enterprise/getEnterprise", {"enterpriseId": 9})]


async def refused(
    environ: dict[str, str],
    reason: str,
    remedy: str,
    *,
    connected: str = HOST,
    enterprise_id: int | None = None,
    enterprise_error: bool = False,
    enterprise_failure: Literal["http", "empty", "no_id"] | None = None,
) -> None:
    """*environ* is refused for *reason*, with what to do, in a message with neither the host nor the token."""
    access, _ = await access_for(
        environ,
        connected=connected,
        enterprise_id=enterprise_id,
        enterprise_error=enterprise_error,
        enterprise_failure=enterprise_failure,
    )
    assert access == live.WriteAccess(
        False, f"writes refused: {reason}; nothing was written; {remedy}", True
    )
    assert HOST not in access.refusal and "test-token" not in access.refusal


async def test_an_opt_in_that_does_not_match_is_refused():
    await refused(
        {live.WRITE_HOST_ENV: HOST},
        "PYVELO_IT_WRITE_HOST and PYVELO_IT_WRITE_ENTERPRISE_ID must both be set",
        "set the one that is missing, or unset the other to turn writes off",
    )
    await refused(
        {**OPT_IN, live.WRITE_ENTERPRISE_ENV: "seven"},
        "PYVELO_IT_WRITE_ENTERPRISE_ID is not a whole number",
        "set it to the ID of the enterprise, in digits",
    )
    await refused(
        {**OPT_IN, live.WRITE_HOST_ENV: f"user@{HOST}"},
        "PYVELO_IT_WRITE_HOST must be a host name alone, with no scheme, port, "
        "path or user",
        "set it to the host name VCO_HOST names, and nothing else",
    )
    await refused(
        OPT_IN,
        "PYVELO_IT_WRITE_HOST does not name the Orchestrator this run is connected to",
        "set it to the host name VCO_HOST names, or point VCO_HOST at the "
        "Orchestrator you mean to write to",
        connected="other.example.invalid",
    )
    await refused(
        OPT_IN,
        "PYVELO_IT_WRITE_ENTERPRISE_ID is not the enterprise this run addresses",
        "set it to the ID of the enterprise the token belongs to, or that "
        "VCO_ENTERPRISE_ID names",
        enterprise_id=9,
    )
    await refused(
        OPT_IN,
        "the enterprise this run addresses could not be read from the Orchestrator "
        "(no error code)",
        "set VCO_ENTERPRISE_ID if the token is not an enterprise's own",
        enterprise_error=True,
    )
    await refused(
        OPT_IN,
        "the enterprise this run addresses could not be read from the Orchestrator "
        "(HTTP 503)",
        "check that VCO_HOST names an Orchestrator that answers and that "
        "VCO_TOKEN is accepted by it, then run again",
        enterprise_failure="http",
    )
    await refused(
        OPT_IN,
        "the enterprise this run addresses could not be read from the Orchestrator "
        "(no answer)",
        "set VCO_ENTERPRISE_ID if the token is not an enterprise's own",
        enterprise_failure="empty",
    )
    await refused(
        OPT_IN,
        "the enterprise this run addresses could not be read from the Orchestrator "
        "(the answer has no ID)",
        "check that VCO_HOST names an Orchestrator and that the token may "
        "read its enterprise",
        enterprise_failure="no_id",
    )


async def test_connect_skips_without_the_variables(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VCO_HOST", raising=False)
    monkeypatch.setenv("VCO_TOKEN", "test-token")
    with pytest.raises(pytest.skip.Exception, match="VCO_HOST not set"):
        async with live.connect():
            pass
    monkeypatch.setenv("VCO_HOST", HOST)
    monkeypatch.delenv("VCO_TOKEN")
    with pytest.raises(pytest.skip.Exception, match="VCO_TOKEN not set"):
        async with live.connect():
            pass


async def test_connect_puts_the_write_guard_on_the_client(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("VCO_HOST", HOST)
    monkeypatch.setenv("VCO_TOKEN", "test-token")
    async with live.connect() as client:
        run = live.run_of(client)
        assert run.access == live.WriteAccess()
        assert live.run_of(client) is run
        request = UpdateConfigurationModule(
            configuration_module_id=1, update=ConfigurationModuleUpdate(data={})
        )
        with pytest.raises(live.WritesRefused, match="live write tests are off"):
            _ = await client.update_configuration_module(request)


def qos_update(module_id: int) -> UpdateConfigurationModule:
    return UpdateConfigurationModule(
        configuration_module_id=module_id,
        update=ConfigurationModuleUpdate(data=copy.deepcopy(QOS)),
    )


async def test_without_the_opt_in_restoring_skips_and_nothing_is_sent():
    fake = lab_fake()
    lab = await live.discover_lab(client_over(fake), None)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    fake.requests.clear()
    with pytest.raises(pytest.skip.Exception, match="live write tests are off"):
        async with live.restoring(site):
            pass
    with pytest.raises(live.WritesRefused, match="live write tests are off"):
        _ = await lab.client.update_configuration_module(qos_update(1))
    assert fake.requests == []


async def test_an_opt_in_that_does_not_match_fails_the_test(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv(live.WRITE_HOST_ENV, "other.example.invalid")
    monkeypatch.setenv(live.WRITE_ENTERPRISE_ENV, str(ENTERPRISE))
    fake = lab_fake()
    lab = await live.discover_lab(client_over(fake), None)
    with pytest.raises(pytest.fail.Exception, match="^writes refused: "):
        async with live.restoring(lab.edge_site(lab.layouts[0], Name.QOS)):
            pass
    with pytest.raises(pytest.fail.Exception, match="^writes refused: "):
        _ = await lab.pick_writable_edge(Name.QOS)
    assert "updateConfigurationModule" not in fake.methods()


async def test_a_test_that_is_not_marked_cannot_write(monkeypatch: pytest.MonkeyPatch):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    with live.writer_test(False):
        with pytest.raises(pytest.fail.Exception) as caught:
            async with live.restoring(site):
                pass
        assert str(caught.value) == live.NOT_MARKED
        with pytest.raises(live.WritesRefused) as refused:
            _ = await lab.client.update_configuration_module(qos_update(1))
        assert str(refused.value) == live.NOT_MARKED
    assert "updateConfigurationModule" not in fake.methods()


async def test_a_write_outside_a_block_is_refused(monkeypatch: pytest.MonkeyPatch):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    with pytest.raises(live.WritesRefused) as refused:
        _ = await lab.client.update_configuration_module(qos_update(1))
    assert str(refused.value) == live.OUTSIDE_RESTORING
    assert "updateConfigurationModule" not in fake.methods()


async def test_a_block_covers_its_own_module_and_no_other(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    async with live.restoring(site):
        await raise_limit(site, 2)
        assert fake.data(1)["serviceRateLimit"]["value"] == 2
        # Module 2 is the profile's: no block is open on it.
        with pytest.raises(live.WritesRefused) as refused:
            _ = await lab.client.update_configuration_module(qos_update(2))
        assert str(refused.value) == (
            "an update of a module that no open restoring block covers; "
            "nothing was sent; open `restoring(site)` on the module's own site "
            "and write inside it"
        )
    assert fake.data(1) == QOS and fake.data(2) == QOS
    assert live.run_of(lab.client).open == []


async def test_an_insert_is_covered_by_the_block_of_its_configuration_and_name(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    fake.add_edge(7, 12, 20, description=REPLACEABLE)  # no QoS module of its own
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[2], Name.QOS)
    run = live.run_of(lab.client)
    elsewhere = InsertQosConfigurationModule(
        configuration_id=11, data=copy.deepcopy(QOS)
    )
    async with live.restoring(site) as original:
        assert original is None
        with pytest.raises(live.WritesRefused) as refused:
            _ = await lab.client.insert_configuration_module(elsewhere)
        assert str(refused.value) == (
            "an insert of a QOS module that no open restoring block covers; "
            "nothing was sent; open `restoring(site)` on the module's own site "
            "and write inside it"
        )
        await live.insert_module(site, copy.deepcopy(QOS), None)
        (added,) = set(fake.store.rows) - {1, 2, 3}
        assert set(run.inserted) == {added}
        # The module the run inserted is covered by the block.
        await raise_limit(site, 5)
        assert fake.data(added)["serviceRateLimit"]["value"] == 5
    # Leaving the block deleted it again.
    assert set(fake.store.rows) == {1, 2, 3}
    assert run.inserted == {}


async def test_a_refused_write_carries_its_codes_and_not_the_names_in_its_text(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    fake.refused.add(1)
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    async with live.restoring(site):
        with pytest.raises(VcoOperationError) as caught:
            await raise_limit(site, 2)
    assert live.refusal_codes(caught.value) == "DS00000001 lan.networks"
    assert "secret-lab-name" not in str(caught.value)
    # The Orchestrator's own error, which names the entity, is not in the chain.
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


async def test_a_restore_that_works_leaves_no_backup(
    backups: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    async with live.restoring(site):
        assert len(list(backups.iterdir())) == 1  # taken before the test writes
        await raise_limit(site, 2)
        assert fake.data(2)["serviceRateLimit"]["value"] == 2
    assert fake.data(2) == QOS
    assert list(backups.iterdir()) == []


async def test_a_restore_that_fails_names_the_backup_and_never_the_data(
    backups: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    fake = lab_fake()
    fake.data(2)["segments"] = [{"segment": {"name": "secret-lab-name"}}]
    original = copy.deepcopy(fake.data(2))
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    with pytest.raises(pytest.fail.Exception) as caught:
        async with live.restoring(site):
            await raise_limit(site, 2)
            fake.refused.add(2)  # the write back is refused
    message = str(caught.value)
    (backup,) = backups.iterdir()
    assert message.endswith(f"; the original QOS module is saved at {backup}")
    assert "secret-lab-name" not in message
    assert json.loads(backup.read_text())["data"] == original


async def test_a_refused_delete_and_a_refused_effective_read_carry_codes_alone(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    fake.edges[5]["description"] = REPLACEABLE
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    fake.refused_methods = {
        "configuration/deleteConfigurationModule",
        "edge/getEdgeConfigurationModules",
    }
    async with live.restoring(site):
        with pytest.raises(VcoOperationError) as delete:
            await live.delete_module(site, 1)
    with pytest.raises(VcoOperationError) as effective:
        _ = await live.effective_data(lab.client, 5, Name.QOS, None)
    for caught in (delete, effective):
        assert "secret-lab-name" not in str(caught.value)
        assert "DS00000001" in str(caught.value)
    assert 1 in fake.store.rows


async def test_write_module_outside_a_block_is_refused(monkeypatch: pytest.MonkeyPatch):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    module = await lab.edge_site(lab.layouts[0], Name.QOS).read_present()
    data = copy.deepcopy(QOS)
    data["serviceRateLimit"]["value"] = 2
    with pytest.raises(live.WritesRefused) as refused:
        _ = await lab.client.write_module(module, plan_module(module, data))
    assert str(refused.value) == live.OUTSIDE_RESTORING
    assert "updateConfigurationModule" not in fake.methods()
    assert fake.data(1) == QOS


async def test_write_module_inside_a_block_writes_the_module_it_covers(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    async with live.restoring(site):
        module = await site.read_present()
        data = copy.deepcopy(QOS)
        data["serviceRateLimit"]["value"] = 2
        _ = await lab.client.write_module(module, plan_module(module, data))
        assert fake.data(1)["serviceRateLimit"]["value"] == 2
    assert fake.data(1) == QOS


async def test_a_delete_outside_a_block_or_of_an_uncovered_module_is_refused(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    fake.edges[5]["description"] = REPLACEABLE
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    elsewhere = lab.profile_site(20, Name.QOS)
    with pytest.raises(live.WritesRefused) as outside:
        await live.delete_module(site, 1)
    assert str(outside.value) == live.OUTSIDE_RESTORING
    async with live.restoring(site):
        # A module of another configuration, and a module of this one the block did not find.
        with pytest.raises(live.WritesRefused) as other_site:
            await live.delete_module(elsewhere, 2)
        with pytest.raises(live.WritesRefused) as other_module:
            await live.delete_module(site, 3)
    for refused in (other_site, other_module):
        assert str(refused.value).endswith(
            "that no open restoring block covers; nothing was sent; open "
            "`restoring(site)` on the module's own site and write inside it"
        )
        assert str(refused.value).startswith("a delete of a")
    assert "deleteConfigurationModule" not in fake.methods()
    assert set(fake.store.rows) == {1, 2, 3}


async def test_a_module_a_block_inserted_is_not_covered_once_the_block_is_closed(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    fake.edges[5]["description"] = REPLACEABLE
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    run = live.run_of(lab.client)
    async with live.restoring(site):
        # A second QoS module in the configuration the block is on: the block does not delete it.
        await live.insert_module(site, copy.deepcopy(QOS), None)
        (added,) = set(fake.store.rows) - {1, 2, 3}
        assert set(run.inserted) == {added}
    assert run.inserted == {}
    async with live.restoring(site):
        with pytest.raises(live.WritesRefused, match="an update of a module"):
            _ = await lab.client.update_configuration_module(qos_update(added))


class WriteCall:
    """A write the client sends over the portal, and how a test might send it."""

    def __init__(self, name: str, send: Callable[[VcoClient], Any]) -> None:
        self.name: str = name
        self.send: Callable[[VcoClient], Any] = send


WRITE_CALLS = [
    WriteCall(
        "portal",
        lambda client: client.portal(
            "edge/deleteEdge", DeleteEdge(id_=5), RowsModifiedConfirmation
        ),
    ),
    WriteCall(
        "portal_list",
        lambda client: client.portal_list(
            "edge/deleteEdge", DeleteEdge(id_=5), RowsModifiedConfirmation
        ),
    ),
    WriteCall(
        "portal_paginated_list",
        lambda client: client.portal_paginated_list(
            "edge/deleteEdge", GetEnterpriseEdges(), RowsModifiedConfirmation
        ),
    ),
    WriteCall(
        "portal_paginated",
        lambda client: anext(
            client.portal_paginated(
                "edge/deleteEdge", GetEnterpriseEdges(), RowsModifiedConfirmation
            )
        ),
    ),
    WriteCall("delete_edge", lambda client: client.delete_edge(DeleteEdge(id_=5))),
    WriteCall(
        "a module update sent by hand",
        lambda client: client.portal(
            "configuration/updateConfigurationModule",
            qos_update(1),
            RowsModifiedConfirmation,
        ),
    ),
    WriteCall(
        "a module delete sent by hand",
        lambda client: client.portal(
            "configuration/deleteConfigurationModule",
            live.DeleteConfigurationModule(id_=1),
            RowsModifiedConfirmation,
        ),
    ),
]


@pytest.mark.parametrize("call", WRITE_CALLS, ids=[c.name for c in WRITE_CALLS])
async def test_a_write_that_is_not_the_suites_own_is_refused_even_in_a_block(
    call: WriteCall, monkeypatch: pytest.MonkeyPatch
):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    async with live.restoring(site):
        fake.requests.clear()
        with pytest.raises(live.WritesRefused) as refused:
            _ = await call.send(lab.client)
        sent = list(fake.requests)
    message = str(refused.value)
    assert re.match(r"a call to \w+/\w+, which is not a read", message)
    assert "nothing was sent" in message and "restoring block" in message
    assert HOST not in message and "test-token" not in message
    assert sent == []


async def test_a_read_passes_every_form_of_the_call(monkeypatch: pytest.MonkeyPatch):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    client = lab.client
    _ = await client.portal(
        "enterprise/getEnterprise", GetEnterprise(), live.EnterpriseId
    )
    _ = await client.portal_list(
        "enterprise/getEnterpriseConfigurations",
        GetEnterpriseConfigurations(),
        EnterpriseConfigurationProfile,
    )
    _ = await client.portal_paginated_list(
        "enterprise/getEnterpriseEdges", GetEnterpriseEdges(), GetEnterpriseEdgesItem
    )
    pages = [
        page
        async for page in client.portal_paginated(
            "enterprise/getEnterpriseEdges",
            GetEnterpriseEdges(),
            GetEnterpriseEdgesItem,
        )
    ]
    assert len(pages) == 1


@pytest.mark.parametrize(
    "method",
    [
        "enterprise/getEnterprise",
        "edge/getEdgeConfigurationStack",
        "event/getEnterpriseEventsList",
        "network/getNetworkGateways",
        "metrics/getEdgeAppMetrics",
        "enterprise/listSomething",
        "enterprise/isSomething",
    ],
)
def test_a_method_that_starts_with_a_read_verb_passes(method: str):
    live.check_call(method)


@pytest.mark.parametrize(
    "method",
    [
        "edge/deleteEdge",
        "edge/edgeProvision",
        "configuration/cloneEnterpriseTemplate",
        "enterprise/insertObjectGroup",
        "diagnosticBundle/updateDiagnosticBundle",
        "pki/renewEdgeCertificate",
        "enterprise/getter",  # a verb is followed by a capital
        "enterprise/isolateSomething",
        "configuration/updateConfigurationModule",
        "configuration/insertConfigurationModule",
        "configuration/deleteConfigurationModule",
    ],
)
def test_any_other_method_is_refused_outside_the_code_that_checked_it(method: str):
    with pytest.raises(live.WritesRefused, match="^a call to "):
        live.check_call(method)


def string_constants(function: ast.AST) -> list[str]:
    return [
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_every_method_the_read_only_live_tests_reach_passes_the_guard():
    """The methods named in the analytics and in each read operation of the client are reads."""
    source = pathlib.Path(pyvelo.__file__).parent
    method = re.compile(r"[a-zA-Z]+/[a-zA-Z]+")
    reached: set[str] = set()
    for path in (source / "analytics").glob("*.py"):
        reached.update(
            text
            for text in string_constants(ast.parse(path.read_text()))
            if method.fullmatch(text)
        )
    for path in (source / "client" / "operations").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and node.name.startswith(("get_", "list_", "is_")):
                reached.update(
                    text for text in string_constants(node) if method.fullmatch(text)
                )
    assert "enterprise/getEnterpriseEdges" in reached and len(reached) > 20
    for name in sorted(reached):
        live.check_call(name)


def test_marks_are_whole_words_of_a_description():
    assert live.marks_in("lab edge, pyvelo-it-target.") == {live.TARGET_MARK}
    assert live.marks_in(REPLACEABLE) == {live.TARGET_MARK, live.REPLACE_MODULES_MARK}
    assert live.marks_in("pyvelo-it-targets pyvelo-it-0123456789") == frozenset()
    assert live.marks_in(None) == frozenset()


def test_a_profile_with_edges_needs_the_second_mark():
    entry = live.ProfileEntry
    both = frozenset({live.TARGET_MARK, live.WITH_EDGES_MARK})
    assert entry(1, frozenset({live.TARGET_MARK}), 0).target
    assert not entry(1, frozenset({live.TARGET_MARK}), 3).target
    assert not entry(
        1, frozenset({live.TARGET_MARK}), None
    ).target  # not told: as if it had
    assert entry(1, both, 3).target
    assert not entry(1, frozenset({live.WITH_EDGES_MARK}), 0).target
    assert not entry(1, frozenset(), 0).target


async def test_discovery_reads_the_marks(monkeypatch: pytest.MonkeyPatch):
    fake = lab_fake()
    fake.add_profile(21, description="pyvelo-it-target", edge_count=2)
    fake.add_edge(7, 12, 20, description=REPLACEABLE)
    lab = await opened(fake, monkeypatch)
    assert [(x.edge_id, x.target, x.replace_modules) for x in lab.layouts] == [
        (5, True, False),
        (6, False, False),
        (7, True, True),
    ]
    assert [x.edge_id for x in lab.targets] == [5, 7]
    assert lab.profile_ids == [20, 21]
    assert [p.target for p in lab.profiles] == [True, False]
    assert ("enterprise/getEnterpriseConfigurations", {"with": ["edgeCount"]}) in (
        fake.requests
    )
    assert lab.profile_site(20, Name.QOS).target
    assert not lab.profile_site(21, Name.QOS).target
    assert not lab.edge_site(lab.layouts[1], Name.QOS).target
    assert lab.edge_site(lab.layouts[2], Name.QOS).replace_modules


async def test_only_marked_edges_are_picked(monkeypatch: pytest.MonkeyPatch):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    assert [x.edge_id for x in lab.edges_with(Name.QOS)] == [5]
    assert (await lab.pick_writable_edge(Name.QOS)).edge_id == 5
    # An unmarked edge given to first_writable is passed over.
    assert await lab.first_writable([lab.layouts[1]], Name.QOS) is None
    assert fake.store.rows[3]["version"] == "v1"  # edge 6 was never written
    with pytest.raises(pytest.skip.Exception) as skipped:
        _ = await lab.pick_writable_edge(Name.QOS, replace_modules=True)
    assert str(skipped.value) == live.NO_EDGE_FOR_MODULES
    with pytest.raises(pytest.skip.Exception) as skipped:
        _ = await lab.pick_writable_edge(Name.FIREWALL, what="with a rule")
    assert str(skipped.value) == "no marked edge has a firewall module with a rule"


async def test_edges_lacking_a_module_are_those_marked_for_modules(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    fake.add_edge(7, 12, 20, description=REPLACEABLE)
    fake.add_edge(8, 13, 20, description=MARKED)
    lab = await opened(fake, monkeypatch)
    assert [x.edge_id for x in lab.edges_lacking(Name.QOS)] == [7]
    assert [x.edge_id for x in lab.edges_lacking(Name.FIREWALL)] == [7]


async def test_with_no_mark_every_pick_skips_and_says_how(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = FakeLab()
    fake.add_profile(20, description="the default profile")
    fake.module(2, 20, "QOS", QOS)
    fake.add_edge(5, 10, 20)
    fake.module(1, 10, "QOS", QOS)
    lab = await opened(fake, monkeypatch)
    with pytest.raises(pytest.skip.Exception) as skipped:
        _ = await lab.pick_writable_edge(Name.QOS)
    assert str(skipped.value) == (
        "no edge is marked for this suite: put pyvelo-it-target in the "
        "description of an edge it may write to"
    )
    with pytest.raises(pytest.skip.Exception) as skipped:
        _ = await lab.pick_writable_profile(Name.QOS)
    assert str(skipped.value) == (
        "no profile is marked for this suite: put pyvelo-it-target in the "
        "description of a profile it may write to, and pyvelo-it-with-edges as "
        "well if edges are assigned to it"
    )
    assert "updateConfigurationModule" not in fake.methods()


async def test_a_marked_profile_with_edges_is_passed_over(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    fake.profiles[20]["edgeCount"] = 2
    lab = await opened(fake, monkeypatch)
    with pytest.raises(pytest.skip.Exception) as skipped:
        _ = await lab.pick_writable_profile(Name.QOS)
    assert str(skipped.value) == live.NO_PROFILE_MARKED
    fake.profiles[20]["description"] = "pyvelo-it-target pyvelo-it-with-edges"
    lab = await opened(fake, monkeypatch)
    assert await lab.pick_writable_profile(Name.QOS) == 20


async def test_a_target_that_is_not_marked_is_refused(monkeypatch: pytest.MonkeyPatch):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    unmarked = lab.edge_site(lab.layouts[1], Name.QOS)
    fake.requests.clear()
    with pytest.raises(pytest.fail.Exception) as caught:
        async with live.restoring(unmarked):
            pass
    assert str(caught.value) == live.NOT_A_TARGET
    assert str(caught.value).startswith(
        "the test tried to write to an edge or profile that is not marked "
        "pyvelo-it-target; nothing was written; "
    )
    assert fake.requests == []


async def test_an_insert_and_a_delete_need_the_mark_for_modules(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)  # edge 5: marked, not for modules
    insert = InsertQosConfigurationModule(configuration_id=10, data=copy.deepcopy(QOS))
    remedy = (
        "put pyvelo-it-replace-modules beside pyvelo-it-target in the description "
        "of an edge the suite may delete and insert modules of"
    )
    async with live.restoring(site):
        with pytest.raises(live.WritesRefused) as refused:
            await live.delete_module(site, 1)
        assert str(refused.value) == (
            "a delete of a QOS module of an edge that is not marked "
            f"pyvelo-it-replace-modules; nothing was sent; {remedy}"
        )
        with pytest.raises(live.WritesRefused) as refused:
            _ = await lab.client.insert_configuration_module(insert)
        assert str(refused.value) == (
            "an insert of a QOS module of an edge that is not marked "
            f"pyvelo-it-replace-modules; nothing was sent; {remedy}"
        )
    assert set(fake.store.rows) == {1, 2, 3}
    assert "deleteConfigurationModule" not in fake.methods()


async def test_a_module_that_takes_its_own_data_is_writable(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    assert await live.own_data_refusal(site) is None
    assert await live.accepts_a_write(site)


async def test_a_validation_refusal_is_its_codes(monkeypatch: pytest.MonkeyPatch):
    fake = lab_fake()
    fake.refused.add(1)
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    assert await live.own_data_refusal(site) == "DS00000001 lan.networks"
    assert not await live.accepts_a_write(site)


async def test_any_other_error_of_the_write_is_raised(monkeypatch: pytest.MonkeyPatch):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    real = fake.store.handle

    def answer_nothing_useful(method: str, params: dict[str, Any]) -> object:
        if method.endswith("updateConfigurationModule"):
            return {}  # as a client that no longer reads the answer would see it
        return real(method, params)

    monkeypatch.setattr(fake.store, "handle", answer_nothing_useful)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    with pytest.raises(VcoOperationError) as caught:
        _ = await live.accepts_a_write(site)
    assert not isinstance(caught.value, live.DataRefused)
    with pytest.raises(VcoOperationError):
        _ = await lab.pick_writable_edge(Name.QOS)


async def test_marked_targets_that_all_refuse_fail_the_test(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    fake.refused.update({1, 2})
    lab = await opened(fake, monkeypatch)
    with pytest.raises(pytest.fail.Exception) as failed:
        _ = await lab.pick_writable_edge(Name.QOS)
    assert str(failed.value) == (
        "none of the 1 marked edge(s) with a suitable QOS module takes a write "
        "of its own data (DS00000001 lan.networks): the modules are not valid "
        "as they stand, or the update path is broken"
    )
    assert "secret-lab-name" not in str(failed.value)
    with pytest.raises(pytest.fail.Exception, match="^none of the 1 marked profile"):
        _ = await lab.pick_writable_profile(Name.QOS)


async def test_the_next_marked_edge_is_tried(monkeypatch: pytest.MonkeyPatch):
    fake = lab_fake()
    fake.add_edge(7, 12, 20, description=MARKED)
    fake.module(4, 12, "QOS", QOS)
    fake.refused.add(1)
    lab = await opened(fake, monkeypatch)
    assert (await lab.pick_writable_edge(Name.QOS)).edge_id == 7
    first = await lab.first_writable(lab.targets, Name.QOS)
    assert first is not None and first.edge_id == 7


async def test_a_refusal_the_orchestrator_states_is_a_data_refusal(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    fake.refused.add(1)
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    async with live.restoring(site):
        with pytest.raises(live.DataRefused) as caught:
            await raise_limit(site, 2)
    assert caught.value.error == "DS00000001 lan.networks"
    assert "secret-lab-name" not in str(caught.value)
    assert live.refusal_codes(caught.value) == "DS00000001 lan.networks"


async def test_edges_that_all_refuse_fail_a_pick_among_given_layouts(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    fake.refused.add(1)
    lab = await opened(fake, monkeypatch)
    assert await lab.first_writable_or_fail([], Name.QOS) is None
    with pytest.raises(pytest.fail.Exception) as failed:
        _ = await lab.first_writable_or_fail(lab.targets, Name.QOS)
    assert str(failed.value).startswith("none of the 1 marked edge(s) with a suitable")
    assert "secret-lab-name" not in str(failed.value)
    fake.refused.clear()
    taken = await lab.first_writable_or_fail(lab.targets, Name.QOS)
    assert taken is not None and taken.edge_id == 5


async def test_backups_left_are_listed_once(
    backups: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    lines: list[str] = []
    async with live.restoring(lab.edge_site(lab.layouts[0], Name.QOS)):
        pass
    live.report_leftover_backups(lines.append)
    assert lines == []  # a module that was put back leaves nothing to list

    site = lab.profile_site(20, Name.QOS)
    with pytest.raises(pytest.fail.Exception):
        async with live.restoring(site):
            await raise_limit(site, 2)
            fake.refused.add(2)  # the write back is refused
    (backup,) = backups.iterdir()
    live.report_leftover_backups(lines.append)
    assert lines == [
        (
            "live suite: 1 module(s) were not put back; the original of each is "
            "saved at:"
        ),
        f"  {backup}  (QOS)",
    ]
    live.report_leftover_backups(lines.append)
    assert len(lines) == 2


async def test_a_change_by_another_writer_is_not_written_over(
    backups: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    with pytest.raises(pytest.fail.Exception) as caught:
        async with live.restoring(site):
            await raise_limit(site, 2)
            # Someone else, in the UI, while the test runs:
            fake.store.other_writer(
                2, lambda row: row["data"].update(addedByAnAdmin={"keep": "me"})
            )
    (backup,) = backups.iterdir()
    assert str(caught.value) == (
        "restore: the QOS module changed outside this run after its last write "
        "(+ addedByAnAdmin); it was left as it is, with this run's changes; "
        "check who else changed the QOS module, and put the original back by "
        f"hand if it is wanted; the original QOS module is saved at {backup}"
    )
    assert fake.data(2)["addedByAnAdmin"] == {"keep": "me"}
    assert fake.data(2)["serviceRateLimit"]["value"] == 2
    assert fake.methods().count("updateConfigurationModule") == 1


async def test_a_change_before_the_run_wrote_is_seen_too(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    with pytest.raises(pytest.fail.Exception, match="changed outside this run"):
        async with live.restoring(site):
            fake.store.other_writer(2, lambda row: row["data"].update(added=1))
    assert fake.data(2)["added"] == 1
    assert "updateConfigurationModule" not in fake.methods()


async def test_a_change_between_two_blocks_is_not_blamed_on_the_second(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    async with live.restoring(site):
        await raise_limit(site, 2)
    fake.store.other_writer(2, lambda row: row["data"].update(added=1))
    async with live.restoring(site):
        await raise_limit(site, 3)
    assert fake.data(2) == {**QOS, "added": 1}


async def test_without_the_row_of_the_last_write_the_original_goes_back(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    fake.store.return_data = False  # it answers a write with a row count
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    async with live.restoring(site):
        await raise_limit(site, 2)
    assert fake.data(2) == QOS


async def test_after_a_write_whose_answer_was_lost_the_original_goes_back(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    async with live.restoring(site):
        fake.store.lose_answers = {1}  # the store's first write: stored, not answered
        with pytest.raises(httpx.ReadTimeout):
            await raise_limit(site, 2)
        assert fake.data(2)["serviceRateLimit"]["value"] == 2
    assert fake.data(2) == QOS


async def test_a_module_the_run_did_not_insert_is_not_deleted(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    fake.add_edge(7, 12, 20, description=REPLACEABLE)
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[2], Name.QOS)
    with pytest.raises(pytest.fail.Exception) as caught:
        async with live.restoring(site) as original:
            assert original is None
            # The test inserts nothing; an administrator creates the module meanwhile.
            fake.module(77, 12, "QOS", QOS)
    assert str(caught.value) == (
        "restore: a QOS module this run did not insert is there now; it was "
        "left as it is; check who created it, and delete it by hand if the lab "
        "should not have it"
    )
    assert 77 in fake.store.rows
    assert "deleteConfigurationModule" not in fake.methods()


async def test_a_module_the_run_deleted_is_inserted_again(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    fake.edges[5]["description"] = REPLACEABLE
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    async with live.restoring(site):
        await live.delete_module(site, 1)
        assert await site.read() is None
    back = await site.read_present()
    assert back.id_ != 1 and back.raw_data == QOS


async def test_a_module_someone_else_deleted_is_not_put_back(
    backups: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    fake = lab_fake()
    fake.edges[5]["description"] = REPLACEABLE
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    with pytest.raises(pytest.fail.Exception) as caught:
        async with live.restoring(site):
            del fake.store.rows[1]
    (backup,) = backups.iterdir()
    assert str(caught.value) == (
        "restore: the QOS module is gone and this run did not delete it; it "
        "was not put back; check who deleted it, and insert it again from the "
        f"saved original if it is wanted; the original QOS module is saved at {backup}"
    )
    assert "insertConfigurationModule" not in fake.methods()


async def test_an_interrupt_during_the_restore_is_held_until_it_is_done(
    backups: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    seen: list[str] = []

    def interrupt(_method: str) -> None:
        signal.raise_signal(signal.SIGINT)  # Ctrl-C, at each request of the restore
        assert seen == []

    previous = signal.signal(signal.SIGINT, lambda *_: seen.append("delivered"))
    try:
        async with live.restoring(site):
            await raise_limit(site, 99)
            fake.before = interrupt
        fake.before = None
        assert seen == ["delivered"]  # once, after the module was back
    finally:
        _ = signal.signal(signal.SIGINT, previous)
    assert fake.data(2) == QOS
    assert list(backups.iterdir()) == []


def test_interrupts_are_held_and_then_delivered(capsys: pytest.CaptureFixture[str]):
    seen: list[str] = []
    previous = signal.signal(signal.SIGINT, lambda *_: seen.append("delivered"))
    try:
        with live.interrupts_held():
            signal.raise_signal(signal.SIGINT)
            signal.raise_signal(signal.SIGINT)
            assert seen == []
        assert seen == ["delivered"]
        assert capsys.readouterr().err == (
            "live suite: holding Ctrl-C until the module is put back\n"
        )  # one line for two interrupts
        with live.interrupts_held():
            pass
        assert seen == ["delivered"]
        assert capsys.readouterr().err == ""
    finally:
        _ = signal.signal(signal.SIGINT, previous)


async def test_another_module_in_place_of_the_one_found_is_left_alone(
    backups: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    with pytest.raises(pytest.fail.Exception) as caught:
        async with live.restoring(site):
            # Someone replaces the module, in the UI, while the test runs:
            del fake.store.rows[2]
            fake.module(77, 20, "QOS", {**QOS, "added": 1})
    (backup,) = backups.iterdir()
    assert str(caught.value) == (
        "restore: another QOS module is there than the one the test began "
        "with, and this run did not insert it; it was left as it is; check who "
        "replaced the module, and put the original back by hand if it is "
        f"wanted; the original QOS module is saved at {backup}"
    )
    assert fake.data(77)["added"] == 1
    assert "updateConfigurationModule" not in fake.methods()


async def test_a_write_refused_for_its_data_leaves_what_the_run_last_saw(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    with pytest.raises(pytest.fail.Exception, match="changed outside this run"):
        async with live.restoring(site):
            await raise_limit(site, 2)
            fake.store.other_writer(2, lambda row: row["data"].update(added=1))
            fake.refused.add(2)  # a stated validation refusal stores nothing
            with pytest.raises(live.DataRefused):
                await raise_limit(site, 3)
            fake.refused.discard(2)
    assert fake.data(2)["added"] == 1
    assert fake.data(2)["serviceRateLimit"]["value"] == 2
    assert fake.methods().count("updateConfigurationModule") == 2  # none after it


async def test_a_write_that_failed_before_it_was_sent_leaves_what_the_run_last_saw(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    with pytest.raises(pytest.fail.Exception, match="changed outside this run"):
        async with live.restoring(site):
            await raise_limit(site, 2)
            fake.store.other_writer(2, lambda row: row["data"].update(added=1))
            module = await site.read_present()
            with monkeypatch.context() as patched:

                async def unsendable(*_args: object, **_kwargs: object) -> None:
                    raise RuntimeError("failed before anything was sent")

                patched.setattr(site.client, "portal", unsendable)
                with pytest.raises(RuntimeError):
                    await live.write_data(site, module, module.raw_data)
    assert fake.data(2)["added"] == 1
    assert fake.methods().count("updateConfigurationModule") == 1


async def test_a_write_cancelled_after_it_was_stored_is_not_blamed_on_anyone(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    stores = ModuleStore.__call__

    def stores_then_cancels(
        self: ModuleStore, request: httpx.Request
    ) -> httpx.Response:
        answer = stores(self, request)
        if json.loads(request.content)["method"].endswith("updateConfigurationModule"):
            raise asyncio.CancelledError
        return answer

    async with live.restoring(site):
        with monkeypatch.context() as patched:
            patched.setattr(ModuleStore, "__call__", stores_then_cancels)
            with pytest.raises(asyncio.CancelledError):
                await raise_limit(site, 2)
        assert fake.data(2)["serviceRateLimit"]["value"] == 2
    assert fake.data(2) == QOS


async def test_a_delete_of_a_module_no_block_covers_names_its_type(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = lab_fake()
    fake.edges[5]["description"] = REPLACEABLE
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    async with live.restoring(site):
        with pytest.raises(live.WritesRefused, match="^a delete of a QOS module "):
            await live.delete_module(site, 3)
    assert 3 in fake.store.rows
