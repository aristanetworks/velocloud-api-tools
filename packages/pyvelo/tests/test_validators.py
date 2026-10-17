"""Tests for custom validators: check_time_is_never, check_int_zero, check_string_empty."""

import datetime

from pydantic_core import MISSING
from pyvelo.client.schemas.event import check_int_zero, check_string_empty
from pyvelo.core import check_time_is_never


class TestCheckTimeIsNever:
    def test_zero_timestamp_string(self):
        """'0000-00-00 00:00:00' should be converted to epoch start."""
        result = check_time_is_never("0000-00-00 00:00:00")
        assert isinstance(result, datetime.datetime)
        assert result == datetime.datetime.fromtimestamp(0, datetime.UTC)

    def test_normal_timestamp_passthrough(self):
        """Normal timestamp strings should pass through unchanged."""
        ts = "2024-01-15 10:30:00"
        assert check_time_is_never(ts) == ts

    def test_none_passthrough(self):
        """None should pass through unchanged."""
        assert check_time_is_never(None) is None

    def test_datetime_passthrough(self):
        """Datetime objects should pass through unchanged."""
        dt = datetime.datetime(2024, 1, 15, tzinfo=datetime.UTC)
        assert check_time_is_never(dt) is dt

    def test_repeated_calls_return_equal_epoch(self):
        """Repeated calls should return equal epoch datetime objects."""
        r1 = check_time_is_never("0000-00-00 00:00:00")
        r2 = check_time_is_never("0000-00-00 00:00:00")
        assert r1 == r2


class TestCheckIntZero:
    def test_zero_becomes_missing(self):
        """Zero should be converted to MISSING."""
        assert check_int_zero(0) is MISSING

    def test_nonzero_passthrough(self):
        """Non-zero integers should pass through."""
        assert check_int_zero(42) == 42
        assert check_int_zero(-1) == -1

    def test_missing_passthrough(self):
        """MISSING should pass through unchanged."""
        assert check_int_zero(MISSING) is MISSING


class TestCheckStringEmpty:
    def test_empty_string_becomes_missing(self):
        """Empty string should be converted to MISSING."""
        assert check_string_empty("") is MISSING

    def test_nonempty_passthrough(self):
        """Non-empty strings should pass through."""
        assert check_string_empty("hello") == "hello"

    def test_missing_passthrough(self):
        """MISSING should pass through unchanged."""
        assert check_string_empty(MISSING) is MISSING

    def test_whitespace_not_empty(self):
        """Whitespace-only strings are not empty."""
        assert check_string_empty(" ") == " "
