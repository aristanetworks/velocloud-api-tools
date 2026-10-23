"""Typing helpers for assertions about Pydantic's ``MISSING`` sentinel."""

from __future__ import annotations

from pydantic import MISSING


def require_present[T](value: T | MISSING) -> T:
    """Return a field value, failing the test if Pydantic left it missing."""
    if value is MISSING:
        raise AssertionError("expected a present model field")
    return value
