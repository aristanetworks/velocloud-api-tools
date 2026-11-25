"""``load_config`` and ``confirm_save``: what they do with bad input."""

import io
from pathlib import Path
from typing import ClassVar

import pytest
import yaml
from pydantic import BaseModel, ConfigDict
from pyvelo.config import ConfigInputError, confirm_save, load_config
from test_state_plan import (  # pyright: ignore[reportImplicitRelativeImport]
    make_state,
)


class Intent(BaseModel):
    edge: str
    enabled: bool = True
    ports: list[int] = []


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "intent.yaml"
    _ = path.write_text(text)
    return path


def test_load_config_reads_a_valid_file(tmp_path: Path):
    path = write(tmp_path, "edge: e1\nports: [22, 80]\n")
    assert load_config(path, Intent) == Intent(edge="e1", ports=[22, 80])


def test_load_config_refuses_a_key_given_twice(tmp_path: Path):
    path = write(tmp_path, "edge: e1\nports: [22]\nedge: e2\n")
    with pytest.raises(ConfigInputError) as exc:
        _ = load_config(path, Intent)
    assert str(exc.value).startswith(f"{path}: not valid YAML: ")
    assert "the key 'edge' is given twice" in str(exc.value)
    assert "line 3" in str(exc.value)


def test_load_config_refuses_a_nested_key_given_twice(tmp_path: Path):
    class Outer(BaseModel):
        inner: dict[str, int]

    path = write(tmp_path, "inner:\n  a: 1\n  a: 2\n")
    with pytest.raises(ConfigInputError, match="the key 'a' is given twice"):
        _ = load_config(path, Outer)


class Loose(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")


@pytest.mark.parametrize(
    "text",
    [
        "base: &b {k: 1, j: 0}\nuse: {<<: *b, k: 2}\n",
        "c: &c {k: 1}\nfirst: {inner: &b {<<: *c, k: 2}}\nsecond: {<<: *b}\n",
    ],
)
def test_load_config_accepts_a_key_that_overrides_a_merged_key(
    tmp_path: Path, text: str
):
    loaded = load_config(write(tmp_path, text), Loose)
    assert loaded.model_dump() == yaml.safe_load(text)


def test_load_config_refuses_a_key_given_twice_beside_a_merge(tmp_path: Path):
    path = write(tmp_path, "base: &b {k: 1}\nuse:\n  <<: *b\n  j: 1\n  j: 2\n")
    with pytest.raises(ConfigInputError, match="the key 'j' is given twice"):
        _ = load_config(path, Loose)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("edge: [unclosed\n", "not valid YAML"),
        ("edge: e1\n---\nedge: e2\n", "not valid YAML"),
        ("", "1 invalid value(s) for Intent: (top level): "),
        ("- a\n- b\n", "1 invalid value(s) for Intent: (top level): "),
        ("edge: e1\nports: [x]\n", "1 invalid value(s) for Intent: ports.0: "),
    ],
)
def test_load_config_raises_config_input_error(tmp_path: Path, text: str, message: str):
    path = write(tmp_path, text)
    with pytest.raises(ConfigInputError) as exc:
        _ = load_config(path, Intent)
    assert str(exc.value).startswith(f"{path}: ")
    assert message in str(exc.value)
    assert exc.value.__cause__ is not None


def test_load_config_does_not_quote_the_values_it_refuses(tmp_path: Path):
    path = write(tmp_path, "edge: e1\nports: [hunter2]\n")
    with pytest.raises(ConfigInputError) as exc:
        _ = load_config(path, Intent)
    assert "hunter2" not in str(exc.value)


def test_load_config_leaves_a_missing_file_to_the_os_error(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        _ = load_config(tmp_path / "absent.yaml", Intent)


@pytest.mark.parametrize(
    ("answer", "confirmed"),
    [("y\n", True), (" YES \n", True), ("\n", False), ("n\n", False), ("yep\n", False)],
)
def test_confirm_save_reads_an_answer(
    monkeypatch: pytest.MonkeyPatch, answer: str, confirmed: bool
):
    monkeypatch.setattr("sys.stdin", io.StringIO(answer))
    assert confirm_save() is confirmed


def test_confirm_save_without_input_is_not_confirmed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # closed, or not a terminal
    assert confirm_save() is False


def test_confirm_save_with_no_stdin_is_not_confirmed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("sys.stdin", None)  # a process started with no stdin
    assert confirm_save() is False


def test_confirm_save_takes_only_a_bool_for_auto_yes(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    assert confirm_save(auto_yes=True) is True
    with pytest.raises(TypeError, match="auto_yes must be True or False, not 'no'"):
        _ = confirm_save(auto_yes="no")  # pyright: ignore[reportArgumentType]


def test_confirm_save_shows_the_plan_it_is_given(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    state = make_state()
    state.edge.firewall.firewall_enabled.set_local(False)
    plan = state.plan()
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))

    assert confirm_save(plan=plan) is True

    shown = capsys.readouterr().out
    assert shown.startswith(plan.render() + "\n")
    assert shown.endswith("Apply changes? [y/N]: ")
    assert confirm_save(auto_yes=True, plan=plan) is True
    assert capsys.readouterr().out == plan.render() + "\n"
