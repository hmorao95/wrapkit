"""
DataFrame helpers shared by the wrappers: the tidy shape, polars, periods.

The opt-in tidy shape every wrapper offers is one row per observation with the
columns ``date``, then the source's dimension columns, then ``value`` (float64)
and ``unit`` (string, ``<NA>`` when unknown). Which columns are dimensions and
where the unit comes from is the wrapper's business; :func:`tidy_frame` applies
the shared column order, dtypes and sort.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from collections.abc import Sequence

    import polars as pl

__all__ = [
    "TIDY_VALUE_COLUMNS",
    "as_polars",
    "empty_tidy",
    "period_end",
    "period_start",
    "snake",
    "tidy_frame",
]

#: The fixed trailing columns of the tidy shape.
TIDY_VALUE_COLUMNS = ("value", "unit")


def snake(name: str, *, acronyms: bool = False) -> str:
    """
    Convert a column name to snake_case.

    Splits camelCase boundaries and turns any run of non-alphanumeric
    characters into one underscore: ``stateDescription`` becomes
    ``state_description`` and ``area-name`` becomes ``area_name``.

    By default a run of capitals stays glued to the next word (``GDPValue``
    becomes ``gdpvalue``). With ``acronyms`` the run is kept as a word of its
    own: ``GDPValue`` becomes ``gdp_value`` and ``isCAMRelevant``
    ``is_cam_relevant``.

    Args:
        name (str): The raw column name.
        acronyms (bool): Split a run of capitals from the word that follows.

    Returns:
        str: The lower-case snake_case name.
    """
    if acronyms:
        name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    spaced = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name)
    return re.sub(r"[^0-9A-Za-z]+", "_", spaced).strip("_").lower()


def empty_tidy(dims: Sequence[str]) -> pd.DataFrame:
    """
    Return an empty frame in the tidy shape, with its dtypes.

    Args:
        dims (Sequence[str]): The dimension columns, in order.

    Returns:
        pd.DataFrame: Columns ``date``, ``dims``, ``value``, ``unit``.
    """
    return pd.DataFrame(
        {
            "date": pd.Series(dtype="datetime64[ns]"),
            **{c: pd.Series(dtype="string") for c in dims},
            "value": pd.Series(dtype="float64"),
            "unit": pd.Series(dtype="string"),
        }
    )


def tidy_frame(frame: pd.DataFrame, dims: Sequence[str]) -> pd.DataFrame:
    """
    Put a frame into the tidy shape's column order, dtypes and row order.

    ``date`` becomes ``datetime64[ns]``, the dimensions ``string``, ``value``
    ``float64`` (unparseable values become NaN) and ``unit`` ``string`` with
    blank units as ``<NA>``. A missing ``unit`` column is added as all
    ``<NA>``. Rows are sorted by the dimensions, then ``date`` (stable), and
    get a fresh index. Other columns are dropped.

    Args:
        frame (pd.DataFrame): A frame with ``date``, ``dims``, ``value`` and
            optionally ``unit``.
        dims (Sequence[str]): The dimension columns, in order.

    Returns:
        pd.DataFrame: The tidy frame (a new object; the input is not changed).
    """
    if frame.empty:
        return empty_tidy(dims)
    tidy = frame.copy()
    tidy["date"] = pd.to_datetime(tidy["date"]).astype("datetime64[ns]")
    for col in dims:
        tidy[col] = tidy[col].astype("string")
    tidy["value"] = pd.to_numeric(tidy["value"], errors="coerce").astype("float64")
    unit = (
        tidy["unit"] if "unit" in tidy.columns else pd.Series(pd.NA, index=tidy.index)
    )
    text = unit.astype("string")
    # A blank unit carries no information: treat it as missing.
    tidy["unit"] = text.mask(text.eq("").fillna(value=False))
    return (
        tidy[["date", *dims, *TIDY_VALUE_COLUMNS]]
        .sort_values([*dims, "date"], kind="stable")
        .reset_index(drop=True)
    )


def as_polars(frame: pd.DataFrame, dist: str) -> pl.DataFrame:
    """
    Convert a pandas result to polars, importing polars lazily.

    polars is an optional extra of every wrapper, so it is only imported when a
    caller asks for ``as_polars=True``.

    Args:
        frame (pd.DataFrame): The pandas result.
        dist (str): The wrapper's distribution name, for the install hint.

    Returns:
        pl.DataFrame: ``polars.from_pandas(frame)``.

    Raises:
        ImportError: If polars is not installed.
    """
    try:
        import polars as pl
    except ImportError as exc:
        msg = f'polars is not installed; install it with pip install "{dist}[polars]"'
        raise ImportError(msg) from exc
    return pl.from_pandas(frame)


# A period label at year, quarter or month precision: 2024, 2024-Q2 (or
# 2024Q2), 2024-06 (or 202406).
_YEAR = re.compile(r"^\s*(\d{4})\s*$")
_QUARTER = re.compile(r"^\s*(\d{4})-?Q([1-4])\s*$", re.IGNORECASE)
_MONTH = re.compile(r"^\s*(\d{4})-?(0[1-9]|1[0-2])\s*$")


def _span(label: str) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """
    Return the start and exclusive end of a year, quarter or month label.

    Args:
        label (str): The period label.

    Returns:
        tuple[pd.Timestamp, pd.Timestamp] | None: ``(start, next_start)``, or
        ``None`` when ``label`` is not at one of those precisions.
    """
    if m := _YEAR.match(label):
        start = pd.Timestamp(year=int(m[1]), month=1, day=1)
        return start, start + pd.DateOffset(years=1)
    if m := _QUARTER.match(label):
        start = pd.Timestamp(year=int(m[1]), month=3 * int(m[2]) - 2, day=1)
        return start, start + pd.DateOffset(months=3)
    if m := _MONTH.match(label):
        start = pd.Timestamp(year=int(m[1]), month=int(m[2]), day=1)
        return start, start + pd.DateOffset(months=1)
    return None


def period_start(label: str) -> pd.Timestamp:
    """
    Return the first instant of a period label.

    Args:
        label (str): ``2024``, ``2024-Q2``, ``2024-06`` or anything
            :func:`pandas.Timestamp` parses (``2024-06-15``,
            ``2024-06-15T12:00``).

    Returns:
        pd.Timestamp: The start of the period. An unparseable label raises
        ``ValueError``.
    """
    span = _span(label)
    return span[0] if span else _parse(label)


def period_end(label: str) -> pd.Timestamp:
    """
    Return the exclusive end of a period label: an end bound covers its period.

    The shared "end" rule: ``end="2024"`` means up to the end of 2024, so the
    exclusive bound is 2025-01-01; ``2024-Q2`` ends at 2024-07-01 and
    ``2024-06`` at 2024-07-01. A full date or timestamp is returned as given:
    the wrapper decides whether a day bound is inclusive.

    Args:
        label (str): A period label (see :func:`period_start`).

    Returns:
        pd.Timestamp: The first instant after a year, quarter or month label,
        or the parsed timestamp itself. An unparseable label raises
        ``ValueError``.
    """
    span = _span(label)
    return span[1] if span else _parse(label)


def _parse(label: str) -> pd.Timestamp:
    """
    Parse a date or timestamp label.

    Args:
        label (str): The label.

    Returns:
        pd.Timestamp: The parsed value.

    Raises:
        ValueError: If pandas cannot parse it.
    """
    try:
        value = pd.Timestamp(label.strip())
    except (ValueError, TypeError) as exc:
        msg = f"Cannot parse period {label!r}."
        raise ValueError(msg) from exc
    if pd.isna(value):
        msg = f"Cannot parse period {label!r}."
        raise ValueError(msg)
    return value
