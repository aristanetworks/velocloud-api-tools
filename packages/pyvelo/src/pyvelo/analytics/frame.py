from __future__ import annotations

from collections.abc import Iterator
from typing import Any, override

import polars as pl


class GroupByProxy:
    __slots__ = ("_df", "_keys")

    def __init__(self, df: pl.DataFrame, keys: list[str]) -> None:
        self._df = df
        self._keys = keys

    def agg(
        self,
        *exprs: pl.Expr,
        count: str | None = None,
        sum: str | list[str] | None = None,
        mean: str | list[str] | None = None,
        min: str | list[str] | None = None,
        max: str | list[str] | None = None,
    ) -> MetricFrame:
        agg_exprs: list[pl.Expr] = list(exprs)
        if count:
            agg_exprs.append(pl.len().alias(count))
        for label, func in [
            (sum, "sum"),
            (mean, "mean"),
            (min, "min"),
            (max, "max"),
        ]:
            if label is None:
                continue
            cols = [label] if isinstance(label, str) else label
            for c in cols:
                agg_exprs.append(getattr(pl.col(c), func)().alias(f"{c}_{func}"))
        result = self._df.group_by(self._keys).agg(agg_exprs)
        return MetricFrame._from_polars(result)


class MetricFrame:
    __slots__ = ("_df",)

    def __init__(
        self,
        columns: list[str],
        rows: list[dict[str, Any]],
    ) -> None:
        if rows:
            data = {c: [row.get(c) for row in rows] for c in columns}
            self._df = pl.DataFrame(data)
        else:
            self._df = pl.DataFrame(schema={c: pl.Utf8 for c in columns})

    @classmethod
    def _from_polars(cls, df: pl.DataFrame) -> MetricFrame:
        frame = object.__new__(cls)
        frame._df = df
        return frame

    # -- backward-compat properties -------------------------------------------

    @property
    def columns(self) -> list[str]:
        return self._df.columns

    @property
    def rows(self) -> list[dict[str, Any]]:
        return self._df.to_dicts()

    # -- core operations (return new MetricFrame) -----------------------------

    def filter(self, field: str, operator: str, value: Any) -> MetricFrame:
        ops: dict[str, Any] = {
            "==": lambda c, v: c == v,
            "!=": lambda c, v: c != v,
            ">": lambda c, v: c > v,
            ">=": lambda c, v: c >= v,
            "<": lambda c, v: c < v,
            "<=": lambda c, v: c <= v,
            "in": lambda c, v: c.is_in(v),
            "not_in": lambda c, v: ~c.is_in(v),
            "contains": lambda c, v: c.cast(pl.Utf8).str.contains(v, literal=True),
            "starts_with": lambda c, v: c.cast(pl.Utf8).str.starts_with(v),
            "ends_with": lambda c, v: c.cast(pl.Utf8).str.ends_with(v),
            "is_null": lambda c, _: c.is_null(),
            "is_not_null": lambda c, _: c.is_not_null(),
        }
        builder = ops.get(operator)
        if builder is None:
            raise ValueError(
                f"Unknown operator {operator!r}. Supported: {', '.join(sorted(ops))}"
            )
        expr = builder(pl.col(field), value)
        return MetricFrame._from_polars(self._df.filter(expr))

    def group_by(self, fields: str | list[str]) -> GroupByProxy:
        keys = [fields] if isinstance(fields, str) else list(fields)
        return GroupByProxy(self._df, keys)

    def top_n(
        self,
        n: int,
        *,
        by: str,
        descending: bool = True,
    ) -> MetricFrame:
        sorted_df = self._df.sort(by, descending=descending)
        return MetricFrame._from_polars(sorted_df.head(n))

    def sort(self, field: str, *, descending: bool = False) -> MetricFrame:
        return MetricFrame._from_polars(self._df.sort(field, descending=descending))

    def select(self, columns: list[str]) -> MetricFrame:
        return MetricFrame._from_polars(self._df.select(columns))

    def drop(self, columns: list[str]) -> MetricFrame:
        return MetricFrame._from_polars(self._df.drop(columns))

    def head(self, n: int = 5) -> MetricFrame:
        return MetricFrame._from_polars(self._df.head(n))

    def tail(self, n: int = 5) -> MetricFrame:
        return MetricFrame._from_polars(self._df.tail(n))

    # -- export ---------------------------------------------------------------

    def to_polars(self) -> pl.DataFrame:
        return self._df

    def to_dataframe(self) -> pl.DataFrame:
        return self._df

    def to_dict(
        self,
        *,
        max_rows: int = 50,
        include_schema: bool = True,
    ) -> dict[str, Any]:
        total = len(self._df)
        truncated = total > max_rows
        df = self._df.head(max_rows) if truncated else self._df
        result: dict[str, Any] = {
            "total_rows": total,
            "columns": self._df.columns,
            "rows": df.to_dicts(),
        }
        if truncated:
            result["truncated"] = True
            result["showing"] = max_rows
        if include_schema:
            result["schema"] = {
                name: str(dtype)
                for name, dtype in zip(self._df.columns, self._df.dtypes)
            }
        return result

    # -- dunder ---------------------------------------------------------------

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self._df.to_dicts())

    def __len__(self) -> int:
        return len(self._df)

    @override
    def __repr__(self) -> str:
        return f"MetricFrame(columns={self._df.columns}, rows={len(self._df)})"
