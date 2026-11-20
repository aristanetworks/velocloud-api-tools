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


@pytest.mark.parametrize("present", [{}, {"VCO_HOST": "h"}, {"VCO_TOKEN": "t"}])
def test_missing_or_empty_vars_raise(
    monkeypatch: pytest.MonkeyPatch, present: dict[str, str]
) -> None:
    for key, value in present.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(VcoCredentialsError, match="VCO_HOST and VCO_TOKEN"):
        _ = get_vco_credentials()


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
