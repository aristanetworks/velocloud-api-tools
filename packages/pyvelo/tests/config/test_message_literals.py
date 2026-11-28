"""No text written in the source names a field by the key the VCO writes it under.

``test_shown_paths_round_trip`` reads the texts pyvelo writes over its fixtures.
This reads the source instead: every string literal that reads as prose, in
every module of the source (the models and the schemas too), with the same
matcher. It
finds a wire key, or the VCO's name of a module, in a message no fixture
renders.

What it reads: each string literal and each f-string that holds a space, with
every formatted value of an f-string taken out; a literal inside a formatted
value is a text of its own. Literals the parser joins (``"a " "b"``) are one
text.

What it does not read: a docstring; a literal with no space, which is data (a
ref name, a key used to read raw JSON, a method name); a type given to
``cast`` as text.

What it cannot see: a name a message takes from a variable or a call at run
time (the alias of a field, a key of a dump), and a message built from words
with no space in them. Those are covered only where a fixture renders them.
"""

import ast
import pathlib
from collections.abc import Iterator
from typing import Final

import pyvelo
from test_shown_paths_round_trip import (  # pyright: ignore[reportImplicitRelativeImport]
    forbidden_names,
    vco_names,
)

SOURCE: Final = pathlib.Path(pyvelo.__file__).parent
ALLOWED: Final[dict[tuple[str, str], str]] = {
    (
        "client/schemas/metrics.py",
        "Sort by a display column",
    ): "a field's description, which lists the VCO's own sort values",
    (
        "client/schemas/monitoring.py",
        "WAN link internal identifier",
    ): "WAN is the word here, not the module",
    (
        "client/exceptions.py",
        "configuration/updateConfigurationModule with",
    ): "the parameters of a request to the VCO, which is sent its own keys",
    (
        "client/websocket.py",
        "logicalId=%s",
    ): "a log line of the remote diagnostics protocol, in the protocol's words",
    (
        "config/hub_steps.py",
        "`deviceSettings:segment` row",
    ): "a ref name, which is data and is never renamed",
    (
        "core/effective.py",
        "Annotated[list[Seg[L]]",
    ): "a line of Python for the author of a model: `list[` is a type there",
}
"""A file and a piece of one text of it that may hold a name of the VCO's, and why."""


def _is_cast(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Name) and func.id == "cast") or (
        isinstance(func, ast.Attribute) and func.attr == "cast"
    )


def literals(source: str) -> Iterator[tuple[int, str]]:
    """Each text of *source* that reads as prose, with the line it starts on.

    An f-string is its literal parts, with a NUL where a value is formatted
    in, so that a key is not joined to what stands beside the value.
    """
    tree = ast.parse(source)
    skipped: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            skipped.add(id(node.value))
        elif _is_cast(node) and isinstance(node, ast.Call) and node.args:
            skipped.add(id(node.args[0]))
        elif isinstance(node, ast.JoinedStr):
            skipped.update(id(part) for part in node.values)
    for node in ast.walk(tree):
        if id(node) in skipped:
            continue
        if isinstance(node, ast.JoinedStr):
            text = "".join(
                part.value
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
                else "\x00"
                for part in node.values
            )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        else:
            continue
        if " " in text:
            yield node.lineno, text


def modules() -> list[pathlib.Path]:
    """The source modules whose literals are read."""
    return sorted(SOURCE.rglob("*.py"))


def test_the_literals_read_are_the_prose_of_a_module() -> None:
    source = '''
"""A docstring names `segmentId` as it likes."""
KEY = "backHaulEdges"
ROWS = cast("list[dict[str, Any]]", KEY)
def refuse(name: str, key: str) -> None:
    """So does this one: {"logicalId": 1}."""
    raise ValueError(
        f"{name}: give the `segmentId` of the segment, "
        "as in {\\"logicalId\\": 1}, and leave "
        f"{key}Id alone"
    )
'''
    ((line, text),) = literals(source)

    assert line == 8
    assert text == (
        "\x00: give the `segmentId` of the segment, "
        'as in {"logicalId": 1}, and leave \x00Id alone'
    )
    assert vco_names(text, forbidden_names()) == ["segmentId", "logicalId"]


def test_a_literal_inside_a_formatted_value_is_read_on_its_own() -> None:
    source = """
def refuse(names: list[str]) -> None:
    raise ValueError(f"{'/'.join(names) or 'no segmentId at all'}: give one of them")
"""
    assert [text for _, text in literals(source)] == [
        "\x00: give one of them",
        "no segmentId at all",
    ]


def test_the_models_and_the_schemas_are_read() -> None:
    read = modules()
    assert SOURCE / "client" / "schemas" / "edge.py" in read
    assert (
        SOURCE / "client" / "models" / "configuration" / "profile_segments.py" in read
    )


def test_no_message_literal_holds_a_vco_module_name_or_a_wire_key() -> None:
    forbidden = forbidden_names()
    read = modules()
    assert len(read) > 100
    assert SOURCE / "config" / "pipeline.py" in read
    assert SOURCE / "core" / "merge.py" in read

    found: dict[str, list[str]] = {}
    used: set[tuple[str, str]] = set()
    for path in read:
        name = path.relative_to(SOURCE).as_posix()
        for line, text in literals(path.read_text()):
            names = vco_names(text, forbidden)
            if not names:
                continue
            allowed = {key for key in ALLOWED if key[0] == name and key[1] in text}
            used |= allowed
            if not allowed:
                found[f"{name}:{line}"] = names

    assert found == {}
    # Every exception is still needed: one that matches nothing is taken out.
    assert used == set(ALLOWED)
