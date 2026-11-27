"""Offline checks of the support code of the live save tests.

The code is tests/support/live_support.py.

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
import uuid
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
from pyvelo.client.exceptions import PRECONDITION_FAILED, LossyFieldError, WriteAnswer
from pyvelo.client.models.configuration.qos.root import ServiceRateLimit
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas import edge as edge_schemas
from pyvelo.client.schemas import enterprise as enterprise_schemas
from pyvelo.client.schemas.configuration import (
    ConfigurationModuleUpdate,
    DeleteConfigurationModule,
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
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.core import PatchError, RowsModifiedConfirmation
from pyvelo.core.layers import Edge
from pyvelo.core.rebase import Key, Path, RawChange
from pyvelo.core.response_check import PlannedChange, ServerChange
from pyvelo.testing import (
    ModuleStore,
    make_jsonrpc_response,
    make_module_json,
    make_transport,
)
from refs_helpers import (
    DNS_A,
    SEG0,
    SEG0_OBJECT,
    SEGMENT,
    dns,
    segment_entry,
    segment_row,
    service_row,
)

DS = Name.DEVICE_SETTINGS


def segment(segment_id: int, **extra: object) -> dict[str, Any]:
    return {"segment": {"segmentId": segment_id, "name": f"s{segment_id}"}, **extra}


class TestSettle:
    """``routeMapsVersion`` is accepted drift only where the write added it."""

    def test_a_key_the_write_added_to_an_entry_is_dropped(self) -> None:
        before = {"segments": [segment(0)]}
        after = {"segments": [segment(0, routeMapsVersion="1")]}
        assert live.settle(DS, before, after) == before
        assert live.changes_between(DS, before, after) == []

    def test_a_key_the_entry_already_had_is_compared(self) -> None:
        before = {"segments": [segment(0, routeMapsVersion="1")]}
        after = {"segments": [segment(0, routeMapsVersion="2")]}
        assert live.settle(DS, before, after) == after
        assert len(live.changes_between(DS, before, after)) == 1

    def test_a_key_on_a_new_entry_is_dropped(self) -> None:
        before = {"segments": [segment(0)]}
        after = {"segments": [segment(0), segment(2, routeMapsVersion="1")]}
        assert live.settle(DS, before, after) == {"segments": [segment(0), segment(2)]}

    def test_only_device_settings_segments_are_settled(self) -> None:
        before = {"segments": [segment(0)]}
        after = {"segments": [segment(0, routeMapsVersion="1")]}
        assert live.settle(Name.FIREWALL, before, after) == after
        nested = {"segments": [segment(0)], "other": {"routeMapsVersion": "1"}}
        assert live.settle(DS, before, nested) == nested

    def test_the_inputs_are_not_changed(self) -> None:
        after = {"segments": [segment(0, routeMapsVersion="1")]}
        _ = live.settle(DS, {"segments": [segment(0)]}, after)
        assert "routeMapsVersion" in after["segments"][0]


def test_has_key_looks_at_any_depth() -> None:
    assert live.has_key({"a": [{"b": {"k": 1}}]}, "k")
    assert not live.has_key({"a": [{"b": {"j": 1}}]}, "k")
    assert not live.has_key([1, "k", None], "k")


def test_a_redacted_path_shows_no_key_but_a_synthetic_one() -> None:
    path = Path(
        ("segments", Key("Lab Segment"), "outbound", Key(f"{live.SYNTHETIC_PREFIX}x"))
    )
    assert live.redact(path) == f"segments[*].outbound[{live.SYNTHETIC_PREFIX}x]"


def test_an_unreadable_edge_is_reported_by_error_type_alone() -> None:
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

    def test_a_backup_is_a_private_file_with_the_data(
        self, tmp_path: pathlib.Path
    ) -> None:
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

    def test_only_a_qos_backup_holds_refs_and_never_a_client_connector_one(
        self,
    ) -> None:
        qos = live.save_backup(self.site(Name.QOS), self.original())
        assert json.loads(qos.read_text())["refs"] == {"r": {"id": 1}}
        ds = live.save_backup(self.site(Name.DEVICE_SETTINGS), self.original())
        assert json.loads(ds.read_text())["refs"] is None

    def test_the_file_name_holds_no_lab_identifier(self) -> None:
        path = live.save_backup(self.site(), self.original())
        assert re.fullmatch(r"[0-9a-f]{32}-firewall\.json", path.name)

    def test_a_backup_directory_inside_the_repository_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
    """Backups go to a temporary directory and the opt-in is unset.

    The test is a writer.
    """
    monkeypatch.setenv(live.BACKUP_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(live, "_backup_dir", None)
    monkeypatch.setattr(live, "_LEFT", {})
    monkeypatch.delenv(live.WRITE_HOST_ENV, raising=False)
    monkeypatch.delenv(live.WRITE_ENTERPRISE_ENV, raising=False)
    with live.writer_test(marked=True):
        yield tmp_path


class TestNoOverrideFlagCheck:
    CLEAN: ClassVar[dict[str, Any]] = {"segments": [segment(0)]}
    STRAY: ClassVar[dict[str, Any]] = {"segments": [segment(0, outboundOverride=True)]}

    def test_clean_data_passes(self) -> None:
        live.check_no_override_flag("x", self.CLEAN, self.CLEAN, self.CLEAN)
        live.check_no_override_flag("x", None, self.CLEAN, self.CLEAN)

    def test_a_key_the_module_did_not_have_is_reported_as_added_by_the_write(
        self,
    ) -> None:
        with pytest.raises(pytest.fail.Exception) as caught:
            live.check_no_override_flag("x", self.CLEAN, self.CLEAN, self.STRAY)
        message = str(caught.value)
        assert "read back carries" in message
        assert "write added it" in message

    def test_a_key_the_module_already_had_is_reported_as_pre_existing(self) -> None:
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


def test_where_redacts_paths_and_marks_refs() -> None:
    changes = [
        planned(Path(("segments", SECRET, "outbound")), 1, 2, 1),
        PlannedChange(Path(("a", Key(5))), 1, 2, 1, part="refs"),
    ]
    assert live.where(changes) == "segments[*].outbound, refs a[*]"
    assert live.where([]) == "none"


def test_stored_otherwise_goes_to_the_deepest_difference() -> None:
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


def test_outcome_names_the_class_and_redacted_paths_only() -> None:
    error = WriteNotAppliedError(
        1,
        "v1",
        module_name="firewall",
        plan=None,  # pyright: ignore[reportArgumentType]
        module=None,  # pyright: ignore[reportArgumentType]
        lost_edits=[
            planned(Path(("segments", SECRET, "x")), "lab-a", "lab-b", "lab-c")
        ],
        applied=[
            planned(
                Path(("firewall_enabled",)), as_read=True, intended=False, stored=False
            )
        ],
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


def test_same_value_ignores_the_keys_the_orchestrator_assigns() -> None:
    assert live.same_value({"name": "a", "ruleLogicalId": "x"}, {"name": "a"})
    assert not live.same_value({"name": "a"}, {"name": "b"})
    assert live.same_value(3, 3)


def test_value_at_follows_names_only() -> None:
    document = {"qoe": {"voice": {"latency": {"red": 5}}}, "links": [{"a": 1}]}
    assert live.value_at(document, Path(("qoe", "voice", "latency", "red"))) == 5
    assert live.value_at(document, Path(("qoe", "video"))) is MISSING
    assert live.value_at(document, Path(("links", Key("x")))) is MISSING


def test_the_checks_fail_with_redacted_paths() -> None:
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


def test_check_as_read_compares_version_and_raw_data() -> None:
    def module(version: str, **data: object) -> FirewallConfigurationModule[Edge]:
        return FirewallConfigurationModule[Edge].model_validate(
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


def firewall_disabled() -> FirewallConfigurationModule[Edge]:
    module = FirewallConfigurationModule[Edge].model_validate(
        make_module_json("firewall", copy.deepcopy(FIREWALL), configuration_id=10)
    )
    module.data.firewall_enabled.set_local(False)
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


async def test_refusal_form_of_a_stale_write_whose_module_was_read() -> None:
    form = live.refusal_form
    precondition = await write_error(stale=True, conditional_check=True)
    assert isinstance(precondition, ConfigConflictError)
    assert precondition.stated
    assert form(precondition) == "refused with the precondition error"
    zero_rows = await write_error(stale=True, return_data=False)
    assert isinstance(zero_rows, ConfigConflictError)
    assert zero_rows.stated
    assert form(zero_rows) == "refused with a row count of zero"
    inferred = await write_error(stale=True)
    assert isinstance(inferred, ConfigConflictError)
    assert not inferred.stated
    assert form(inferred) == "refused with the module row as stored"


async def test_refusal_form_of_a_write_stored_otherwise_than_sent_is_no_refusal() -> (
    None
):
    error = await write_error(stale=True, clash=True)
    assert isinstance(error, WriteNotAppliedError)
    assert live.refusal_form(error) == "not refused: stored otherwise than sent"


async def test_refusal_form_of_a_refusal_whose_read_back_did_not_parse() -> None:
    """Pydantic's ``ValidationError`` carries no ``__context__``.

    So the refusal is not in this chain: the clause says only ``refused``.
    """

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
    assert isinstance(error, WriteOutcomeUnknownError)
    assert error.answer == "refused"
    assert isinstance(error.__cause__, ValidationError)
    assert live.refusal_form(error) == (
        "refused, and the module as stored could not be read"
    )


def test_refusal_form_walks_the_chain_to_the_refusal() -> None:
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


async def test_refusal_form_finds_the_refusal_a_read_that_found_no_module_was_raised_in() -> (  # noqa: E501 (a test's name)
    None
):
    unread = ", and the module as stored could not be read"
    error = await write_error(stale=True, conditional_check=True, read_back=False)
    assert live.refusal_form(error) == ("refused with the precondition error" + unread)


def unknown(
    answer: WriteAnswer,
    refusal: ConfigConflictError | None = None,
    detail: str = "its configuration does not hold the module",
) -> WriteOutcomeUnknownError:
    """The error of a write whose stored module could not be read.

    It is raised while handling *refusal*.
    """
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


def test_refusal_form_of_a_write_whose_module_could_not_be_read() -> None:
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


def test_outcome_of_a_write_whose_module_could_not_be_read() -> None:
    assert live.outcome(unknown("counted")) == (
        "WriteOutcomeUnknownError (its configuration does not hold the module; "
        "answer counted) on the firewall module: sent ~ firewall_enabled"
    )


def test_outcome_of_a_write_whose_module_could_not_be_read_shows_no_number() -> None:
    error = unknown("counted", detail="configuration 4711 does not hold module 8123")
    assert live.outcome(error) == (
        "WriteOutcomeUnknownError (configuration # does not hold module #; "
        "answer counted) on the firewall module: sent ~ firewall_enabled"
    )


def test_where_marks_the_order_of_a_list() -> None:
    path = Path(("segments", SECRET, "outbound"))
    order = PlannedChange(path, ["a", "b"], ["b", "a"], ["a", "b"], moves=())
    assert live.where([order]) == "order of segments[*].outbound"


class PassThrough:
    """A capture whose ``disabled`` changes nothing.

    So ``capsys`` sees what is printed past it.
    """

    def disabled(self) -> contextlib.AbstractContextManager[None]:
        return contextlib.nullcontext()


async def test_a_counted_write_that_was_not_read_back_is_not_a_refusal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    error = await write_error(stale=False, return_data=False, read_back=False)
    assert isinstance(error, WriteOutcomeUnknownError)
    assert error.answer == "counted"
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


class SaveBlockedError(Exception):
    """Shaped as ``SaveValidationError`` of ``pyvelo.config``.

    ``errors`` with a code each.
    """

    def __init__(self) -> None:
        self.errors: list[ValidatorError] = [ValidatorError("B2"), ValidatorError("A1")]
        super().__init__("secret-lab-name is wrong")


def test_a_validation_error_shows_its_types_and_no_input() -> None:
    assert live.failure_text(validation_error()) == (
        "ValidationError for Model: 1 error(s) of type int_parsing"
    )


def test_a_refusal_shows_its_codes() -> None:
    error = VcoOperationError(
        "configuration/getConfiguration",
        "validationError",
        data={"error": [{"code": "E1", "path": "p", "message": "secret-lab-name"}]},
    )
    assert live.failure_text(error) == (
        "VcoOperationError in configuration/getConfiguration: E1 p"
    )


def test_a_refusal_the_client_already_redacted_still_shows_its_codes() -> None:
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
        "DataRefusedError in configuration/updateConfigurationModule: E1 a.b[0]; E_2"
    )


def test_a_bracketed_name_in_a_refusal_is_not_read_as_a_code() -> None:
    named = "[HQ] Munich-Branch-7"
    in_text = VcoOperationError("edge/getEdge", f"no such edge {named}")
    in_message = VcoOperationError(
        "edge/getEdge",
        "validationError",
        data={"error": [{"code": "E1", "path": "a", "message": f"{named} clash"}]},
    )
    for error in (in_text, in_message, live._redacted(in_message)):  # pyright: ignore[reportPrivateUsage]
        for shown in (live.failure_text(error), live.refusal_codes(error)):
            assert shown is not None
            assert "HQ" not in shown
            assert "Munich" not in shown
        assert "HQ" not in str(live._redacted(error))  # pyright: ignore[reportPrivateUsage]
    assert live.refusal_codes(in_text) == "no error code"
    assert live.refusal_codes(in_message) == "E1 a"
    assert live.refusal_codes(live._redacted(in_message)) == "E1 a"  # pyright: ignore[reportPrivateUsage]


def test_a_name_in_brackets_in_a_refusal_path_is_hidden() -> None:
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


def test_an_http_error_shows_its_status() -> None:
    error = VcoHttpError(502, "<html>secret-lab-name</html>")
    assert live.failure_text(error) == "VcoHttpError: HTTP 502"


def test_a_refused_patch_shows_its_kind_and_where() -> None:
    path = Path(("segments", SECRET, "outbound"))
    error = PatchError("secret-lab-name is already in the list", path, "exists")
    assert live.failure_text(error) == "PatchError (exists) at segments[*].outbound"


def test_an_edit_a_save_cannot_write_shows_where() -> None:
    error = LossyFieldError("firewall", [Path(("segments", SECRET, "x"))])
    assert live.failure_text(error) == (
        "LossyFieldError in the firewall module at segments[*].x"
    )


def test_a_write_error_shows_its_outcome() -> None:
    error = ConfigConflictError(1, "v1", "the VCO refused the version")
    assert live.failure_text(error) == live.outcome(error)


def test_validator_errors_show_their_codes() -> None:
    assert live.failure_text(SaveBlockedError()) == (
        "SaveBlockedError: 2 error(s) with code A1, B2"
    )


def test_any_other_error_shows_its_class() -> None:
    assert live.failure_text(KeyError("secret-lab-name")) == "KeyError"
    assert live.failure_text(AssertionError("secret-lab-name")) == "AssertionError"


def test_notes_arguments_and_causes_are_never_shown() -> None:
    cause = ValueError("secret-lab-name in a cause")
    for error in (
        KeyError("secret-lab-name"),
        PatchError("secret-lab-name", Path(("x",)), "exists"),
        VcoHttpError(502, "secret-lab-name"),
        SaveBlockedError(),
    ):
        error.add_note("secret-lab-name in a note")
        error.__cause__ = cause
        error.__context__ = cause
        assert "secret-lab-name" not in str(live.failure_text(error))


def test_what_the_suite_words_itself_is_left() -> None:
    with pytest.raises(pytest.fail.Exception) as failed:
        pytest.fail("a message the test wrote", pytrace=False)
    with pytest.raises(pytest.skip.Exception) as skipped:
        pytest.skip("no target")
    assert live.failure_text(failed.value) is None
    assert live.failure_text(skipped.value) is None


def test_an_error_of_the_suite_is_its_own_message() -> None:
    error = live.LiveSuiteError("edge #3 does not load")
    assert live.failure_text(error) == "edge #3 does not load"


def test_an_unreadable_edge_is_an_error_of_the_suite() -> None:
    assert issubclass(live.LabUnreadableError, live.LiveSuiteError)


class FakeItem:
    """A test item: its marks, and the fixtures it uses."""

    def __init__(self, marks: tuple[str, ...], fixtures: tuple[str, ...]) -> None:
        self.marks: tuple[str, ...] = marks
        self.fixturenames: list[str] = list(fixtures)

    def get_closest_marker(self, name: str) -> object:
        return name if name in self.marks else None


class FakeReport:
    def __init__(self, *, failed: bool) -> None:
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
    report = FakeReport(failed=failed)
    call = pytest.CallInfo.from_call(body, when)
    live.redact_report(FakeItem(marks, fixtures), call, report)  # pyright: ignore[reportArgumentType]
    assert isinstance(report.longrepr, str)
    return report.longrepr


HERE = r" \(raised at test_live_support\.py:\d+\)"


def test_a_live_failure_is_its_text_and_where_it_was_raised() -> None:
    shown = redacted(raising(KeyError("secret-lab-name")))
    assert re.fullmatch("KeyError" + HERE, shown)


def test_the_line_is_the_one_that_raised() -> None:
    def body() -> None:
        frame = inspect.currentframe()
        assert frame is not None
        raised_at.append(frame.f_lineno + 1)
        raise KeyError("secret-lab-name")

    raised_at: list[int] = []
    shown = redacted(body)
    assert shown == f"KeyError (raised at test_live_support.py:{raised_at[0]})"


def test_a_failure_in_a_fixture_is_redacted_too() -> None:
    error = raising(live.LabUnreadableError("edge #3 does not load"))
    for when in ("setup", "teardown"):
        shown = redacted(error, when=when)
        assert re.fullmatch("edge #3 does not load" + HERE, shown)
        shown = redacted(raising(KeyError("secret-lab-name")), when=when)
        assert re.fullmatch("KeyError" + HERE, shown)


def test_the_place_is_the_last_frame_in_the_tests() -> None:
    # Raised inside pydantic: the report names the line of this file that called it.
    shown = redacted(lambda: Model.model_validate({"value": "secret-lab-value"}))
    text = r"ValidationError for Model: 1 error\(s\) of type int_parsing"
    assert re.fullmatch(text + HERE, shown)


def test_the_place_is_in_the_test_and_not_in_the_support_code() -> None:
    # Raised inside live_support, which is in a tests directory too: the report
    # names the line of this file that called it.
    shown = redacted(lambda: live.redact(1))  # pyright: ignore[reportArgumentType]
    assert re.fullmatch("AttributeError" + HERE, shown)


def test_an_error_of_the_suite_keeps_its_message() -> None:
    shown = redacted(raising(live.LiveSuiteError("edge #3 does not load")))
    assert re.fullmatch("edge #3 does not load" + HERE, shown)


def test_other_tests_and_worded_failures_are_left() -> None:
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
    """A fake Orchestrator for a live run.

    A ``ModuleStore``, plus the edges, profiles and enterprise it lists.

    It is an ``httpx`` handler, as the store is: a client built over it runs
    its own request building and parsing. Module updates, inserts, deletes,
    ``getConfiguration`` and the configuration stack of an edge go to the
    store; the listings and the enterprise are answered here, and so is the
    delete of a module where ``delete_answer`` says what to answer.
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
        """How ``enterprise/getEnterprise`` fails apart from a refusal: an HTTP status,
        no result, a result with no ID.
        """
        self.before: Callable[[str], None] | None = None
        """Called with the method of each request before it is answered."""
        self.delete_answer: dict[str, Any] | None = None
        """What the delete of a module is answered with, the module left where it is;
        with ``None`` the store deletes it and answers.
        """

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

    def _enterprise(self, params: dict[str, Any]) -> httpx.Response:
        """The answer to ``enterprise/getEnterprise``, or how it fails."""
        if self.enterprise_failure == "http":
            return httpx.Response(503, text="secret-lab-name is down")
        if self.enterprise_failure == "empty":
            return httpx.Response(200, json=make_jsonrpc_response())
        if self.enterprise_error:
            error = {"code": -32000, "message": "invalid enterprise context"}
            return httpx.Response(200, json=make_jsonrpc_response(error=error))
        result = (
            {}
            if self.enterprise_failure == "no_id"
            else {"id": params.get("enterpriseId", ENTERPRISE)}
        )
        return httpx.Response(200, json=make_jsonrpc_response(result=result))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] = json.loads(request.content)
        method: str = body["method"]
        params: dict[str, Any] = body.get("params") or {}
        self.requests.append((method, params))
        if self.before is not None:
            self.before(method)
        result: object
        update = method == "configuration/updateConfigurationModule"
        if method in self.refused_methods or (update and params["id"] in self.refused):
            return httpx.Response(200, json=make_jsonrpc_response(error=REFUSAL))
        if method == "enterprise/getEnterprise":
            return self._enterprise(params)
        if method == "enterprise/getEnterpriseEdges":
            result = {"metaData": {"more": False}, "data": list(self.edges.values())}
        elif method == "enterprise/getEnterpriseConfigurations":
            result = list(self.profiles.values())
        elif method == "enterprise/getEnterpriseNetworkSegments":
            result = []  # what a pipeline's load asks for; no module here has one
        elif (
            method == "configuration/deleteConfigurationModule"
            and self.delete_answer is not None
        ):
            result = self.delete_answer
        else:
            return self.store(request)
        return httpx.Response(200, json=make_jsonrpc_response(result=result))


OPT_IN = {live.WRITE_HOST_ENV: HOST, live.WRITE_ENTERPRISE_ENV: str(ENTERPRISE)}


MARKED = "lab edge, pyvelo-it-target"
"""The description of an edge the owner marked for the suite."""


REPLACEABLE = "pyvelo-it-target pyvelo-it-replace-modules"
"""The description of an edge whose modules the suite may also delete and insert."""


def lab_fake() -> FakeLab:
    """Profile 20 (marked, no edge counted) with QoS module 2.

    Edge 5 (marked) with QoS module 1; edge 6, unmarked, with QoS module 3.
    """
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
    """Write the QoS module of *site* with its rate limit value set to *value*.

    Write it as a test would.
    """
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
    """The access *environ* gives a client connected to *connected*.

    And what it asked the Orchestrator.
    """
    fake = FakeLab()
    fake.enterprise_error = enterprise_error
    fake.enterprise_failure = enterprise_failure
    access = await live.check_write_access(
        client_over(fake, connected), enterprise_id, environ
    )
    return access, fake.requests


async def test_an_unset_opt_in_is_off_and_asks_nothing() -> None:
    access, requests = await access_for({})
    assert access == live.WriteAccess(
        granted=False, refusal=live.WRITES_OFF, misconfigured=False
    )
    assert access.refusal == (
        "live write tests are off: set PYVELO_IT_WRITE_HOST to the host name "
        "of the Orchestrator this suite may write to and "
        "PYVELO_IT_WRITE_ENTERPRISE_ID to the ID of the enterprise, then run again"
    )
    assert requests == []


async def test_the_host_and_the_enterprise_of_the_client_are_granted() -> None:
    access, requests = await access_for(OPT_IN)
    assert access == live.WriteAccess(granted=True, refusal="")
    assert requests == [("enterprise/getEnterprise", {})]


async def test_the_host_is_compared_without_case_or_a_last_dot() -> None:
    environ = {**OPT_IN, live.WRITE_HOST_ENV: " VCO.Example.Invalid. "}
    access, _ = await access_for(environ)
    assert access.granted


async def test_the_enterprise_the_requests_name_is_the_one_read() -> None:
    environ = {**OPT_IN, live.WRITE_ENTERPRISE_ENV: "9"}
    access, requests = await access_for(environ, enterprise_id=9)
    assert access.granted
    assert requests == [("enterprise/getEnterprise", {"enterpriseId": 9})]


async def refused(  # noqa: PLR0913 (existing: split it when it next changes)
    environ: dict[str, str],
    reason: str,
    remedy: str,
    *,
    connected: str = HOST,
    enterprise_id: int | None = None,
    enterprise_error: bool = False,
    enterprise_failure: Literal["http", "empty", "no_id"] | None = None,
) -> None:
    """*environ* is refused for *reason*, with what to do.

    The message has neither the host nor the token.
    """
    access, _ = await access_for(
        environ,
        connected=connected,
        enterprise_id=enterprise_id,
        enterprise_error=enterprise_error,
        enterprise_failure=enterprise_failure,
    )
    assert access == live.WriteAccess(
        granted=False,
        refusal=f"writes refused: {reason}; nothing was written; {remedy}",
        misconfigured=True,
    )
    assert HOST not in access.refusal
    assert "test-token" not in access.refusal


async def test_an_opt_in_that_does_not_match_is_refused() -> None:
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


async def test_connect_skips_without_the_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
) -> None:
    monkeypatch.setenv("VCO_HOST", HOST)
    monkeypatch.setenv("VCO_TOKEN", "test-token")
    async with live.connect() as client:
        run = live.run_of(client)
        assert run.access == live.WriteAccess()
        assert live.run_of(client) is run
        request = UpdateConfigurationModule(
            configuration_module_id=1, update=ConfigurationModuleUpdate(data={})
        )
        with pytest.raises(live.WritesRefusedError, match="live write tests are off"):
            _ = await client.update_configuration_module(request)


def qos_update(module_id: int) -> UpdateConfigurationModule:
    return UpdateConfigurationModule(
        configuration_module_id=module_id,
        update=ConfigurationModuleUpdate(data=copy.deepcopy(QOS)),
    )


async def test_without_the_opt_in_restoring_skips_and_nothing_is_sent() -> None:
    fake = lab_fake()
    lab = await live.discover_lab(client_over(fake), None)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    fake.requests.clear()
    with pytest.raises(pytest.skip.Exception, match="live write tests are off"):
        async with live.restoring(site):
            pass
    with pytest.raises(live.WritesRefusedError, match="live write tests are off"):
        _ = await lab.client.update_configuration_module(qos_update(1))
    assert fake.requests == []


async def test_an_opt_in_that_does_not_match_fails_the_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(live.WRITE_HOST_ENV, "other.example.invalid")
    monkeypatch.setenv(live.WRITE_ENTERPRISE_ENV, str(ENTERPRISE))
    fake = lab_fake()
    lab = await live.discover_lab(client_over(fake), None)
    with pytest.raises(pytest.fail.Exception, match=r"^writes refused: "):
        async with live.restoring(lab.edge_site(lab.layouts[0], Name.QOS)):
            pass
    with pytest.raises(pytest.fail.Exception, match=r"^writes refused: "):
        _ = await lab.pick_writable_edge(Name.QOS)
    assert "updateConfigurationModule" not in fake.methods()


async def test_a_test_that_is_not_marked_cannot_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    with live.writer_test(marked=False):
        with pytest.raises(pytest.fail.Exception) as caught:
            async with live.restoring(site):
                pass
        assert str(caught.value) == live.NOT_MARKED
        with pytest.raises(live.WritesRefusedError) as refused:
            _ = await lab.client.update_configuration_module(qos_update(1))
        assert str(refused.value) == live.NOT_MARKED
    assert "updateConfigurationModule" not in fake.methods()


async def test_a_write_outside_a_block_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    with pytest.raises(live.WritesRefusedError) as refused:
        _ = await lab.client.update_configuration_module(qos_update(1))
    assert str(refused.value) == live.OUTSIDE_RESTORING
    assert "updateConfigurationModule" not in fake.methods()


async def test_a_block_covers_its_own_module_and_no_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    async with live.restoring(site):
        await raise_limit(site, 2)
        assert fake.data(1)["serviceRateLimit"]["value"] == 2
        # Module 2 is the profile's: no block is open on it.
        with pytest.raises(live.WritesRefusedError) as refused:
            _ = await lab.client.update_configuration_module(qos_update(2))
        assert str(refused.value) == (
            "an update of a module that no open restoring block covers; "
            "nothing was sent; open `restoring(site)` on the module's own site "
            "and write inside it"
        )
    assert fake.data(1) == QOS
    assert fake.data(2) == QOS
    assert live.run_of(lab.client).open == []


async def test_an_insert_is_covered_by_the_block_of_its_configuration_and_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        with pytest.raises(live.WritesRefusedError) as refused:
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
) -> None:
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
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


async def test_a_restore_that_works_leaves_no_backup(
    backups: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    async with live.restoring(site):
        assert len(list(backups.iterdir())) == 1  # taken before the test writes
        await raise_limit(site, 2)
        assert fake.data(2)["serviceRateLimit"]["value"] == 2
    assert fake.data(2) == QOS
    assert list(backups.iterdir()) == []


NETWORK = {"vlanId": 100, "name": "users", "segmentId": 0, "override": True}
VNFS_EDGE = "deviceSettings:vnfs:edge"
"""An append-only ref name: its rows are left out of the refs a restore sends."""


def ds_lab() -> FakeLab:
    """Profile 20 with a deviceSettings module (4).

    The module has a segment, a segment row and an append-only row.
    """
    fake = lab_fake()
    data = {
        "lan": {"networks": [dict(NETWORK)], "interfaces": []},
        "segments": [segment_entry(0, "Global Segment", SEG0, dns=dns(), css={})],
    }
    fake.module(4, 20, "deviceSettings", data)
    fake.store.rows[4]["refs"] = {
        SEGMENT: segment_row(0, SEG0_OBJECT, SEG0),
        VNFS_EDGE: {
            **service_row(VNFS_EDGE, DNS_A, 701, SEG0_OBJECT, SEG0),
            "type": "securityVnf",
        },
    }
    return fake


def updates_of(fake: FakeLab) -> list[dict[str, Any]]:
    """The ``_update`` of each module update the fake was sent."""
    return [
        params["_update"]
        for method, params in fake.requests
        if method == "configuration/updateConfigurationModule"
    ]


async def test_a_restore_of_a_module_with_segments_sends_the_refs_as_read(
    backups: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = ds_lab()
    original = copy.deepcopy(fake.data(4))
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, DS)
    async with live.restoring(site):
        module = await site.read_present()
        data = copy.deepcopy(module.raw_data)
        assert isinstance(data, dict)
        data["lan"]["networks"][0]["name"] = "renamed"
        await live.write_data(site, module, data)
        assert fake.data(4) != original
        at_restore = copy.deepcopy(fake.store.rows[4]["refs"])
    assert fake.data(4) == original
    *_, restore = updates_of(fake)
    assert restore["refs"] == {
        name: rows for name, rows in at_restore.items() if name != VNFS_EDGE
    }
    assert SEGMENT in restore["refs"]
    # The backup holds none of them.
    assert list(backups.iterdir()) == []


async def test_a_write_of_a_module_with_segments_carries_the_refs_but_not_the_append_only_ones(  # noqa: E501 (a test's name)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = ds_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, DS)
    module = await site.read_present()
    as_read = copy.deepcopy(fake.store.rows[4]["refs"][SEGMENT])
    data = module.raw_data
    assert isinstance(data, dict)
    assert live.refs_as_read(module, data) == {SEGMENT: as_read}
    assert await live.own_data_refusal(site) is None
    # The preflight write is a write of the module as it is: it carries them too.
    first, *_ = updates_of(fake)
    assert set(first["refs"]) == {SEGMENT}


async def test_a_restore_of_a_module_without_segments_or_of_another_name_sends_no_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = ds_lab()
    fake.data(4)["segments"] = []  # no segments: no refs go
    fake.module(6, 20, "firewall", {**FIREWALL, "segments": [segment(0)]})
    fake.store.rows[6]["refs"] = {SEGMENT: segment_row(0, SEG0_OBJECT, SEG0)}
    lab = await opened(fake, monkeypatch)
    for module_id, name in ((4, DS), (6, Name.FIREWALL)):
        site = lab.profile_site(20, name)
        async with live.restoring(site):
            module = await site.read_present()
            assert module.id_ == module_id
            data = copy.deepcopy(module.raw_data)
            assert isinstance(data, dict)
            assert live.refs_as_read(module, data) is MISSING
            await live.write_data(site, module, data)
    assert all("refs" not in update for update in updates_of(fake))


async def test_a_restore_that_fails_names_the_backup_and_never_the_data(
    backups: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = lab_fake()
    fake.data(2)["segments"] = [{"segment": {"name": "secret-lab-name"}}]
    original = copy.deepcopy(fake.data(2))
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    with pytest.raises(pytest.fail.Exception) as caught:  # noqa: PT012  # restoring raises as it exits, so the block holds the run it restores
        async with live.restoring(site):
            await raise_limit(site, 2)
            fake.refused.add(2)  # the write back is refused
    message = str(caught.value)
    (backup,) = backups.iterdir()
    assert message.endswith(f"; the original QOS module is saved at {backup}")
    assert "secret-lab-name" not in message
    assert json.loads(backup.read_text())["data"] == original


def as_pytest_reports(failed: pytest.ExceptionInfo[BaseException]) -> str:
    """The report pytest writes for a failure the suite worded itself.

    A failure raised with ``pytrace=False`` is reported as the text of each
    exception of its chain, and ``redact_report`` leaves such a report as it
    is: an exception chained to a worded failure is printed with its own text.
    """
    return str(failed.getrepr(style="value"))


def lab_error() -> VcoOperationError:
    """An error of a read, as a test's body ends with one: its text names the lab."""
    error = VcoOperationError("getConfiguration", "secret-lab-name cannot be read")
    error.__cause__ = ValueError("secret-lab-name in the answer")
    return error


BODY_FAILURES: list[tuple[Callable[[], BaseException], str]] = [
    (
        lab_error,
        (
            "the test had failed before that (VcoOperationError in "
            "getConfiguration: no error code)"
        ),
    ),
    (lambda: KeyError("secret-lab-name"), "the test had failed before that (KeyError)"),
    (
        lambda: pytest.fail.Exception("a message the test wrote", pytrace=False),
        "the test had failed before that (a message the test wrote)",
    ),
    (
        lambda: pytest.skip.Exception("no rule to copy"),
        "the test had skipped before that (no rule to copy)",
    ),
]
"""How a test's body ends, and what a failed restore after it says of that."""


@pytest.mark.parametrize(
    ("ending", "said"), BODY_FAILURES, ids=["an error", "a raw error", "fail", "skip"]
)
async def test_a_restore_that_fails_after_its_test_failed_reports_both_failures(
    ending: Callable[[], BaseException],
    said: str,
    backups: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The restore's failure, then the body's as ``failure_text`` gives it.

    Nothing else of the chain is reported: not the text of the body's
    exception, nor of an exception it was raised from.
    """
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    with pytest.raises(pytest.fail.Exception) as caught:  # noqa: PT012  # restoring raises as it exits, so the block holds the run it restores
        async with live.restoring(site):
            await raise_limit(site, 2)
            fake.data(2)["serviceRateLimit"]["value"] = 9  # another writer's change
            raise ending()
    (backup,) = backups.iterdir()
    report = as_pytest_reports(caught)
    assert "secret-lab-name" not in report
    assert report == (
        "restore: the QOS module changed outside this run after its last write "
        "(~ serviceRateLimit.value); it was left as it is, with this run's "
        "changes; check who else changed the QOS module, and put the original "
        f"back by hand if it is wanted; {said}; the original QOS module is saved "
        f"at {backup}"
    )


async def test_a_restore_with_no_backup_that_fails_after_its_test_failed_reports_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An edge with no module of its own: the module the test added stays."""
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[2], Name.QOS)
    with pytest.raises(pytest.fail.Exception) as caught:  # noqa: PT012  # restoring raises as it exits, so the block holds the run it restores
        async with live.restoring(site) as original:
            assert original is None
            await live.insert_module(site, copy.deepcopy(QOS), None)
            fake.delete_answer = {"rows": 0}  # the restore's delete deletes nothing
            raise lab_error()
    assert as_pytest_reports(caught) == (
        "restore: the QOS module the test added is still there; the test had "
        "failed before that (VcoOperationError in getConfiguration: no error code)"
    )


async def test_a_restore_that_fails_alone_reports_as_it_did_and_no_text_of_its_error(
    backups: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The error of the restore's own write is named by its class alone."""
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)

    def fails(method: str) -> None:
        if method == "configuration/updateConfigurationModule":
            raise httpx.ConnectError("secret-lab-name did not answer")

    with pytest.raises(pytest.fail.Exception) as caught:  # noqa: PT012  # restoring raises as it exits, so the block holds the run it restores
        async with live.restoring(site):
            await raise_limit(site, 2)
            fake.before = fails
    (backup,) = backups.iterdir()
    assert as_pytest_reports(caught) == (
        f"ConnectError; the original QOS module is saved at {backup}"
    )


async def test_a_restore_with_no_backup_that_fails_alone_ends_with_its_own_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing to add to it: the report is made of the error, as of any other."""
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[2], Name.QOS)
    with pytest.raises(VcoOperationError) as caught:  # noqa: PT012  # restoring raises as it exits, so the block holds the run it restores
        async with live.restoring(site) as original:
            assert original is None
            await live.insert_module(site, copy.deepcopy(QOS), None)
            fake.refused_methods = {"configuration/deleteConfigurationModule"}
    assert live.failure_text(caught.value) == (
        "DataRefusedError in configuration/deleteConfigurationModule: "
        "DS00000001 lan.networks"
    )


async def test_a_test_that_fails_alone_ends_with_its_own_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The restore worked: the body's error leaves the block as it was raised."""
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    error = KeyError("secret-lab-name")
    with pytest.raises(KeyError) as caught:  # noqa: PT012  # restoring raises as it exits, so the block holds the run it restores
        async with live.restoring(site):
            await raise_limit(site, 2)
            raise error
    assert caught.value is error
    assert fake.data(2) == QOS


async def test_a_refused_delete_and_a_refused_effective_read_carry_codes_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


async def test_write_module_outside_a_block_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    module = await lab.edge_site(lab.layouts[0], Name.QOS).read_present()
    data = copy.deepcopy(QOS)
    data["serviceRateLimit"]["value"] = 2
    with pytest.raises(live.WritesRefusedError) as refused:
        _ = await lab.client.write_module(module, plan_module(module, data))
    assert str(refused.value) == live.OUTSIDE_RESTORING
    assert "updateConfigurationModule" not in fake.methods()
    assert fake.data(1) == QOS


async def test_write_module_inside_a_block_writes_the_module_it_covers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
) -> None:
    fake = lab_fake()
    fake.edges[5]["description"] = REPLACEABLE
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    elsewhere = lab.profile_site(20, Name.QOS)
    with pytest.raises(live.WritesRefusedError) as outside:
        await live.delete_module(site, 1)
    assert str(outside.value) == live.OUTSIDE_RESTORING
    async with live.restoring(site):
        # A module of another configuration, and a module of this one the block did not
        # find.
        with pytest.raises(live.WritesRefusedError) as other_site:
            await live.delete_module(elsewhere, 2)
        with pytest.raises(live.WritesRefusedError) as other_module:
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
) -> None:
    fake = lab_fake()
    fake.edges[5]["description"] = REPLACEABLE
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    run = live.run_of(lab.client)
    async with live.restoring(site):
        # A second QoS module in the configuration the block is on: the block does not
        # delete it.
        await live.insert_module(site, copy.deepcopy(QOS), None)
        (added,) = set(fake.store.rows) - {1, 2, 3}
        assert set(run.inserted) == {added}
    assert run.inserted == {}
    async with live.restoring(site):
        with pytest.raises(live.WritesRefusedError, match="an update of a module"):
            _ = await lab.client.update_configuration_module(qos_update(added))


def module_delete(module_id: int) -> DeleteConfigurationModule:
    return DeleteConfigurationModule(id_=module_id)


def replaceable_lab() -> FakeLab:
    """``lab_fake`` with edge 5 marked for deleting and inserting modules as well.

    And edge 7 (configuration 12), marked the same, with no QoS module of its own.
    """
    fake = lab_fake()
    fake.edges[5]["description"] = REPLACEABLE
    fake.add_edge(7, 12, 20, description=REPLACEABLE)
    return fake


def deletes_of(fake: FakeLab) -> list[int]:
    """The module ID of each delete *fake* was sent."""
    return [
        params["id"]
        for method, params in fake.requests
        if method == "configuration/deleteConfigurationModule"
    ]


async def test_the_clients_own_delete_of_the_module_a_block_found_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    run = live.run_of(lab.client)
    async with live.restoring(site):
        sent = run.sent
        answer = await lab.client.delete_configuration_module(module_delete(1))
        assert (answer.error, answer.rows) == (None, 1)
        assert run.sent == sent + 1
        assert 1 not in fake.store.rows
        # The records are as after ``delete_module``.
        assert set(run.deleted) == {1}
        assert run.maybe_deleted == {}
        assert 1 not in run.seen
    # Leaving the block inserted the original again.
    back = await site.read_present()
    assert back.id_ != 1
    assert back.raw_data == QOS
    assert deletes_of(fake) == [1]


async def test_the_clients_own_delete_of_a_module_inserted_in_the_block_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[2], Name.QOS)
    run = live.run_of(lab.client)
    async with live.restoring(site) as original:
        assert original is None
        await live.insert_module(site, copy.deepcopy(QOS), None)
        (added,) = set(fake.store.rows) - {1, 2, 3}
        assert set(run.inserted) == {added}
        answer = await lab.client.delete_configuration_module(module_delete(added))
        assert (answer.error, answer.rows) == (None, 1)
        assert set(run.deleted) == {added}
        assert run.maybe_deleted == {}
        assert run.inserted == {}
        assert added not in run.seen
    # The restore found the layer as the block found it, and sent nothing.
    assert set(fake.store.rows) == {1, 2, 3}
    assert deletes_of(fake) == [added]


async def test_the_clients_own_delete_from_a_test_that_is_not_marked_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    with (
        live.writer_test(marked=False),
        pytest.raises(live.WritesRefusedError) as refused,
    ):
        _ = await lab.client.delete_configuration_module(module_delete(1))
    assert str(refused.value) == live.NOT_MARKED
    assert deletes_of(fake) == []


async def test_the_clients_own_delete_without_the_opt_in_is_refused() -> None:
    fake = replaceable_lab()
    lab = await live.discover_lab(client_over(fake), None)
    with pytest.raises(live.WritesRefusedError, match="live write tests are off"):
        _ = await lab.client.delete_configuration_module(module_delete(1))
    assert deletes_of(fake) == []


async def test_the_clients_own_delete_outside_a_block_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    with pytest.raises(live.WritesRefusedError) as refused:
        _ = await lab.client.delete_configuration_module(module_delete(1))
    assert str(refused.value) == live.OUTSIDE_RESTORING
    assert deletes_of(fake) == []


async def test_the_clients_own_delete_needs_the_mark_for_modules_on_the_blocks_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = lab_fake()  # edge 5: marked, not for modules
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    async with live.restoring(site):
        with pytest.raises(live.WritesRefusedError) as refused:
            _ = await lab.client.delete_configuration_module(module_delete(1))
    assert str(refused.value) == (
        "a delete of a QOS module of an edge that is not marked "
        "pyvelo-it-replace-modules; nothing was sent; put "
        "pyvelo-it-replace-modules beside pyvelo-it-target in the description of "
        "an edge the suite may delete and insert modules of"
    )
    assert deletes_of(fake) == []
    assert set(fake.store.rows) == {1, 2, 3}


async def test_the_clients_own_delete_of_a_module_no_open_block_covers_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    other = lab.edge_site(lab.layouts[2], Name.QOS)
    run = live.run_of(lab.client)
    async with live.restoring(other):
        await live.insert_module(other, copy.deepcopy(QOS), None)
        (added,) = set(run.inserted)
        fake.requests.clear()
    # The block that inserted it is closed (and deleted it); one is open elsewhere.
    fake.module(added, 12, "QOS", QOS)
    async with live.restoring(site):
        # The unmarked edge's module, the profile's, the one a closed block inserted,
        # and an ID of no module at all.
        for module_id in (3, 2, added, 99):
            with pytest.raises(live.WritesRefusedError) as refused:
                _ = await lab.client.delete_configuration_module(
                    module_delete(module_id)
                )
            assert str(refused.value) == (
                "a delete of a module that no open restoring block covers; "
                "nothing was sent; open `restoring(site)` on the module's own "
                "site and write inside it"
            )
    assert deletes_of(fake) == [added]  # the first block's own restore, and no other
    assert {1, 2, 3, added} <= set(fake.store.rows)


async def test_a_delete_is_not_covered_by_a_marked_block_that_is_not_the_modules_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The profile's module, found by a block of its own, with a marked edge's open.

    Neither the client's own delete nor ``delete_module`` on the edge's site
    takes the edge's mark for the profile's module.
    """
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    profile = lab.profile_site(20, Name.QOS)
    async with live.restoring(profile), live.restoring(site):
        with pytest.raises(live.WritesRefusedError, match="that is not marked"):
            _ = await lab.client.delete_configuration_module(module_delete(2))
        with pytest.raises(live.WritesRefusedError, match="that is not marked"):
            await live.delete_module(site, 2)
    assert deletes_of(fake) == []
    assert fake.data(2) == QOS


async def test_delete_module_sends_through_the_clients_own_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    run = live.run_of(lab.client)
    delete = lab.client.delete_configuration_module
    seen: list[int] = []

    async def spy(request: DeleteConfigurationModule) -> RowsModifiedConfirmation:
        seen.append(request.id_)
        return await delete(request)

    monkeypatch.setattr(lab.client, "delete_configuration_module", spy)
    async with live.restoring(site):
        await live.delete_module(site, 1)
        assert seen == [1]
        assert set(run.deleted) == {1}
    assert (await site.read_present()).raw_data == QOS


NOT_DELETED = [{"rows": 0}, {"error": "secret-lab-name is locked"}]
"""Answers to a delete that say the module was not deleted."""


@pytest.mark.parametrize("answer", NOT_DELETED, ids=["no row", "an error"])
@pytest.mark.parametrize("gone", [False, True], ids=["there", "gone"])
async def test_after_a_delete_answered_as_not_done_the_module_found_is_put_right(
    answer: dict[str, Any],
    gone: bool,  # noqa: FBT001 (a parameter of the test)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    run = live.run_of(lab.client)
    async with live.restoring(site):
        await raise_limit(site, 2)
        fake.delete_answer = answer
        told = await lab.client.delete_configuration_module(module_delete(1))
        assert (told.error, told.rows) == (answer.get("error"), answer.get("rows"))
        fake.delete_answer = None
        # Not known to be deleted, and not forgotten either.
        assert run.deleted == {}
        assert set(run.maybe_deleted) == {1}
        assert 1 in run.seen
        if gone:
            del fake.store.rows[1]  # the Orchestrator deleted it all the same
    back = await site.read_present()
    assert back.raw_data == QOS
    assert (back.id_ == 1) is not gone
    assert fake.methods().count("insertConfigurationModule") == (1 if gone else 0)


@pytest.mark.parametrize("answer", NOT_DELETED, ids=["no row", "an error"])
@pytest.mark.parametrize("gone", [False, True], ids=["there", "gone"])
async def test_after_a_delete_answered_as_not_done_the_module_inserted_is_put_right(
    answer: dict[str, Any],
    gone: bool,  # noqa: FBT001 (a parameter of the test)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[2], Name.QOS)
    run = live.run_of(lab.client)
    async with live.restoring(site):
        await live.insert_module(site, copy.deepcopy(QOS), None)
        (added,) = set(run.inserted)
        fake.delete_answer = answer
        _ = await lab.client.delete_configuration_module(module_delete(added))
        fake.delete_answer = None
        assert run.deleted == {}
        assert set(run.maybe_deleted) == {added}
        assert set(run.inserted) == {added}
        assert added in run.seen
        if gone:
            del fake.store.rows[added]
    assert set(fake.store.rows) == {1, 2, 3}
    # Still there, the restore deleted it; gone, it sent no second delete.
    assert deletes_of(fake) == ([added] if gone else [added, added])


@pytest.mark.parametrize("gone", [False, True], ids=["there", "gone"])
async def test_after_a_delete_that_raised_once_sent_the_module_found_is_put_right(
    gone: bool,  # noqa: FBT001 (a parameter of the test)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    run = live.run_of(lab.client)

    def fails(method: str) -> None:
        if method == "configuration/deleteConfigurationModule":
            raise httpx.ConnectError("the request did not arrive")

    async with live.restoring(site):
        if gone:
            fake.store.lose_answers = {1}  # the store's first write: done, unanswered
        else:
            fake.before = fails
        with pytest.raises(httpx.TransportError):
            _ = await lab.client.delete_configuration_module(module_delete(1))
        fake.before = None
        assert (1 in fake.store.rows) is not gone
        assert run.deleted == {}
        assert set(run.maybe_deleted) == {1}
        assert 1 in run.seen
    back = await site.read_present()
    assert back.raw_data == QOS
    assert (back.id_ == 1) is not gone


@pytest.mark.parametrize("gone", [False, True], ids=["there", "gone"])
async def test_after_a_delete_that_raised_once_sent_the_module_inserted_is_put_right(
    gone: bool,  # noqa: FBT001 (a parameter of the test)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[2], Name.QOS)
    run = live.run_of(lab.client)
    async with live.restoring(site):
        await live.insert_module(site, copy.deepcopy(QOS), None)
        (added,) = set(run.inserted)
        if gone:
            fake.store.lose_answers = {2}  # the insert was the store's first write
        else:
            fake.refused_methods = {"configuration/deleteConfigurationModule"}
        with pytest.raises((httpx.ReadTimeout, VcoOperationError)) as caught:
            _ = await lab.client.delete_configuration_module(module_delete(added))
        fake.refused_methods = set()
        # A refusal of the Orchestrator carries its codes and no name of the lab.
        assert "secret-lab-name" not in str(caught.value)
        assert run.deleted == {}
        assert set(run.maybe_deleted) == {added}
        assert set(run.inserted) == {added}
    assert set(fake.store.rows) == {1, 2, 3}
    assert deletes_of(fake) == ([added] if gone else [added, added])


async def test_a_delete_that_failed_before_it_was_sent_changes_no_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    run = live.run_of(lab.client)
    async with live.restoring(site):
        seen = copy.deepcopy(run.seen)
        with monkeypatch.context() as patched:

            async def unsendable(*_args: object, **_kwargs: object) -> None:
                raise RuntimeError("failed before anything was sent")

            patched.setattr(lab.client, "portal", unsendable)
            with pytest.raises(RuntimeError):
                _ = await lab.client.delete_configuration_module(module_delete(1))
        assert run.deleted == {}
        assert run.maybe_deleted == {}
        assert run.seen == seen
    assert deletes_of(fake) == []
    assert fake.data(1) == QOS


async def test_delete_again_sends_the_delete_of_a_module_this_run_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[2], Name.QOS)
    run = live.run_of(lab.client)
    async with live.restoring(site):
        await live.insert_module(site, copy.deepcopy(QOS), None)
        (added,) = set(run.inserted)
        await live.delete_module(site, added)
        # The client's own delete is not sent for a module that is gone.
        with pytest.raises(live.WritesRefusedError, match="no open restoring block"):
            _ = await lab.client.delete_configuration_module(module_delete(added))
        assert deletes_of(fake) == [added]
        sent = run.sent
        answer = await live.delete_again(site, added)
        assert (answer.error, answer.rows) == (None, 0)
        assert run.sent == sent + 1
        assert deletes_of(fake) == [added, added]
        assert set(run.deleted) == {added}
        assert run.inserted == {}
    assert set(fake.store.rows) == {1, 2, 3}


async def test_delete_again_is_refused_for_a_module_this_run_did_not_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    run = live.run_of(lab.client)
    async with live.restoring(site):
        fake.delete_answer = {"rows": 0}
        _ = await lab.client.delete_configuration_module(module_delete(1))
        fake.delete_answer = None
        assert set(run.maybe_deleted) == {1}
        # The module the block found, one whose delete was answered as not done,
        # another edge's, and an ID of no module at all.
        for module_id in (1, 3, 99):
            with pytest.raises(live.WritesRefusedError) as refused:
                _ = await live.delete_again(site, module_id)
            assert str(refused.value) == (
                "a second delete of a module this run did not delete inside a "
                "restoring block that is still open on its site; nothing was "
                "sent; delete_again only sends the delete of a module again that "
                "an answer to this run said was deleted, and only inside the "
                "block it was deleted in, so delete it first with delete_module "
                "and call delete_again inside the same restoring block"
            )
    assert deletes_of(fake) == [1]
    assert set(fake.store.rows) == {1, 2, 3}


async def test_delete_again_needs_an_open_block_on_its_site_marked_for_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[2], Name.QOS)
    elsewhere = lab.profile_site(20, Name.QOS)
    run = live.run_of(lab.client)
    async with live.restoring(site):
        await live.insert_module(site, copy.deepcopy(QOS), None)
        (added,) = set(run.inserted)
        await live.delete_module(site, added)
    fake.requests.clear()
    with pytest.raises(live.WritesRefusedError) as outside:
        _ = await live.delete_again(site, added)
    assert str(outside.value) == live.OUTSIDE_RESTORING
    async with live.restoring(elsewhere):
        with pytest.raises(live.WritesRefusedError, match=r"^a delete of a QOS module"):
            _ = await live.delete_again(site, added)
        with pytest.raises(live.WritesRefusedError, match="that is not marked"):
            _ = await live.delete_again(elsewhere, added)
    with (
        live.writer_test(marked=False),
        pytest.raises(live.WritesRefusedError) as unmarked,
    ):
        _ = await live.delete_again(site, added)
    assert str(unmarked.value) == live.NOT_MARKED
    assert deletes_of(fake) == []


async def test_a_refused_delete_again_carries_its_codes_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    async with live.restoring(site):
        await live.delete_module(site, 1)
        fake.refused_methods = {"configuration/deleteConfigurationModule"}
        with pytest.raises(VcoOperationError) as caught:
            _ = await live.delete_again(site, 1)
        fake.refused_methods = set()
    assert live.refusal_codes(caught.value) == "DS00000001 lan.networks"
    assert "secret-lab-name" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
    assert (await site.read_present()).raw_data == QOS


async def test_delete_again_is_refused_once_the_block_that_deleted_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Another block on the same site, marked the same, did not delete the module."""
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[2], Name.QOS)
    run = live.run_of(lab.client)
    async with live.restoring(site):
        await live.insert_module(site, copy.deepcopy(QOS), None)
        (added,) = set(run.inserted)
        await live.delete_module(site, added)
    fake.requests.clear()
    async with live.restoring(site):
        with pytest.raises(live.WritesRefusedError) as refused:
            _ = await live.delete_again(site, added)
    assert str(refused.value) == live.NOT_DELETED_BEFORE
    assert deletes_of(fake) == []


async def test_delete_again_is_refused_on_another_site_than_the_deleting_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[2], Name.QOS)
    other = lab.edge_site(lab.layouts[0], Name.QOS)  # marked for modules as well
    run = live.run_of(lab.client)
    async with live.restoring(site), live.restoring(other):
        await live.insert_module(site, copy.deepcopy(QOS), None)
        (added,) = set(run.inserted)
        await live.delete_module(site, added)
        with pytest.raises(live.WritesRefusedError) as refused:
            _ = await live.delete_again(other, added)
        assert str(refused.value) == live.NOT_DELETED_BEFORE
        assert deletes_of(fake) == [added]
        # On the site of the block that deleted it, the second delete is sent.
        _ = await live.delete_again(site, added)
        assert deletes_of(fake) == [added, added]


async def test_a_delete_answered_as_not_done_is_forgotten_when_a_restore_sees_the_module(  # noqa: E501 (a test's name)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A block inside another on one module: the inner restore reads the module."""
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    run = live.run_of(lab.client)
    async with live.restoring(site):
        async with live.restoring(site):
            fake.delete_answer = {"rows": 0}
            _ = await lab.client.delete_configuration_module(module_delete(1))
            fake.delete_answer = None
            assert set(run.maybe_deleted) == {1}
        # The inner restore saw the module there. The outer block, which the
        # record is kept for, is still open.
        assert run.maybe_deleted == {}
    assert fake.data(1) == QOS
    assert deletes_of(fake) == [1]


async def test_a_delete_answered_as_not_done_is_forgotten_when_its_block_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    run = live.run_of(lab.client)
    async with live.restoring(site):
        fake.delete_answer = {"rows": 0}
        _ = await lab.client.delete_configuration_module(module_delete(1))
        fake.delete_answer = None
        del fake.store.rows[1]  # the Orchestrator deleted it all the same
        assert set(run.maybe_deleted) == {1}
    # The restore found the module gone and inserted it again: it never saw
    # module 1 there, and the record went with the block.
    assert (await site.read_present()).id_ != 1
    assert run.maybe_deleted == {}


async def test_a_later_block_does_not_take_an_earlier_unsure_delete_for_its_own(
    backups: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A delete answered as not done, the module left; later another writer deletes it.

    The later block's restore must say that this run did not delete the
    module, and not insert it again on the word of the earlier block's delete.
    """
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    async with live.restoring(site):
        fake.delete_answer = {"rows": 0}
        _ = await lab.client.delete_configuration_module(module_delete(1))
        fake.delete_answer = None
    assert 1 in fake.store.rows
    with pytest.raises(pytest.fail.Exception) as caught:
        async with live.restoring(site):
            del fake.store.rows[1]  # another writer deleted it
    (backup,) = backups.iterdir()
    assert str(caught.value) == (
        "restore: the QOS module is gone and this run did not delete it; it "
        "was not put back; check who deleted it, and insert it again from the "
        f"saved original if it is wanted; the original QOS module is saved at {backup}"
    )
    assert "insertConfigurationModule" not in fake.methods()


WAN: dict[str, Any] = {"links": []}
"""A WAN module's data: a module the suite updates and never inserts."""


async def test_a_delete_of_a_module_the_suite_cannot_insert_again_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A WAN module a block found, on an edge marked for deleting and inserting."""
    fake = replaceable_lab()
    fake.module(4, 10, "WAN", WAN)
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.WAN)
    refusal = (
        "a delete of a WAN module, which the suite could not put back, as it "
        "inserts only a firewall or a QoS module; nothing was sent; delete only "
        "a firewall or a QoS module, and change any other module with an update "
        "inside its restoring block"
    )
    async with live.restoring(site) as original:
        assert original is not None
        assert original.module_id == 4
        with pytest.raises(live.WritesRefusedError) as own:
            _ = await lab.client.delete_configuration_module(module_delete(4))
        with pytest.raises(live.WritesRefusedError) as helper:
            await live.delete_module(site, 4)
    assert str(own.value) == refusal
    assert str(helper.value) == refusal
    assert deletes_of(fake) == []
    assert fake.data(4) == WAN


async def test_an_insert_whose_answer_is_lost_is_sent_and_is_the_runs_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[2], Name.QOS)
    run = live.run_of(lab.client)
    async with live.restoring(site):
        lost = live.losing_the_insert_answer(lab.client, monkeypatch, sent=True)
        with pytest.raises(httpx.ReadTimeout):
            await live.insert_module(site, copy.deepcopy(QOS), None)
        (added,) = set(fake.store.rows) - {1, 2, 3}
        assert lost == [added]
        assert set(run.inserted) == {added}
    # The module is the run's own, so leaving the block deleted it.
    assert set(fake.store.rows) == {1, 2, 3}


async def test_an_insert_that_never_left_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[2], Name.QOS)
    run = live.run_of(lab.client)
    async with live.restoring(site):
        sent = run.sent
        lost = live.losing_the_insert_answer(lab.client, monkeypatch, sent=False)
        with pytest.raises(httpx.ReadTimeout):
            await live.insert_module(site, copy.deepcopy(QOS), None)
        assert lost == []
        assert run.sent == sent
        assert run.inserted == {}
    assert "insertConfigurationModule" not in fake.methods()
    assert set(fake.store.rows) == {1, 2, 3}


@pytest.mark.parametrize("sent", [True, False], ids=["stored", "never sent"])
async def test_only_the_next_insert_loses_its_answer(
    sent: bool,  # noqa: FBT001 (a parameter of the test)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The insert after the lost one is answered: the restore's own insert is one."""
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    async with live.restoring(site):
        await live.delete_module(site, 1)
        lost = live.losing_the_insert_answer(lab.client, monkeypatch, sent=sent)
        with pytest.raises(httpx.ReadTimeout):
            await live.insert_module(site, copy.deepcopy(QOS), None)
        for module_id in lost:
            await live.delete_module(site, module_id)
        assert await site.read() is None
    # The spy is still over the client, and the restore's insert went through it.
    back = await site.read_present()
    assert back.raw_data == QOS
    assert len(lost) == (1 if sent else 0)
    assert fake.methods().count("insertConfigurationModule") == len(lost) + 1


@pytest.mark.parametrize("sent", [True, False], ids=["stored", "never sent"])
async def test_a_save_whose_insert_answer_is_lost_leaves_the_insert_unconfirmed(
    sent: bool,  # noqa: FBT001 (a parameter of the test)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real pipeline's save of a new module, with the insert's answer lost."""
    fake = replaceable_lab()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[2], Name.QOS)
    run = live.run_of(lab.client)
    pipeline = ConfigPipeline(lab.client)
    async with live.restoring(site):
        state = await pipeline.load(7)
        state.edge.qos.service_rate_limit.set_local(
            ServiceRateLimit.model_validate(
                {"enabled": False, "inputType": "percent", "value": 1}
            )
        )
        assert [m.operation for m in state.plan().changed] == ["insert"]
        lost = live.losing_the_insert_answer(lab.client, monkeypatch, sent=sent)
        with pytest.raises(httpx.ReadTimeout):
            _ = await pipeline.save(state)
        assert state.unconfirmed_inserts == {"QOS"}
        assert state.module("QOS").is_new
        stored = set(fake.store.rows) - {1, 2, 3}
        assert stored == set(lost)
        assert len(lost) == (1 if sent else 0)
        assert set(run.inserted) == stored
    assert set(fake.store.rows) == {1, 2, 3}


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
            DeleteConfigurationModule(id_=1),
            RowsModifiedConfirmation,
        ),
    ),
]


@pytest.mark.parametrize("call", WRITE_CALLS, ids=[c.name for c in WRITE_CALLS])
async def test_a_write_that_is_not_the_suites_own_is_refused_even_in_a_block(
    call: WriteCall, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    async with live.restoring(site):
        fake.requests.clear()
        with pytest.raises(live.WritesRefusedError) as refused:
            _ = await call.send(lab.client)
        sent = list(fake.requests)
    message = str(refused.value)
    assert re.match(r"a call to \w+/\w+, which is not a read", message)
    assert "nothing was sent" in message
    assert "restoring block" in message
    assert HOST not in message
    assert "test-token" not in message
    assert sent == []


async def test_a_read_passes_every_form_of_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
def test_a_method_that_starts_with_a_read_verb_passes(method: str) -> None:
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
def test_any_other_method_is_refused_outside_the_code_that_checked_it(
    method: str,
) -> None:
    with pytest.raises(live.WritesRefusedError, match=r"^a call to "):
        live.check_call(method)


def string_constants(function: ast.AST) -> list[str]:
    return [
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_every_method_the_read_only_live_tests_reach_passes_the_guard() -> None:
    """The methods named in the analytics are reads.

    So are those named in each read operation of the client.
    """
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
    assert "enterprise/getEnterpriseEdges" in reached
    assert len(reached) > 20
    for name in sorted(reached):
        live.check_call(name)


def test_marks_are_whole_words_of_a_description() -> None:
    assert live.marks_in("lab edge, pyvelo-it-target.") == {live.TARGET_MARK}
    assert live.marks_in(REPLACEABLE) == {live.TARGET_MARK, live.REPLACE_MODULES_MARK}
    assert live.marks_in("pyvelo-it-targets pyvelo-it-0123456789") == frozenset()
    assert live.marks_in(None) == frozenset()


@pytest.mark.parametrize(
    "description",
    [
        "pyvelo-it-target_old",
        "old_pyvelo-it-target",
        "pyvelo-it-target\u00dc",
        "pyvelo-it-target/prod",
        "prod/pyvelo-it-target",
    ],
)
def test_a_mark_inside_a_longer_token_is_none(description: str) -> None:
    assert live.marks_in(description) == frozenset()


@pytest.mark.parametrize(
    "description",
    [
        "pyvelo-it-target",
        "lab edge pyvelo-it-target and more",
        "lab edge pyvelo-it-target.",
        "lab edge, pyvelo-it-target, more",
        "(pyvelo-it-target)",
    ],
)
def test_a_mark_bounded_by_spaces_or_punctuation_counts(description: str) -> None:
    assert live.marks_in(description) == {live.TARGET_MARK}


def test_a_profile_with_edges_needs_the_second_mark() -> None:
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


async def test_discovery_reads_the_marks(monkeypatch: pytest.MonkeyPatch) -> None:
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


async def test_only_marked_edges_are_picked(monkeypatch: pytest.MonkeyPatch) -> None:
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
) -> None:
    fake = lab_fake()
    fake.add_edge(7, 12, 20, description=REPLACEABLE)
    fake.add_edge(8, 13, 20, description=MARKED)
    lab = await opened(fake, monkeypatch)
    assert [x.edge_id for x in lab.edges_lacking(Name.QOS)] == [7]
    assert [x.edge_id for x in lab.edges_lacking(Name.FIREWALL)] == [7]


async def test_with_no_mark_every_pick_skips_and_says_how(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


async def test_edge_targets_are_required_after_the_opt_in_and_name_the_missing_mark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeLab()
    fake.add_profile(20, description="the default profile")
    fake.add_edge(5, 10, 20)
    lab = await opened(fake, monkeypatch)
    with pytest.raises(pytest.skip.Exception) as skipped:
        _ = lab.require_edge_targets()
    assert str(skipped.value) == live.NO_EDGE_MARKED

    marked = FakeLab()
    marked.add_profile(20, description="the default profile")
    marked.add_edge(5, 10, 20, description=MARKED)
    lab = await opened(marked, monkeypatch)
    assert [x.edge_id for x in lab.require_edge_targets()] == [5]
    with pytest.raises(pytest.skip.Exception) as skipped:
        _ = lab.require_edge_targets(replace_modules=True)
    assert str(skipped.value) == live.NO_EDGE_FOR_MODULES


async def test_edge_targets_with_the_opt_in_wrong_fail_before_the_marks_are_looked_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(live.WRITE_HOST_ENV, "other.example.invalid")
    monkeypatch.setenv(live.WRITE_ENTERPRISE_ENV, str(ENTERPRISE))
    fake = FakeLab()
    fake.add_profile(20, description="the default profile")
    fake.add_edge(5, 10, 20)
    lab = await live.discover_lab(client_over(fake), None)
    with pytest.raises(pytest.fail.Exception, match=r"^writes refused: "):
        _ = lab.require_edge_targets(replace_modules=True)


async def test_a_marked_profile_with_edges_is_passed_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = lab_fake()
    fake.profiles[20]["edgeCount"] = 2
    lab = await opened(fake, monkeypatch)
    with pytest.raises(pytest.skip.Exception) as skipped:
        _ = await lab.pick_writable_profile(Name.QOS)
    assert str(skipped.value) == live.NO_PROFILE_MARKED
    fake.profiles[20]["description"] = "pyvelo-it-target pyvelo-it-with-edges"
    lab = await opened(fake, monkeypatch)
    assert await lab.pick_writable_profile(Name.QOS) == 20


async def test_a_target_that_is_not_marked_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
) -> None:
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)  # edge 5: marked, not for modules
    insert = InsertQosConfigurationModule(configuration_id=10, data=copy.deepcopy(QOS))
    remedy = (
        "put pyvelo-it-replace-modules beside pyvelo-it-target in the description "
        "of an edge the suite may delete and insert modules of"
    )
    async with live.restoring(site):
        with pytest.raises(live.WritesRefusedError) as refused:
            await live.delete_module(site, 1)
        assert str(refused.value) == (
            "a delete of a QOS module of an edge that is not marked "
            f"pyvelo-it-replace-modules; nothing was sent; {remedy}"
        )
        with pytest.raises(live.WritesRefusedError) as refused:
            _ = await lab.client.insert_configuration_module(insert)
        assert str(refused.value) == (
            "an insert of a QOS module of an edge that is not marked "
            f"pyvelo-it-replace-modules; nothing was sent; {remedy}"
        )
    assert set(fake.store.rows) == {1, 2, 3}
    assert "deleteConfigurationModule" not in fake.methods()


async def test_a_module_that_takes_its_own_data_is_writable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    assert await live.own_data_refusal(site) is None
    assert await live.accepts_a_write(site)


async def test_a_validation_refusal_is_its_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = lab_fake()
    fake.refused.add(1)
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    assert await live.own_data_refusal(site) == "DS00000001 lan.networks"
    assert not await live.accepts_a_write(site)


async def test_any_other_error_of_the_write_is_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    assert not isinstance(caught.value, live.DataRefusedError)
    with pytest.raises(VcoOperationError):
        _ = await lab.pick_writable_edge(Name.QOS)


async def test_marked_targets_that_all_refuse_fail_the_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    with pytest.raises(pytest.fail.Exception, match=r"^none of the 1 marked profile"):
        _ = await lab.pick_writable_profile(Name.QOS)


async def test_the_next_marked_edge_is_tried(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = lab_fake()
    fake.add_edge(7, 12, 20, description=MARKED)
    fake.module(4, 12, "QOS", QOS)
    fake.refused.add(1)
    lab = await opened(fake, monkeypatch)
    assert (await lab.pick_writable_edge(Name.QOS)).edge_id == 7
    first = await lab.first_writable(lab.targets, Name.QOS)
    assert first is not None
    assert first.edge_id == 7


async def test_a_refusal_the_orchestrator_states_is_a_data_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = lab_fake()
    fake.refused.add(1)
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    async with live.restoring(site):
        with pytest.raises(live.DataRefusedError) as caught:
            await raise_limit(site, 2)
    assert caught.value.error == "DS00000001 lan.networks"
    assert "secret-lab-name" not in str(caught.value)
    assert live.refusal_codes(caught.value) == "DS00000001 lan.networks"


async def test_edges_that_all_refuse_fail_a_pick_among_given_layouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    assert taken is not None
    assert taken.edge_id == 5


async def test_backups_left_are_listed_once(
    backups: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    lines: list[str] = []
    async with live.restoring(lab.edge_site(lab.layouts[0], Name.QOS)):
        pass
    live.report_leftover_backups(lines.append)
    assert lines == []  # a module that was put back leaves nothing to list

    site = lab.profile_site(20, Name.QOS)
    with pytest.raises(pytest.fail.Exception):  # noqa: PT012  # restoring raises as it exits, so the block holds the run it restores
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
) -> None:
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    with pytest.raises(pytest.fail.Exception) as caught:  # noqa: PT012  # restoring raises as it exits, so the block holds the run it restores
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
) -> None:
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
) -> None:
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
) -> None:
    fake = lab_fake()
    fake.store.return_data = False  # it answers a write with a row count
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    async with live.restoring(site):
        await raise_limit(site, 2)
    assert fake.data(2) == QOS


async def test_after_a_write_whose_answer_was_lost_the_original_goes_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
) -> None:
    fake = lab_fake()
    fake.add_edge(7, 12, 20, description=REPLACEABLE)
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[2], Name.QOS)
    with pytest.raises(pytest.fail.Exception) as caught:  # noqa: PT012  # restoring raises as it exits, so the block holds the run it restores
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
) -> None:
    fake = lab_fake()
    fake.edges[5]["description"] = REPLACEABLE
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    async with live.restoring(site):
        await live.delete_module(site, 1)
        assert await site.read() is None
    back = await site.read_present()
    assert back.id_ != 1
    assert back.raw_data == QOS


async def test_a_module_someone_else_deleted_is_not_put_back(
    backups: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
) -> None:
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


def test_interrupts_are_held_and_then_delivered(
    capsys: pytest.CaptureFixture[str],
) -> None:
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
) -> None:
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    with pytest.raises(pytest.fail.Exception) as caught:  # noqa: PT012  # restoring raises as it exits, so the block holds the run it restores
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
) -> None:
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    with pytest.raises(pytest.fail.Exception, match="changed outside this run"):  # noqa: PT012  # restoring raises as it exits, so the block holds the run it restores
        async with live.restoring(site):
            await raise_limit(site, 2)
            fake.store.other_writer(2, lambda row: row["data"].update(added=1))
            fake.refused.add(2)  # a stated validation refusal stores nothing
            with pytest.raises(live.DataRefusedError):
                await raise_limit(site, 3)
            fake.refused.discard(2)
    assert fake.data(2)["added"] == 1
    assert fake.data(2)["serviceRateLimit"]["value"] == 2
    assert fake.methods().count("updateConfigurationModule") == 2  # none after it


async def test_a_write_that_failed_before_it_was_sent_leaves_what_the_run_last_saw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    site = lab.profile_site(20, Name.QOS)
    with pytest.raises(pytest.fail.Exception, match="changed outside this run"):  # noqa: PT012  # restoring raises as it exits, so the block holds the run it restores
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
) -> None:
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
) -> None:
    fake = lab_fake()
    fake.edges[5]["description"] = REPLACEABLE
    lab = await opened(fake, monkeypatch)
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    async with live.restoring(site):
        with pytest.raises(
            live.WritesRefusedError, match=r"^a delete of a QOS module "
        ):
            await live.delete_module(site, 3)
    assert 3 in fake.store.rows


def answer(status: int, document: object) -> live.Answer:
    return live.Answer(
        "configuration/updateConfigurationModule", status, json.dumps(document).encode()
    )


PRECONDITION = {
    "error": {
        "code": -32003,
        "message": "validationError",
        "data": {
            "error": [{"code": PRECONDITION_FAILED, "message": "secret-lab-name"}]
        },
    }
}


def test_the_form_of_the_answer_to_a_stale_version() -> None:
    form = live.answer_form
    assert form(answer(200, PRECONDITION)) == (
        "refused with the precondition error (HTTP 200)"
    )
    assert form(answer(412, PRECONDITION)) == (
        "refused with the precondition error (HTTP 412)"
    )
    other = {"error": {"code": -32603, "message": "secret-lab-name"}}
    assert form(answer(200, other)) == "refused with another error (HTTP 200)"
    row = {"result": make_module_json("firewall", {"segments": []})}
    assert form(answer(200, row)) == "not refused: the module row (HTTP 200)"
    assert form(answer(200, {"result": {"rows": 0}})) == (
        "not refused: a row count of 0 (HTTP 200)"
    )
    assert form(live.Answer("m", 502, b"<html>")) == (
        "an answer that is not JSON (HTTP 502)"
    )
    assert (
        form(answer(200, {"result": None})) == "an answer of another shape (HTTP 200)"
    )


def test_the_form_of_an_answer_shows_no_name_of_the_lab() -> None:
    """The body of an answer holds the lab's names and IDs: only its form is shown."""
    named = {"name": "secret-lab-name", "id": 4711}
    bodies = [
        answer(200, PRECONDITION),
        answer(200, {"error": {"code": -1, "message": "secret-lab-name"}}),
        answer(200, {"result": {"data": named, "rows": 3, **named}}),
        answer(200, {"result": {"rows": "secret-lab-name"}}),
        answer(200, {"result": named}),
        answer(200, named),
        live.Answer("m", 502, b"secret-lab-name is down"),
        live.Answer("m", 200, b"\xff secret-lab-name"),
    ]
    for body in bodies:
        shown = live.answer_form(body)
        assert "secret-lab-name" not in shown
        assert "4711" not in shown
        assert "secret-lab-name" not in repr(body)
    assert live.answer_form(bodies[3]) == "an answer of another shape (HTTP 200)"


@pytest.mark.parametrize(
    ("knobs", "form"),
    [
        ({"conditional_check": True}, "refused with the precondition error (HTTP 200)"),
        ({}, "not refused: the module row (HTTP 200)"),
        ({"return_data": False}, "not refused: a row count of 0 (HTTP 200)"),
    ],
)
async def test_answers_are_recorded_with_their_status(
    monkeypatch: pytest.MonkeyPatch, knobs: dict[str, bool], form: str
) -> None:
    module = firewall_disabled()
    store = ModuleStore(**knobs)
    store.add_configuration({"id": 10, "name": "Profile 10"})
    store.rows[1] = make_module_json(
        "firewall", copy.deepcopy(FIREWALL), configuration_id=10
    )
    store.other_writer(1, lambda row: row["data"].update(future=2))
    client = client_over(store)
    answers = live.record_answers(client, monkeypatch)
    with pytest.raises(ModuleWriteError):
        _ = await client.write_module(module, plan_module(module))
    assert answers[0].method == "configuration/updateConfigurationModule"
    assert live.answer_form(answers[0]) == form


async def test_a_refusal_with_an_http_error_status_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(_: httpx.Request) -> httpx.Response:
        return httpx.Response(412, json={"jsonrpc": "2.0", "id": 1, **PRECONDITION})

    client = client_over(refuse)
    answers = live.record_answers(client, monkeypatch)
    request = UpdateConfigurationModule(
        configuration_module_id=1,
        version="v1",
        update=ConfigurationModuleUpdate(data={}),
        return_data=True,
    )
    with pytest.raises(ConfigConflictError):
        _ = await client.update_configuration_module(request)
    (only,) = answers
    assert live.answer_form(only) == "refused with the precondition error (HTTP 412)"


async def test_without_return_data_the_module_is_read_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = firewall_disabled()
    store = ModuleStore()
    store.add_configuration({"id": 10, "name": "Profile 10"})
    store.rows[1] = make_module_json(
        "firewall", copy.deepcopy(FIREWALL), configuration_id=10
    )
    client = client_over(store)
    live.without_return_data(client, monkeypatch)
    answers = live.record_answers(client, monkeypatch)
    write = await client.write_module(module, plan_module(module))
    assert [method.rsplit("/", 1)[-1] for method, _ in store.requests] == [
        "updateConfigurationModule",
        "getConfiguration",
    ]
    assert "returnData" not in store.requests[0][1]
    assert live.read_backs(answers) == 1
    assert write.module.raw_data == store.rows[1]["data"]


async def test_the_helpers_sit_behind_the_write_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write the guard refuses is neither sent, recorded nor counted.

    One it passes is recorded and counted once.
    """
    fake = lab_fake()
    lab = await opened(fake, monkeypatch)
    run = live.run_of(lab.client)
    live.without_return_data(lab.client, monkeypatch)
    answers = live.record_answers(lab.client, monkeypatch)
    fake.requests.clear()
    request = qos_update(1)
    request.return_data = True
    with pytest.raises(live.WritesRefusedError) as refused:
        _ = await lab.client.update_configuration_module(request)
    assert str(refused.value) == live.OUTSIDE_RESTORING
    assert request.return_data is MISSING  # the wrapper ran, the guard refused
    assert fake.requests == []
    assert answers == []
    assert run.sent == 0
    site = lab.edge_site(lab.layouts[0], Name.QOS)
    async with live.restoring(site):
        sent = run.sent
        answers.clear()
        await raise_limit(site, 2)
        assert run.sent == sent + 1
        updates = [
            a for a in answers if a.method.endswith("/updateConfigurationModule")
        ]
        assert [live.answer_form(a) for a in updates] == [
            "not refused: a row count of 1 (HTTP 200)"
        ]
        assert live.read_backs(answers) == 0
    assert fake.data(1) == QOS


EDGE_HUB_LOGICAL_ID = "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1"
CLUSTER_LOGICAL_ID = "c1c1c1c1-c1c1-4c1c-8c1c-c1c1c1c1c1c1"
OTHER_LOGICAL_ID = "b2b2b2b2-b2b2-4b2b-8b2b-b2b2b2b2b2b2"
EDGE_HUB_ID, CLUSTER_ID, OTHER_EDGE_ID = 31, 41, 32
DS_MODULE = 4
"""The device settings module of profile 20 in ``ds_lab``."""


def hub_lab() -> FakeLab:
    """``ds_lab`` with an Edge, a cluster and another Edge.

    The Global Segment's object is known to the store.
    """
    fake = ds_lab()
    fake.store.add_object(
        SEG0_OBJECT,
        logical_id=SEG0,
        name="Global Segment",
        type_="segment",
        data={"segmentId": 0},
    )
    fake.store.add_edge(EDGE_HUB_ID, logical_id=EDGE_HUB_LOGICAL_ID, name="Hub-A")
    fake.store.add_edge(OTHER_EDGE_ID, logical_id=OTHER_LOGICAL_ID, name="Hub-B")
    fake.store.add_cluster(CLUSTER_ID, logical_id=CLUSTER_LOGICAL_ID, name="Cluster-1")
    return fake


def role_requests(fake: FakeLab) -> list[str]:
    """The role calls the fake was sent, by portal method."""
    return [
        method
        for method, _ in fake.requests
        if method in {wire for wire, _ in live.ROLE_CALLS.values()}
    ]


def role_request(
    method: str,
    target_id: int,
    *,
    configuration_id: int = 20,
    module_id: int = DS_MODULE,
) -> Any:
    """The request of the client method *method* for *target_id*.

    It is on that profile and module.
    """
    module = (
        edge_schemas if live.ROLE_CALLS[method][1] == "edge" else enterprise_schemas
    )
    name = "".join(part.capitalize() for part in method.split("_"))
    return getattr(module, name.replace("Backhaul", "BackHaul"))(
        id_=target_id,
        configuration_id=configuration_id,
        module_id=module_id,
        segment_object_id=SEG0_OBJECT,
    )


async def hub_opened(
    fake: FakeLab,
    monkeypatch: pytest.MonkeyPatch,
    hub: str | None = EDGE_HUB_LOGICAL_ID,
) -> live.Lab:
    lab = await opened(fake, monkeypatch)
    if hub is not None:
        monkeypatch.setenv(live.HUB_ENV, hub)
    return lab


EDGE_ROLE_CALLS = [m for m, (_, kind) in live.ROLE_CALLS.items() if kind == "edge"]
CLUSTER_ROLE_CALLS = [
    m for m, (_, kind) in live.ROLE_CALLS.items() if kind == "cluster"
]


def test_the_role_calls_are_the_twelve_methods_of_the_client() -> None:
    assert len(live.ROLE_CALLS) == 12
    assert len(EDGE_ROLE_CALLS) == len(CLUSTER_ROLE_CALLS) == 6
    for method, (wire, kind) in live.ROLE_CALLS.items():
        assert callable(getattr(VcoClient, method))
        assert wire.startswith("edge/" if kind == "edge" else "enterprise/")
        assert not live._READ.match(wire.rsplit("/", 1)[-1])  # pyright: ignore[reportPrivateUsage]
        with pytest.raises(live.WritesRefusedError, match=r"^a call to "):
            live.check_call(wire)


@pytest.mark.parametrize("method", EDGE_ROLE_CALLS)
async def test_a_role_call_for_the_named_edge_on_a_marked_profile_is_sent(
    method: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = hub_lab()
    lab = await hub_opened(fake, monkeypatch)
    hub = await live.admit_hub(lab.client, None)
    assert (hub.kind, hub.id_) == ("edge", EDGE_HUB_ID)
    run = live.run_of(lab.client)
    site = lab.profile_site(20, DS)
    async with live.restoring(site):
        sent = run.sent
        _ = await getattr(lab.client, method)(role_request(method, EDGE_HUB_ID))
        assert run.sent == sent + 1
    assert role_requests(fake) == [live.ROLE_CALLS[method][0]]


@pytest.mark.parametrize("method", CLUSTER_ROLE_CALLS)
async def test_a_role_call_for_the_named_cluster_on_a_marked_profile_is_sent(
    method: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = hub_lab()
    lab = await hub_opened(fake, monkeypatch, CLUSTER_LOGICAL_ID)
    hub = await live.admit_hub(lab.client, None)
    assert (hub.kind, hub.id_) == ("cluster", CLUSTER_ID)
    site = lab.profile_site(20, DS)
    async with live.restoring(site):
        # A disable of a cluster that was never enabled is failed by the Orchestrator:
        # enable first, as a test would.
        _ = await lab.client.enable_cluster_hub(
            role_request("enable_cluster_hub", CLUSTER_ID)
        )
        _ = await getattr(lab.client, method)(role_request(method, CLUSTER_ID))
    assert role_requests(fake)[-1] == live.ROLE_CALLS[method][0]


@pytest.mark.parametrize("method", list(live.ROLE_CALLS))
async def test_a_role_call_is_refused_before_a_hub_is_admitted(
    method: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = hub_lab()
    lab = await hub_opened(fake, monkeypatch)
    kind = live.ROLE_CALLS[method][1]
    async with live.restoring(lab.profile_site(20, DS)):
        with pytest.raises(live.WritesRefusedError) as refused:
            _ = await getattr(lab.client, method)(
                role_request(method, EDGE_HUB_ID if kind == "edge" else CLUSTER_ID)
            )
    assert str(refused.value) == live.ROLE_CALL_NOT_ADMITTED
    assert role_requests(fake) == []


@pytest.mark.parametrize("method", list(live.ROLE_CALLS))
async def test_a_role_call_outside_a_block_is_refused(
    method: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = hub_lab()
    lab = await hub_opened(fake, monkeypatch)
    _ = await live.admit_hub(lab.client, None)
    target = EDGE_HUB_ID if live.ROLE_CALLS[method][1] == "edge" else CLUSTER_ID
    with pytest.raises(live.WritesRefusedError) as refused:
        _ = await getattr(lab.client, method)(role_request(method, target))
    assert str(refused.value) == live.OUTSIDE_RESTORING
    assert role_requests(fake) == []


async def test_a_role_call_for_a_profile_that_is_not_the_blocks_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = hub_lab()
    fake.add_profile(21)  # no mark: the suite never opens a block on it
    lab = await hub_opened(fake, monkeypatch)
    _ = await live.admit_hub(lab.client, None)
    async with live.restoring(lab.profile_site(20, DS)):
        for configuration_id, module_id in ((21, DS_MODULE), (10, DS_MODULE), (20, 3)):
            with pytest.raises(live.WritesRefusedError) as refused:
                _ = await lab.client.enable_edge_hub(
                    role_request(
                        "enable_edge_hub",
                        EDGE_HUB_ID,
                        configuration_id=configuration_id,
                        module_id=module_id,
                    )
                )
            assert str(refused.value) == live.ROLE_CALL_NOT_COVERED
    assert role_requests(fake) == []


async def test_a_role_call_in_a_block_on_a_profile_that_is_not_a_target_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Everything else holds (the opt-in, the hub, a profile, its device settings
    # module, the profile and the module the call names): only the mark differs.
    lab = await hub_opened(hub_lab(), monkeypatch)
    hub = live.HubTarget("edge", EDGE_HUB_ID, uuid.UUID(EDGE_HUB_LOGICAL_ID))

    def run_with(*, target: bool) -> live.LiveRun:
        site = live.profile_site(lab.client, 20, DS, None, target=target)
        return live.LiveRun(
            access=live.WriteAccess(granted=True, refusal=""),
            hub=hub,
            open=[(site, DS_MODULE)],
        )

    with live.writer_test(marked=True):
        run_with(target=True).check_role_call("edge", 20, DS_MODULE, EDGE_HUB_ID)
        with pytest.raises(live.WritesRefusedError) as refused:
            run_with(target=False).check_role_call("edge", 20, DS_MODULE, EDGE_HUB_ID)
    assert str(refused.value) == live.ROLE_CALL_NOT_COVERED


async def test_a_role_call_in_a_block_on_another_module_or_an_edge_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = hub_lab()
    lab = await hub_opened(fake, monkeypatch)
    _ = await live.admit_hub(lab.client, None)
    for site in (
        lab.profile_site(20, Name.QOS),
        lab.edge_site(lab.layouts[0], Name.QOS),
    ):
        async with live.restoring(site):
            for configuration_id, module_id in ((20, DS_MODULE), (10, DS_MODULE)):
                with pytest.raises(live.WritesRefusedError) as refused:
                    _ = await lab.client.enable_edge_hub(
                        role_request(
                            "enable_edge_hub",
                            EDGE_HUB_ID,
                            configuration_id=configuration_id,
                            module_id=module_id,
                        )
                    )
                assert str(refused.value) == live.ROLE_CALL_NOT_COVERED
    assert role_requests(fake) == []


@pytest.mark.parametrize(
    ("method", "target"),
    [
        ("enable_edge_hub", OTHER_EDGE_ID),  # another Edge
        ("disable_edge_backhaul", OTHER_EDGE_ID),
        ("enable_cluster_hub", EDGE_HUB_ID),  # the Edge's ID, for a cluster call
        ("enable_cluster_hub", CLUSTER_ID),  # a cluster, where an Edge is named
        ("enable_edge_hub", CLUSTER_ID),
    ],
)
async def test_a_role_call_for_another_hub_is_refused(
    method: str, target: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = hub_lab()
    lab = await hub_opened(fake, monkeypatch)
    _ = await live.admit_hub(lab.client, None)
    async with live.restoring(lab.profile_site(20, DS)):
        with pytest.raises(live.WritesRefusedError) as refused:
            _ = await getattr(lab.client, method)(role_request(method, target))
    assert str(refused.value) == live.ROLE_CALL_NOT_THE_HUB
    assert role_requests(fake) == []


async def test_a_role_call_of_a_test_that_is_not_marked_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = hub_lab()
    lab = await hub_opened(fake, monkeypatch)
    _ = await live.admit_hub(lab.client, None)
    async with live.restoring(lab.profile_site(20, DS)):
        with (
            live.writer_test(marked=False),
            pytest.raises(live.WritesRefusedError) as refused,
        ):
            _ = await lab.client.enable_edge_hub(
                role_request("enable_edge_hub", EDGE_HUB_ID)
            )
    assert str(refused.value) == live.NOT_MARKED
    assert role_requests(fake) == []


async def test_a_role_call_without_the_opt_in_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = hub_lab()
    monkeypatch.setenv(live.HUB_ENV, EDGE_HUB_LOGICAL_ID)
    lab = await live.discover_lab(client_over(fake), None)
    with pytest.raises(live.WritesRefusedError, match="live write tests are off"):
        _ = await lab.client.enable_edge_hub(
            role_request("enable_edge_hub", EDGE_HUB_ID)
        )
    with pytest.raises(pytest.skip.Exception, match="live write tests are off"):
        _ = await live.admit_hub(lab.client, None)
    assert role_requests(fake) == []


async def test_a_refused_role_call_carries_its_codes_and_not_the_names_in_its_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = hub_lab()
    fake.refused_methods.add("edge/enableEdgeForEdgeHub")
    lab = await hub_opened(fake, monkeypatch)
    _ = await live.admit_hub(lab.client, None)
    async with live.restoring(lab.profile_site(20, DS)):
        with pytest.raises(VcoOperationError) as caught:
            _ = await lab.client.enable_edge_hub(
                role_request("enable_edge_hub", EDGE_HUB_ID)
            )
    assert live.refusal_codes(caught.value) == "DS00000001 lan.networks"
    assert "secret-lab-name" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


async def test_the_role_calls_are_still_refused_when_sent_by_hand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = hub_lab()
    lab = await hub_opened(fake, monkeypatch)
    _ = await live.admit_hub(lab.client, None)
    async with live.restoring(lab.profile_site(20, DS)):
        with pytest.raises(
            live.WritesRefusedError, match=r"^a call to edge/enableEdge"
        ):
            _ = await lab.client.portal(
                "edge/enableEdgeForEdgeHub",
                role_request("enable_edge_hub", EDGE_HUB_ID),
                RowsModifiedConfirmation,
            )
    assert role_requests(fake) == []


class TestAdmitHub:
    async def test_an_unset_variable_skips_and_says_what_to_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lab = await hub_opened(hub_lab(), monkeypatch, None)
        monkeypatch.delenv(live.HUB_ENV, raising=False)
        with pytest.raises(pytest.skip.Exception, match=live.HUB_ENV):
            _ = await live.admit_hub(lab.client, None)
        assert live.run_of(lab.client).hub is None

    async def test_a_value_that_is_no_logical_id_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lab = await hub_opened(hub_lab(), monkeypatch, "Hub-A")
        with pytest.raises(pytest.fail.Exception) as caught:
            _ = await live.admit_hub(lab.client, None)
        assert str(caught.value) == live.HUB_NOT_A_LOGICAL_ID
        assert "Hub-A" not in str(caught.value)

    async def test_an_id_that_names_nothing_fails_without_quoting_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        unknown = "d4d4d4d4-d4d4-4d4d-8d4d-d4d4d4d4d4d4"
        lab = await hub_opened(hub_lab(), monkeypatch, unknown)
        with pytest.raises(pytest.fail.Exception) as caught:
            _ = await live.admit_hub(lab.client, None)
        assert str(caught.value) == live.HUB_NOT_FOUND
        assert unknown not in str(caught.value)
        assert live.run_of(lab.client).hub is None

    async def test_a_member_of_a_cluster_is_no_hub(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        member = "e5e5e5e5-e5e5-4e5e-8e5e-e5e5e5e5e5e5"
        fake = hub_lab()
        fake.store.add_edge(33, logical_id=member, name="Member-1", cluster=CLUSTER_ID)
        lab = await hub_opened(fake, monkeypatch, member)
        with pytest.raises(pytest.fail.Exception) as caught:
            _ = await live.admit_hub(lab.client, None)
        assert str(caught.value) == live.HUB_NOT_FOUND

    async def test_the_hub_is_found_by_logical_id_in_any_case(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lab = await hub_opened(hub_lab(), monkeypatch, EDGE_HUB_LOGICAL_ID.upper())
        hub = await live.admit_hub(lab.client, None)
        assert hub == live.HubTarget(
            "edge", EDGE_HUB_ID, uuid.UUID(EDGE_HUB_LOGICAL_ID)
        )
        assert live.run_of(lab.client).hub == hub

    async def test_the_opt_in_is_checked_before_the_hub(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(live.WRITE_HOST_ENV, "other.example.invalid")
        monkeypatch.setenv(live.WRITE_ENTERPRISE_ENV, str(ENTERPRISE))
        monkeypatch.setenv(live.HUB_ENV, "not a logical id")
        lab = await live.discover_lab(client_over(hub_lab()), None)
        with pytest.raises(pytest.fail.Exception, match=r"^writes refused: "):
            _ = await live.admit_hub(lab.client, None)
