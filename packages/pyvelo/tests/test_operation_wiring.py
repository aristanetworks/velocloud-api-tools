"""Introspection-based wiring tests for all VCO client operations.

Auto-discovers all operation methods from VcoClientProtocol's mixin hierarchy
and verifies they follow expected signature patterns. These tests expand
automatically as new operations are added — no manual test registration needed.
"""

import ast
import inspect
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
import pyvelo.client.operations as ops_pkg
from pyvelo.client.protocol import VcoClientProtocol

# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

_MIXIN_CLASSES: list[type] = [
    cls for cls in inspect.getmro(VcoClientProtocol) if cls.__name__.endswith("Mixin")
]


def _discover_operations() -> list[tuple[str, type, str]]:
    """Yield (method_name, mixin_class, qualified_name) for all operations."""
    ops = []
    for mixin in _MIXIN_CLASSES:
        for name in sorted(vars(mixin)):
            if name.startswith("_"):
                continue
            attr = getattr(mixin, name)
            if callable(attr):
                ops.append((name, mixin, f"{mixin.__name__}.{name}"))
    return ops


_ALL_OPERATIONS = _discover_operations()


def _operation_signature(method) -> inspect.Signature:
    # Python 3.14 can return forward references without evaluating their targets.
    if hasattr(inspect, "Format"):
        inspect_module = cast(Any, inspect)
        return inspect.signature(
            method, annotation_format=inspect_module.Format.FORWARDREF
        )
    return inspect.signature(method)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOperationDiscovery:
    def test_mixins_found(self):
        """VcoClientProtocol should compose multiple operation mixins."""
        assert len(_MIXIN_CLASSES) > 20, (
            f"Expected 20+ mixins, found {len(_MIXIN_CLASSES)}"
        )

    def test_operations_found(self):
        """Should discover a large number of operation methods."""
        assert len(_ALL_OPERATIONS) > 200, (
            f"Expected 200+ operations, found {len(_ALL_OPERATIONS)}"
        )


class TestSignatureConformance:
    # Factory methods that return context managers rather than coroutines.
    _SYNC_FACTORIES: ClassVar[set[str]] = {"RemoteDiagnosticsMixin.websocket"}

    @pytest.mark.parametrize(
        "method_name,mixin,qualified",
        _ALL_OPERATIONS,
        ids=[q for _, _, q in _ALL_OPERATIONS],
    )
    def test_operation_is_async(self, method_name, mixin, qualified):
        """All operations should be async (except known sync factories)."""
        if qualified in self._SYNC_FACTORIES:
            pytest.skip(f"{qualified} is a sync factory, not a coroutine")
        method = getattr(mixin, method_name)
        assert inspect.iscoroutinefunction(method), f"{qualified} is not async"

    @pytest.mark.parametrize(
        "method_name,mixin,qualified",
        _ALL_OPERATIONS,
        ids=[q for _, _, q in _ALL_OPERATIONS],
    )
    def test_operation_has_return_annotation(self, method_name, mixin, qualified):
        """All operations should declare a return type."""
        method = getattr(mixin, method_name)
        sig = _operation_signature(method)
        assert sig.return_annotation is not inspect.Parameter.empty, (
            f"{qualified} has no return type annotation"
        )

    @pytest.mark.parametrize(
        "method_name,mixin,qualified",
        _ALL_OPERATIONS,
        ids=[q for _, _, q in _ALL_OPERATIONS],
    )
    def test_operation_has_self_parameter(self, method_name, mixin, qualified):
        """All operations should have 'self' as first parameter."""
        method = getattr(mixin, method_name)
        sig = _operation_signature(method)
        params = list(sig.parameters.keys())
        assert len(params) >= 1 and params[0] == "self", (
            f"{qualified} missing 'self' parameter"
        )


# ---------------------------------------------------------------------------
# AST-based portal/rest string extraction
# ---------------------------------------------------------------------------

_OPERATIONS_DIR = (
    Path(__file__).resolve().parent.parent / "src" / "pyvelo" / "client" / "operations"
)


def _extract_api_calls(source_path: Path) -> list[tuple[str, str, int]]:
    """Extract (call_type, method_string, lineno) from portal()/rest() calls.

    Parses AST to find literal strings passed as the first argument to
    self.portal(), self.portal_list(), self.rest(), or self.rest_list().
    """
    tree = ast.parse(source_path.read_text())
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in (
            "portal",
            "portal_list",
            "portal_paginated_list",
            "rest",
            "rest_list",
        ):
            continue

        # For portal: first arg is the method string
        # For rest/rest_list: first arg is HTTP method, second is the path
        if func.attr in ("portal", "portal_list", "portal_paginated_list"):
            if (
                node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                calls.append(("portal", node.args[0].value, node.lineno))
        elif (
            func.attr in ("rest", "rest_list")
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Constant)
        ):
            http_method = node.args[0].value
            # Path may be an f-string or constant
            if isinstance(node.args[1], ast.Constant) and isinstance(
                node.args[1].value, str
            ):
                calls.append(
                    ("rest", f"{http_method} {node.args[1].value}", node.lineno)
                )
            elif isinstance(node.args[1], ast.JoinedStr):
                calls.append(("rest", f"{http_method} <f-string>", node.lineno))
    return calls


def _collect_all_api_calls() -> list[tuple[str, str, str, int]]:
    """Collect all portal/rest calls across all operation files.

    Returns (file_stem, call_type, method_string, lineno).
    """
    results = []
    for py_file in sorted(_OPERATIONS_DIR.rglob("*.py")):
        if py_file.name.startswith("_"):
            continue
        for call_type, method_str, lineno in _extract_api_calls(py_file):
            rel = py_file.relative_to(_OPERATIONS_DIR)
            results.append((str(rel), call_type, method_str, lineno))
    return results


_ALL_API_CALLS = _collect_all_api_calls()


class TestPortalMethodNames:
    def test_api_calls_discovered(self):
        """Should discover portal/rest calls across operation files."""
        assert len(_ALL_API_CALLS) > 100, (
            f"Expected 100+ API calls, found {len(_ALL_API_CALLS)}"
        )

    @pytest.mark.parametrize(
        "file,call_type,method_str,lineno",
        [c for c in _ALL_API_CALLS if c[1] == "portal"],
        ids=[f"{c[0]}:{c[3]}" for c in _ALL_API_CALLS if c[1] == "portal"],
    )
    def test_portal_method_format(self, file, call_type, method_str, lineno):
        """Portal method strings should follow 'domain/methodName' format."""
        parts = method_str.split("/")
        assert len(parts) == 2, (
            f"{file}:{lineno}: portal method '{method_str}' should be 'domain/methodName'"
        )
        domain, method_name = parts
        assert domain.isalpha() and domain[0].islower(), (
            f"{file}:{lineno}: domain '{domain}' should be lowercase alpha"
        )
        assert method_name[0].islower(), (
            f"{file}:{lineno}: method name '{method_name}' should start lowercase"
        )

    @pytest.mark.parametrize(
        "file,call_type,method_str,lineno",
        [c for c in _ALL_API_CALLS if c[1] == "rest"],
        ids=[f"{c[0]}:{c[3]}" for c in _ALL_API_CALLS if c[1] == "rest"],
    )
    def test_rest_method_format(self, file, call_type, method_str, lineno):
        """REST calls should use valid HTTP methods."""
        http_method = method_str.split(" ", 1)[0]
        assert http_method in (
            "GET",
            "POST",
            "PUT",
            "DELETE",
            "PATCH",
        ), f"{file}:{lineno}: invalid HTTP method '{http_method}'"


class TestNoOrphanedMixins:
    def test_all_mixins_composed(self):
        """Every *Mixin class in operations/ should appear in VcoClientProtocol's MRO."""
        composed_names = {cls.__name__ for cls in _MIXIN_CLASSES}

        # Find all mixin classes defined in operation files via import
        ops_init = Path(ops_pkg.__file__)
        declared = set()
        for py_file in sorted(ops_init.parent.rglob("*.py")):
            if py_file.name.startswith("_"):
                continue
            tree = ast.parse(py_file.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name.endswith("Mixin"):
                    declared.add(node.name)

        orphaned = declared - composed_names
        assert not orphaned, (
            f"Mixin(s) defined but not composed into VcoClientProtocol: {orphaned}"
        )
