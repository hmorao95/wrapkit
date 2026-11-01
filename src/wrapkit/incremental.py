"""
Incremental updates of a long extract: find new or revised rows, upsert them.

Each wrapper's ``new_observations`` and ``update_csv`` reduce to three steps:
read what is already stored (:func:`read_existing`), pick the fresh rows that
are new or revised (:func:`select_new`), and merge them in
(:func:`upsert`). Keys are compared as text, so a CSV round-trip that changed
their dtypes (``"01"`` read back as ``1``, a date read back as text) does not
defeat the match, and two missing values count as equal.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = ["key_strings", "read_existing", "select_new", "upsert"]


def key_strings(
    frame: pd.DataFrame,
    key_cols: Sequence[str],
    *,
    date_cols: Iterable[str] = ("date",),
) -> pd.DataFrame:
    """
    Render a frame's key columns as comparable strings.

    Date columns are normalised to one ISO format first; missing values become
    ``""``.

    Args:
        frame (pd.DataFrame): A fetched or stored frame.
        key_cols (Sequence[str]): The identifying columns.
        date_cols (Iterable[str]): Key columns holding dates.

    Returns:
        pd.DataFrame: The key columns as plain strings, same index as ``frame``.
    """
    dates = set(date_cols)
    out = pd.DataFrame(index=frame.index)
    for col in key_cols:
        series = frame[col]
        if col in dates:
            series = pd.to_datetime(series, format="ISO8601").dt.strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
        out[col] = series.astype("string").fillna("").astype(str)
    return out


def select_new(
    fresh: pd.DataFrame,
    existing: pd.DataFrame,
    key_cols: list[str],
    value_cols: list[str],
    *,
    include_revisions: bool = True,
    date_cols: Iterable[str] = ("date",),
) -> pd.DataFrame:
    """
    Return the rows of ``fresh`` that are new (or revised) versus ``existing``.

    A fresh row is selected when its key is absent from ``existing``. With
    ``include_revisions``, rows whose key exists but whose value columns differ
    are selected too; two missing values count as equal.

    Args:
        fresh (pd.DataFrame): A freshly fetched long frame.
        existing (pd.DataFrame): The previously stored long frame.
        key_cols (list[str]): The identifying columns.
        value_cols (list[str]): The value columns compared for revisions.
        include_revisions (bool): Also select rows whose stored value changed.
        date_cols (Iterable[str]): Key columns holding dates.

    Returns:
        pd.DataFrame: The subset of ``fresh`` to add or upsert, with ``fresh``'s
        columns and a fresh index.
    """
    if existing.empty:
        return fresh
    dates = tuple(date_cols)
    prior_cols = [c for c in value_cols if c in existing.columns]
    renamed = {c: f"_prev_{c}" for c in prior_cols}
    left = key_strings(fresh, key_cols, date_cols=dates)
    for col in prior_cols:
        left[col] = fresh[col]
    prior = (
        key_strings(existing, key_cols, date_cols=dates)
        .join(existing[prior_cols].rename(columns=renamed))
        .drop_duplicates(subset=key_cols, keep="last")
    )
    merged = left.merge(prior, on=key_cols, how="left", indicator=True)
    selected = merged["_merge"] == "left_only"
    if include_revisions:
        for col in prior_cols:
            now, prev = merged[col], merged[f"_prev_{col}"]
            same = (now == prev) | (now.isna() & prev.isna())
            selected |= (merged["_merge"] == "both") & ~same
    return fresh[selected.to_numpy()].reset_index(drop=True)


def read_existing(
    existing: pd.DataFrame | str | Path,
    like: pd.DataFrame,
    value_cols: Sequence[str] = (),
    *,
    text_cols: Iterable[str] = (),
) -> pd.DataFrame:
    """
    Normalise an "already extracted" source into a comparable frame.

    A frame is copied, so the caller's is not changed. A CSV is read as text, so
    key columns keep their exact spelling (``"01"`` stays ``"01"``); the value
    columns are then parsed back into numbers.

    Args:
        existing (pd.DataFrame | str | Path): The prior extract or a CSV path.
        like (pd.DataFrame): The fresh frame; a missing file gives an empty
            frame with its columns.
        value_cols (Sequence[str]): Numeric columns to parse after reading a
            CSV.
        text_cols (Iterable[str]): Columns forced to ``str`` either way (such as
            a raw ``period`` label).

    Returns:
        pd.DataFrame: A comparable copy of the prior extract.
    """
    if isinstance(existing, (str, Path)):
        path = Path(existing)
        if not path.exists():
            return like.iloc[0:0]
        frame = pd.read_csv(path, dtype=str)
        for col in value_cols:
            if col in frame.columns:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
    else:
        frame = existing.copy()
    for col in text_cols:
        if col in frame.columns:
            frame[col] = frame[col].astype(str)
    return frame


def upsert(
    existing: pd.DataFrame,
    fresh: pd.DataFrame,
    key_cols: Sequence[str],
    sort_cols: Sequence[str] | None = None,
    *,
    date_cols: Iterable[str] = ("date",),
) -> pd.DataFrame:
    """
    Merge ``fresh`` into ``existing``, the fresh row winning on a key clash.

    Stored history that ``fresh`` no longer covers is kept. Date columns are
    unified to timestamps (stored ones are text after a CSV read), and rows
    are sorted by ``sort_cols`` compared as text.

    Args:
        existing (pd.DataFrame): The stored rows.
        fresh (pd.DataFrame): The fresh rows.
        key_cols (Sequence[str]): The identifying columns.
        sort_cols (Sequence[str] | None): Sort order; ``key_cols`` when
            ``None``.
        date_cols (Iterable[str]): Columns holding dates.

    Returns:
        pd.DataFrame: The merged rows with a fresh index.
    """
    dates = tuple(date_cols)
    combined = pd.concat([existing, fresh], ignore_index=True)
    keys = key_strings(combined, key_cols, date_cols=dates)
    combined = combined[~keys.duplicated(keep="last").to_numpy()]
    for col in dates:
        if col in combined.columns:
            combined[col] = pd.to_datetime(combined[col], format="ISO8601")
    order_cols = list(key_cols if sort_cols is None else sort_cols)
    order = (
        key_strings(combined, order_cols, date_cols=dates).sort_values(order_cols).index
    )
    return combined.loc[order].reset_index(drop=True)
