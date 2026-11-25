"""Tests for custom validators: check_time_is_never, check_int_zero, check_string_empty."""

import datetime as dt
from typing import cast

from pydantic import MISSING
from pyvelo.client.schemas.event import check_int_zero, check_string_empty
from pyvelo.core import check_time_is_never


class TestCheckTimeIsNever:
    def test_zero_timestamp_string(self) -> None:
        """'0000-00-00 00:00:00' should be converted to epoch start."""
        result = cast(
            "dt.datetime | str | None", check_time_is_never("0000-00-00 00:00:00")
        )
        assert isinstance(result, dt.datetime)
        assert result == dt.datetime.fromtimestamp(0, dt.UTC)

    def test_normal_timestamp_passthrough(self) -> None:
        """Normal timestamp strings should pass through unchanged."""
        ts = "2024-01-15 10:30:00"
        assert check_time_is_never(ts) == ts

    def test_none_passthrough(self) -> None:
        """None should pass through unchanged."""
        assert check_time_is_never(None) is None

    def test_datetime_passthrough(self) -> None:
        """Datetime objects should pass through unchanged."""
        moment = dt.datetime(2024, 1, 15, tzinfo=dt.UTC)
        assert check_time_is_never(moment) is moment

    def test_repeated_calls_return_equal_epoch(self) -> None:
        """Repeated calls should return equal epoch datetime objects."""
        r1 = cast(
            "dt.datetime | str | None", check_time_is_never("0000-00-00 00:00:00")
        )
        r2 = cast(
            "dt.datetime | str | None", check_time_is_never("0000-00-00 00:00:00")
        )
        assert r1 == r2


class TestCheckIntZero:
    def test_zero_becomes_missing(self) -> None:
        """Zero should be converted to MISSING."""
        assert check_int_zero(0) is MISSING

    def test_nonzero_passthrough(self) -> None:
        """Non-zero integers should pass through."""
        assert check_int_zero(42) == 42
        assert check_int_zero(-1) == -1

    def test_missing_passthrough(self) -> None:
        """MISSING should pass through unchanged."""
        assert check_int_zero(MISSING) is MISSING


class TestCheckStringEmpty:
    def test_empty_string_becomes_missing(self) -> None:
        """Empty string should be converted to MISSING."""
        assert check_string_empty("") is MISSING

    def test_nonempty_passthrough(self) -> None:
        """Non-empty strings should pass through."""
        assert check_string_empty("hello") == "hello"

    def test_missing_passthrough(self) -> None:
        """MISSING should pass through unchanged."""
        assert check_string_empty(MISSING) is MISSING

    def test_whitespace_not_empty(self) -> None:
        """Whitespace-only strings are not empty."""
        assert check_string_empty(" ") == " "
