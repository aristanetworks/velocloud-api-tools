"""Offline checks of the support code of the live save tests (tests/integration).

The live tests are deselected without ``-m integration`` and need an Orchestrator;
what they rely on to restore a lab and to keep its names out of their output is
checked here with no lab.
"""

import json
import pathlib
import re
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, ValidationError
from pyvelo.client import VcoOperationError
from pyvelo.client.enums.configuration import ConfigurationModuleName as Name
from pyvelo.client.schemas.configuration import FirewallConfigurationModule
from pyvelo.core.rebase import Key, Path
from pyvelo.testing import make_module_json

from .integration import _config_live as live

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

    async def update_configuration_module(self, request: object) -> None:
        raise VcoOperationError("update", "refused for secret-lab-name")


class RefusingClient(FakeClient):
    async def insert_configuration_module(self, request: object) -> None:
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


def test_the_two_copies_of_the_live_support_are_identical():
    """pyvelo-intent's live tests use a copy of the support file; they must not drift."""
    here = pathlib.Path(live.__file__).resolve()
    copy_of = (
        here.parents[3]
        / "pyvelo-intent"
        / "tests"
        / "integration"
        / "_intent_live_support.py"
    )
    if not copy_of.exists():
        pytest.skip("pyvelo-intent's tests are not part of this checkout")
    assert here.read_bytes() == copy_of.read_bytes(), "cp one over the other"
