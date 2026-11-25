"""``MetricFrame``: a table of analytics rows over a Polars ``DataFrame``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, override

import polars as pl

if TYPE_CHECKING:
    from collections.abc import Iterator


class GroupByProxy:
    """The keys of a ``MetricFrame.group_by``, waiting for ``agg``."""

    __slots__: ClassVar[tuple[str, ...]] = ("_df", "_keys")

    def __init__(self, df: pl.DataFrame, keys: list[str]) -> None:
        """A grouping of *df* by the columns in *keys*."""
        self._df: pl.DataFrame = df
        self._keys: list[str] = keys

    def agg(
        self,
        *exprs: pl.Expr,
        count: str | None = None,
        sum_: str | list[str] | None = None,
        mean: str | list[str] | None = None,
        min_: str | list[str] | None = None,
        max_: str | list[str] | None = None,
    ) -> MetricFrame:
        """Aggregate each group into one row of a new ``MetricFrame``.

        *exprs* are Polars expressions, used as given. *count* names a column for
        the group's row count. *sum*, *mean*, *min* and *max* each name a column or
        columns, and give one result column per name, called ``<column>_<function>``.
        """
        agg_exprs: list[pl.Expr] = list(exprs)
        if count:
            agg_exprs.append(pl.len().alias(count))
        for label, func in [
            (sum_, "sum"),
            (mean, "mean"),
            (min_, "min"),
            (max_, "max"),
        ]:
            if label is None:
                continue
            cols = [label] if isinstance(label, str) else label
            agg_exprs.extend(
                getattr(pl.col(c), func)().alias(f"{c}_{func}") for c in cols
            )
        result = self._df.group_by(self._keys).agg(agg_exprs)
        return MetricFrame.from_polars(result)


class MetricFrame:
    """Analytics rows as a Polars ``DataFrame``, with operations that return new frames.

    Every ``Analytics`` method returns one. ``to_polars`` gives the frame itself
    for anything the operations here do not cover.
    """

    __slots__: ClassVar[tuple[str, ...]] = ("_df",)

    def __init__(
        self,
        columns: list[str],
        rows: list[dict[str, Any]],
    ) -> None:
        """A frame of *rows*, each a dict read by the names in *columns*.

        A key a row lacks gives null. With no rows, every column is a string column.
        """
        if rows:
            data = {c: [row.get(c) for row in rows] for c in columns}
            self._df: pl.DataFrame = pl.DataFrame(data)
        else:
            self._df = pl.DataFrame(schema=dict.fromkeys(columns, pl.Utf8))

    @classmethod
    def from_polars(cls, df: pl.DataFrame) -> MetricFrame:
        """A ``MetricFrame`` over *df*, as it is."""
        frame = object.__new__(cls)
        frame._df = df
        return frame

    # -- backward-compat properties -------------------------------------------

    @property
    def columns(self) -> list[str]:
        """The column names, in order."""
        return self._df.columns

    @property
    def rows(self) -> list[dict[str, Any]]:
        """Every row as a dict, column name to value."""
        return self._df.to_dicts()

    # -- core operations (return new MetricFrame) -----------------------------

    def filter(self, field: str, operator: str, value: Any) -> MetricFrame:
        """The rows where column *field* matches *value* by *operator*.

        *operator* is a comparison (``==``, ``!=``, ``>``, ``>=``, ``<``, ``<=``),
        ``in`` or ``not_in`` a list, ``contains``, ``starts_with`` or ``ends_with`` on
        the value as text, or ``is_null`` or ``is_not_null`` (*value* unused). Any
        other raises ``ValueError``.
        """
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
        return MetricFrame.from_polars(self._df.filter(expr))

    def group_by(self, fields: str | list[str]) -> GroupByProxy:
        """A grouping by the column or columns *fields*; ``agg`` makes the frame."""
        keys = [fields] if isinstance(fields, str) else list(fields)
        return GroupByProxy(self._df, keys)

    def top_n(
        self,
        n: int,
        *,
        by: str,
        descending: bool = True,
    ) -> MetricFrame:
        """The first *n* rows by column *by*, largest first when *descending*."""
        sorted_df = self._df.sort(by, descending=descending)
        return MetricFrame.from_polars(sorted_df.head(n))

    def sort(self, field: str, *, descending: bool = False) -> MetricFrame:
        """The rows sorted by column *field*."""
        return MetricFrame.from_polars(self._df.sort(field, descending=descending))

    def select(self, columns: list[str]) -> MetricFrame:
        """Only *columns*, in that order."""
        return MetricFrame.from_polars(self._df.select(columns))

    def drop(self, columns: list[str]) -> MetricFrame:
        """Every column but *columns*."""
        return MetricFrame.from_polars(self._df.drop(columns))

    def head(self, n: int = 5) -> MetricFrame:
        """The first *n* rows."""
        return MetricFrame.from_polars(self._df.head(n))

    def tail(self, n: int = 5) -> MetricFrame:
        """The last *n* rows."""
        return MetricFrame.from_polars(self._df.tail(n))

    # -- export ---------------------------------------------------------------

    def to_polars(self) -> pl.DataFrame:
        """The Polars ``DataFrame`` the frame holds, not a copy."""
        return self._df

    def to_dataframe(self) -> pl.DataFrame:
        """The Polars ``DataFrame`` the frame holds; the same as ``to_polars``."""
        return self._df

    def to_dict(
        self,
        *,
        max_rows: int = 50,
        include_schema: bool = True,
    ) -> dict[str, Any]:
        """The frame as a JSON-ready dict: ``total_rows``, ``columns`` and ``rows``.

        At most *max_rows* rows are given; when there are more, ``truncated`` and
        ``showing`` say so. With *include_schema*, ``schema`` maps each column to its
        Polars type.
        """
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
                for name, dtype in zip(self._df.columns, self._df.dtypes, strict=True)
            }
        return result

    # -- dunder ---------------------------------------------------------------

    def __iter__(self) -> Iterator[dict[str, Any]]:
        """Each row as a dict."""
        return iter(self._df.to_dicts())

    def __len__(self) -> int:
        """The number of rows."""
        return len(self._df)

    @override
    def __repr__(self) -> str:
        return f"MetricFrame(columns={self._df.columns}, rows={len(self._df)})"
