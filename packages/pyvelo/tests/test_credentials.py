"""Tests for get_vco_credentials()."""

import sys
from pathlib import Path

import pytest
from pyvelo.client import VcoCredentialsError, VcoError, get_vco_credentials


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VCO_HOST", raising=False)
    monkeypatch.delenv("VCO_TOKEN", raising=False)


def test_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VCO_HOST", "vco.example.invalid")
    monkeypatch.setenv("VCO_TOKEN", "secret")
    assert get_vco_credentials() == ("vco.example.invalid", "secret")


def test_loads_env_file_without_overriding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / "lab.env"
    _ = env_file.write_text("VCO_HOST=from-file.invalid\nVCO_TOKEN=file-token\n")
    monkeypatch.setenv("VCO_HOST", "from-env.invalid")
    assert get_vco_credentials(env_file) == ("from-env.invalid", "file-token")


def test_missing_env_file_raises(tmp_path: Path) -> None:
    with pytest.raises(VcoCredentialsError, match="Env file not found"):
        _ = get_vco_credentials(tmp_path / "absent.env")


SET_THEM = (
    " not set or empty. Set VCO_HOST to the Orchestrator's hostname and VCO_TOKEN "
    "to an API token, in the environment or in the env file given to "
    "get_vco_credentials()."
)


@pytest.mark.parametrize(
    ("present", "message"),
    [
        ({}, "VCO_HOST and VCO_TOKEN are" + SET_THEM),
        ({"VCO_HOST": "h"}, "VCO_TOKEN is" + SET_THEM),
        ({"VCO_TOKEN": "t"}, "VCO_HOST is" + SET_THEM),
        ({"VCO_HOST": "h", "VCO_TOKEN": ""}, "VCO_TOKEN is" + SET_THEM),
    ],
)
def test_missing_or_empty_vars_raise(
    monkeypatch: pytest.MonkeyPatch, present: dict[str, str], message: str
) -> None:
    for key, value in present.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(VcoCredentialsError) as exc:
        _ = get_vco_credentials()
    assert str(exc.value) == message


def test_is_a_vco_error() -> None:
    assert issubclass(VcoCredentialsError, VcoError)


def test_env_file_without_dotenv_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(sys.modules, "dotenv", None)  # simulate missing extra
    with pytest.raises(ModuleNotFoundError, match=r"pyvelo\[dotenv\]"):
        _ = get_vco_credentials(tmp_path / "lab.env")


def test_environment_only_does_not_need_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "dotenv", None)
    monkeypatch.setenv("VCO_HOST", "h")
    monkeypatch.setenv("VCO_TOKEN", "t")
    assert get_vco_credentials() == ("h", "t")
