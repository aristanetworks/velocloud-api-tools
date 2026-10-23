from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import cast

import polars as pl
import pytest
from pyvelo.analytics import _flow
from pyvelo.analytics._helpers import is_other_bucket
from pyvelo.analytics._series import denormalize_timeseries
from pyvelo.analytics.frame import MetricFrame
from pyvelo.client.schemas.metrics import EdgeAppMetricsItem

_items_to_frame = cast(
    Callable[[Sequence[EdgeAppMetricsItem]], MetricFrame],
    getattr(_flow, "_items_to_frame"),
)


class TestMetricFrameBackwardCompat:
    def test_columns_and_rows(self):
        f = MetricFrame(columns=["a", "b"], rows=[{"a": 1, "b": "x"}])
        assert f.columns == ["a", "b"]
        assert f.rows == [{"a": 1, "b": "x"}]

    def test_empty_frame(self):
        f = MetricFrame(columns=["a", "b"], rows=[])
        assert len(f) == 0
        assert f.columns == ["a", "b"]
        assert f.rows == []

    def test_len(self):
        f = MetricFrame(columns=["a"], rows=[{"a": 1}, {"a": 2}, {"a": 3}])
        assert len(f) == 3

    def test_iter(self):
        rows = [{"a": 1}, {"a": 2}]
        f = MetricFrame(columns=["a"], rows=rows)
        assert list(f) == rows

    def test_repr(self):
        f = MetricFrame(columns=["x"], rows=[{"x": 1}])
        assert "MetricFrame" in repr(f)
        assert "rows=1" in repr(f)

    def test_to_dataframe_returns_polars(self):
        f = MetricFrame(columns=["a"], rows=[{"a": 1}])
        df = f.to_dataframe()
        assert isinstance(df, pl.DataFrame)

    def test_datetime_values(self):
        ts = datetime(2024, 1, 1, tzinfo=UTC)
        f = MetricFrame(columns=["ts", "v"], rows=[{"ts": ts, "v": 42}])
        assert len(f) == 1
        assert f.rows[0]["v"] == 42

    def test_none_values(self):
        f = MetricFrame(
            columns=["a", "b"],
            rows=[{"a": 1, "b": None}, {"a": None, "b": "y"}],
        )
        assert len(f) == 2

    def test_mixed_int_string_column(self):
        f = MetricFrame(
            columns=["name", "count"],
            rows=[
                {"name": 205, "count": 50},
                {"name": 1448, "count": 25},
            ],
        )
        assert len(f) == 2
        assert all(isinstance(r["name"], int) for r in f)


class TestMetricFrameFilter:
    @pytest.fixture
    def frame(self) -> MetricFrame:
        return MetricFrame(
            columns=["name", "count", "status"],
            rows=[
                {"name": "alpha", "count": 10, "status": "up"},
                {"name": "beta", "count": 20, "status": "down"},
                {"name": "gamma", "count": 30, "status": "up"},
                {"name": "delta", "count": 5, "status": "down"},
            ],
        )

    def test_filter_eq(self, frame: MetricFrame):
        result = frame.filter("status", "==", "up")
        assert len(result) == 2
        assert all(r["status"] == "up" for r in result)

    def test_filter_ne(self, frame: MetricFrame):
        result = frame.filter("status", "!=", "up")
        assert len(result) == 2

    def test_filter_gt(self, frame: MetricFrame):
        result = frame.filter("count", ">", 10)
        assert len(result) == 2

    def test_filter_in(self, frame: MetricFrame):
        result = frame.filter("name", "in", ["alpha", "delta"])
        assert len(result) == 2
        names = {r["name"] for r in result}
        assert names == {"alpha", "delta"}

    def test_filter_not_in(self, frame: MetricFrame):
        result = frame.filter("name", "not_in", ["alpha"])
        assert len(result) == 3

    def test_filter_contains(self, frame: MetricFrame):
        result = frame.filter("name", "contains", "lph")
        assert len(result) == 1  # alpha

    def test_filter_is_null(self):
        f = MetricFrame(columns=["a"], rows=[{"a": 1}, {"a": None}])
        assert len(f.filter("a", "is_null", None)) == 1
        assert len(f.filter("a", "is_not_null", None)) == 1

    def test_filter_unknown_operator(self, frame: MetricFrame):
        with pytest.raises(ValueError, match="Unknown operator"):
            _ = frame.filter("count", "~", 5)

    def test_filter_returns_new_frame(self, frame: MetricFrame):
        result = frame.filter("count", "==", 10)
        assert len(frame) == 4
        assert len(result) == 1


class TestMetricFrameGroupBy:
    def test_group_by_count(self):
        f = MetricFrame(
            columns=["cat", "val"],
            rows=[
                {"cat": "A", "val": 10},
                {"cat": "A", "val": 20},
                {"cat": "B", "val": 30},
            ],
        )
        result = f.group_by("cat").agg(count="n")
        result = result.sort("cat")
        assert len(result) == 2
        assert result.rows[0]["cat"] == "A"
        assert result.rows[0]["n"] == 2
        assert result.rows[1]["n"] == 1

    def test_group_by_sum(self):
        f = MetricFrame(
            columns=["cat", "val"],
            rows=[
                {"cat": "A", "val": 10},
                {"cat": "A", "val": 20},
                {"cat": "B", "val": 30},
            ],
        )
        result = f.group_by("cat").agg(sum="val")
        result = result.sort("cat")
        assert result.rows[0]["val_sum"] == 30
        assert result.rows[1]["val_sum"] == 30

    def test_group_by_multiple_keys(self):
        f = MetricFrame(
            columns=["a", "b", "v"],
            rows=[
                {"a": "X", "b": 1, "v": 10},
                {"a": "X", "b": 1, "v": 20},
                {"a": "X", "b": 2, "v": 30},
            ],
        )
        result = f.group_by(["a", "b"]).agg(count="n")
        assert len(result) == 2

    def test_group_by_with_polars_expr(self):
        f = MetricFrame(
            columns=["cat", "val"],
            rows=[
                {"cat": "A", "val": 10},
                {"cat": "A", "val": 20},
            ],
        )
        result = f.group_by("cat").agg(pl.col("val").max().alias("val_max"))
        assert result.rows[0]["val_max"] == 20


class TestMetricFrameTopN:
    def test_top_n(self):
        f = MetricFrame(
            columns=["name", "count"],
            rows=[
                {"name": "a", "count": 1},
                {"name": "b", "count": 3},
                {"name": "c", "count": 2},
            ],
        )
        result = f.top_n(2, by="count", descending=True)
        assert len(result) == 2
        assert result.rows[0]["count"] == 3
        assert result.rows[1]["count"] == 2

    def test_top_n_ascending(self):
        f = MetricFrame(
            columns=["name", "count"],
            rows=[
                {"name": "a", "count": 1},
                {"name": "b", "count": 3},
                {"name": "c", "count": 2},
            ],
        )
        result = f.top_n(1, by="count", descending=False)
        assert result.rows[0]["count"] == 1


class TestMetricFrameSort:
    def test_sort_ascending(self):
        f = MetricFrame(columns=["v"], rows=[{"v": 3}, {"v": 1}, {"v": 2}])
        result = f.sort("v")
        assert [r["v"] for r in result] == [1, 2, 3]

    def test_sort_descending(self):
        f = MetricFrame(columns=["v"], rows=[{"v": 3}, {"v": 1}, {"v": 2}])
        result = f.sort("v", descending=True)
        assert [r["v"] for r in result] == [3, 2, 1]


class TestMetricFrameSelectDrop:
    def test_select(self):
        f = MetricFrame(columns=["a", "b", "c"], rows=[{"a": 1, "b": 2, "c": 3}])
        result = f.select(["a", "c"])
        assert result.columns == ["a", "c"]

    def test_drop(self):
        f = MetricFrame(columns=["a", "b", "c"], rows=[{"a": 1, "b": 2, "c": 3}])
        result = f.drop(["b"])
        assert result.columns == ["a", "c"]


class TestMetricFrameExport:
    def test_to_polars(self):
        f = MetricFrame(columns=["a"], rows=[{"a": 1}])
        df = f.to_polars()
        assert isinstance(df, pl.DataFrame)
        assert df.shape == (1, 1)

    def test_to_dict(self):
        f = MetricFrame(columns=["a"], rows=[{"a": i} for i in range(100)])
        d = cast(dict[str, object], f.to_dict(max_rows=10))
        assert d["total_rows"] == 100
        assert d["truncated"] is True
        assert d["showing"] == 10
        assert len(cast(list[object], d["rows"])) == 10
        assert "schema" in d

    def test_to_dict_no_truncation(self):
        f = MetricFrame(columns=["a"], rows=[{"a": 1}])
        d = f.to_dict()
        assert d["total_rows"] == 1
        assert "truncated" not in d


class TestMetricFrameImmutability:
    def test_operations_return_new_frames(self):
        f = MetricFrame(columns=["a"], rows=[{"a": 1}, {"a": 2}, {"a": 3}])
        filtered = f.filter("a", ">", 1)
        sorted_ = f.sort("a", descending=True)
        top = f.top_n(1, by="a")
        assert len(f) == 3
        assert len(filtered) == 2
        assert len(sorted_) == 3
        assert len(top) == 1


class TestOtherBucketFiltering:
    @pytest.mark.parametrize("value", ["other", "Other", "OTHER"])
    def test_is_other_bucket_true(self, value: str):
        assert is_other_bucket(value) is True

    @pytest.mark.parametrize("value", [0, 123, "myapp", "another"])
    def test_is_other_bucket_false(self, value: str | int):
        assert is_other_bucket(value) is False

    def test_items_to_frame_drops_other(self):
        items = [
            EdgeAppMetricsItem(name=205, bytes_rx=100, bytes_tx=200),
            EdgeAppMetricsItem(name="other", bytes_rx=50, bytes_tx=75),
            EdgeAppMetricsItem(name=1448, bytes_rx=10, bytes_tx=20),
        ]
        frame = _items_to_frame(items)
        assert len(frame) == 2
        names = [r["name"] for r in frame]
        assert 205 in names
        assert 1448 in names
        assert "other" not in names

    def test_denormalize_timeseries_drops_other(self):
        items = [
            {
                "name": "app1",
                "series": [
                    {
                        "metric": "bytesRx",
                        "start_time": 1700000000000,
                        "tick_interval": 300000,
                        "data": [1, 2],
                    },
                ],
            },
            {
                "name": "other",
                "series": [
                    {
                        "metric": "bytesRx",
                        "start_time": 1700000000000,
                        "tick_interval": 300000,
                        "data": [9, 9],
                    },
                ],
            },
        ]
        frame = denormalize_timeseries(items, "name")
        names = {r["name"] for r in frame}
        assert "app1" in names
        assert "other" not in names
