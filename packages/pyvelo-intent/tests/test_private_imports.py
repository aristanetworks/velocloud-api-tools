"""No source file of the two packages writes ``from m import _name``, outside the three engines of ``pyvelo.core``.

It reads each file's imports and so does not see an attribute access such as
``module._x``, an ``import module`` followed by a use of its private name, or an
import made at run time.
"""

import ast
import pathlib

import pyvelo
import pyvelo_intent

ENGINES = ("merge.py", "rebase.py", "changes.py")
"""The modules of ``pyvelo.core`` that share helpers with ``base.py`` and each other."""


def private_imports(path: pathlib.Path) -> list[str]:
    """``file: name`` for each ``from module import _name`` in the file at *path*."""
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            found.extend(
                f"{path.name}: {alias.name}"
                for alias in node.names
                if alias.name.startswith("_") and not alias.name.startswith("__")
            )
    return found


def test_only_the_engines_of_the_core_import_private_names():
    core = pathlib.Path(pyvelo.__file__).parent / "core"
    files = [
        path
        for package in (pyvelo, pyvelo_intent)
        for path in sorted(pathlib.Path(package.__path__[0]).rglob("*.py"))
        if path not in [core / name for name in ENGINES]
    ]
    assert len(files) > 100
    assert [name for path in files for name in private_imports(path)] == []
