"""Tests for compact serialization via model_dump(context={"compact": True})."""

import warnings
from typing import Annotated, Any

from pydantic import MISSING, Field
from pyvelo.core import Alias, Normalizer, VcoModel

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

_COMPACT = {"compact": True}


def _compact(model: VcoModel) -> dict[str, Any]:
    return model.model_dump(by_alias=False, context=_COMPACT)


# ---------------------------------------------------------------------------
# Test models
# ---------------------------------------------------------------------------


class Inner(VcoModel):
    x: int = 0
    y: str = "kept"


class Outer(VcoModel):
    source_ip: Annotated[str, Alias("sip")] = "any"
    hostname: str = ""
    dscp: int = -1
    name: str = "real_default"
    tags: list[str] = Field(default_factory=list)
    optional: str | MISSING = MISSING  # auto-dropped when MISSING
    inner: Inner = Field(default_factory=Inner)  # nested model
    inner_optional: Inner | MISSING = MISSING  # nested model that accepts MISSING


class HasList(VcoModel):
    items: list[Inner] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# MISSING auto-drop
# ---------------------------------------------------------------------------


class TestMissingDrop:
    def test_missing_field_omitted(self):
        m = Outer()
        d = _compact(m)
        assert "optional" not in d

    def test_present_field_kept(self):
        m = Outer(optional="hello")
        d = _compact(m)
        assert d["optional"] == "hello"


# ---------------------------------------------------------------------------
# Default-value drop
# ---------------------------------------------------------------------------


class TestDefaultValueDrop:
    def test_string_default_dropped(self):
        m = Outer()
        d = _compact(m)
        assert "source_ip" not in d
        assert "hostname" not in d

    def test_int_default_dropped(self):
        m = Outer()
        d = _compact(m)
        assert "dscp" not in d

    def test_non_default_scalar_values_kept(self):
        m = Outer(source_ip="10.0.0.1", hostname="edge1", dscp=46)
        d = _compact(m)
        assert d["source_ip"] == "10.0.0.1"
        assert d["hostname"] == "edge1"
        assert d["dscp"] == 46

    def test_static_default_dropped(self):
        """Fields at their static default are omitted."""
        m = Outer()
        d = _compact(m)
        assert "name" not in d

    def test_factory_default_dropped(self):
        """Fields at their default_factory output are omitted (e.g. empty list)."""
        m = Outer()
        d = _compact(m)
        assert "tags" not in d

    def test_non_default_static_and_factory_values_kept(self):
        m = Outer(name="my_rule", tags=["prod"])
        d = _compact(m)
        assert d["name"] == "my_rule"
        assert d["tags"] == ["prod"]

    def test_missing_typed_field_not_dropped_at_default(self):
        """MISSING-typed field with non-MISSING value is kept."""
        m = Outer(optional="hello")
        d = _compact(m)
        assert d["optional"] == "hello"

    def test_inner_model_at_default_dropped(self):
        """Nested model equal to default_factory output is omitted."""
        m = Outer()
        d = _compact(m)
        assert "inner" not in d

    def test_inner_model_non_default_kept(self):
        m = Outer(inner=Inner(x=5))
        d = _compact(m)
        assert "inner" in d


# ---------------------------------------------------------------------------
# Python names, not aliases
# ---------------------------------------------------------------------------


class TestPythonNames:
    def test_uses_python_names(self):
        m = Outer(source_ip="10.0.0.1")
        d = _compact(m)
        assert "source_ip" in d
        assert "sip" not in d


# ---------------------------------------------------------------------------
# Nested model compaction
# ---------------------------------------------------------------------------


class TestNestedModels:
    def test_nested_model_compacted(self):
        m = Outer(inner=Inner(x=5, y="hello"))
        d = _compact(m)
        assert d["inner"] == {"x": 5, "y": "hello"}

    def test_nested_all_defaults_dropped(self):
        """Inner at all defaults is omitted (equals default_factory output)."""
        m = Outer()
        d = _compact(m)
        assert "inner" not in d

    def test_nested_missing_typed_present_kept(self):
        """inner_optional accepts MISSING; when explicitly set, kept even at defaults."""
        m = Outer(inner_optional=Inner())
        d = _compact(m)
        assert "inner_optional" in d

    def test_nested_missing_typed_absent(self):
        """inner_optional=MISSING is dropped entirely."""
        m = Outer()
        d = _compact(m)
        assert "inner_optional" not in d


class TestListOfModels:
    def test_list_of_models_compacted(self):
        m = HasList(items=[Inner(x=5, y="a"), Inner(x=0, y="b")])
        d = _compact(m)
        # Inner(x=5, y="a") -> {"x": 5, "y": "a"} (both non-default)
        # Inner(x=0, y="b") -> {"y": "b"} (x=0 dropped as default, y non-default)
        assert d["items"] == [{"x": 5, "y": "a"}, {"y": "b"}]

    def test_empty_list_dropped(self):
        m = HasList()
        d = _compact(m)
        assert "items" not in d

    def test_nonempty_list_kept(self):
        m = HasList(items=[Inner(x=5)])
        d = _compact(m)
        assert "items" in d


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_roundtrip_preserves_values(self):
        original = Outer(
            source_ip="10.0.0.1",
            hostname="edge1",
            dscp=46,
            name="my_rule",
            tags=["prod"],
            optional="extra",
            inner=Inner(x=5, y="hello"),
        )
        compacted = _compact(original)
        restored = Outer.model_validate(compacted)

        assert restored.source_ip == "10.0.0.1"
        assert restored.hostname == "edge1"
        assert restored.dscp == 46
        assert restored.name == "my_rule"
        assert restored.tags == ["prod"]
        assert restored.optional == "extra"
        assert restored.inner.x == 5
        assert restored.inner.y == "hello"

    def test_roundtrip_defaults_filled(self):
        """Omitted fields get their defaults back on load."""
        m = Outer()
        compacted = _compact(m)
        restored = Outer.model_validate(compacted)

        assert restored.source_ip == "any"
        assert restored.hostname == ""
        assert restored.dscp == -1
        assert restored.name == "real_default"
        assert restored.tags == []
        assert restored.optional is MISSING
        assert restored.inner.x == 0
        assert restored.inner.y == "kept"

    def test_roundtrip_nested(self):
        m = HasList(items=[Inner(x=5), Inner(x=0)])
        compacted = _compact(m)
        restored = HasList.model_validate(compacted)

        assert restored.items[0].x == 5
        assert restored.items[1].x == 0


# ---------------------------------------------------------------------------
# Normalize: multi-representation equivalence
# ---------------------------------------------------------------------------


class NormalizedPort(VcoModel):
    port: Annotated[int, Normalizer(lambda v: -1 if v == 0 else v)] = -1


class TestNormalize:
    def test_both_representations_dropped(self):
        assert "port" not in _compact(NormalizedPort(port=-1))
        assert "port" not in _compact(NormalizedPort(port=0))

    def test_real_value_kept(self):
        assert _compact(NormalizedPort(port=8080))["port"] == 8080


# ---------------------------------------------------------------------------
# Deprecation warning on compact_dump
# ---------------------------------------------------------------------------


class TestDeprecationWarning:
    def test_compact_dump_emits_warning(self):
        m = Outer()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            m.compact_dump()
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "compact_dump" in str(w[0].message)

    def test_compact_dump_still_works(self):
        m = Outer(source_ip="10.0.0.1")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            d = m.compact_dump()
        assert d["source_ip"] == "10.0.0.1"
        assert "hostname" not in d
