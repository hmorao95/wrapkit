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

# A whole number spelled as a decimal ("110.0", "-3.00"); group 1 is "110".
_WHOLE_DECIMAL = r"^([+-]?\d+)\.0+$"


def key_strings(
    frame: pd.DataFrame,
    key_cols: Sequence[str],
    *,
    date_cols: Iterable[str] = ("date",),
) -> pd.DataFrame:
    """
    Render a frame's key columns as comparable strings.

    Date columns are normalised to one ISO format first; missing values become
    ``""``. A whole number spelled as a decimal (``"110.0"``) is written as an
    integer (``"110"``): pandas spells a number by its column's dtype, and a
    key column turns float when a fetch has a gap in it, so the same key would
    otherwise read ``110`` in one run and ``110.0`` in the next (or in the
    stored CSV). Codes without a decimal point, such as ``"01"``, are kept.

    Args:
        frame (pd.DataFrame): A fetched or stored frame.
        key_cols (Sequence[str]): The identifying columns.
        date_cols (Iterable[str]): Key columns holding dates.

    Returns:
        pd.DataFrame: The key columns as plain strings, same index as ``frame``.
    """
    dates = set(date_cols)
    columns: dict[str, pd.Series] = {}
    for col in key_cols:
        series = frame[col]
        if col in dates:
            series = pd.to_datetime(series, format="ISO8601").dt.strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
        text = series.astype("string").fillna("")
        if col not in dates:
            text = text.str.replace(_WHOLE_DECIMAL, r"\1", regex=True)
        columns[col] = text.astype(str)
    # Built in one go: adding many columns one by one fragments the frame.
    return pd.DataFrame(columns, index=frame.index)


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
    columns = dict(key_strings(fresh, key_cols, date_cols=dates).items())
    columns.update((col, fresh[col]) for col in prior_cols)
    # Built in one go: wide frames have hundreds of value columns, and adding
    # them one by one makes pandas warn about a fragmented frame.
    left = pd.DataFrame(columns, index=fresh.index)
    stored = dict(key_strings(existing, key_cols, date_cols=dates).items())
    stored.update((renamed[col], existing[col]) for col in prior_cols)
    prior = pd.DataFrame(stored, index=existing.index).drop_duplicates(
        subset=key_cols, keep="last"
    )
    merged = left.merge(prior, on=key_cols, how="left", indicator=True)
    selected = merged["_merge"] == "left_only"
    if include_revisions:
        for col in prior_cols:
            now, prev = merged[col], merged[f"_prev_{col}"]
            # ``<NA>`` against a value compares as ``<NA>`` in nullable dtypes;
            # count it as a difference so the mask stays boolean.
            equal = (now == prev).astype("boolean").fillna(value=False).astype(bool)
            same = equal | (now.isna() & prev.isna())
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
    columns are then parsed back into numbers. Only an empty field counts as
    missing: codes such as ``"NA"`` (Namibia) or ``"None"`` are kept as text,
    not turned into NaN as pandas does by default. A missing or empty
    (zero-byte) file counts as no prior extract.

    Args:
        existing (pd.DataFrame | str | Path): The prior extract or a CSV path.
        like (pd.DataFrame): The fresh frame; a missing or empty file gives an
            empty frame with its columns.
        value_cols (Sequence[str]): Numeric columns to parse after reading a
            CSV.
        text_cols (Iterable[str]): Columns forced to ``str`` either way (such as
            a raw ``period`` label).

    Returns:
        pd.DataFrame: A comparable copy of the prior extract.
    """
    if isinstance(existing, (str, Path)):
        path = Path(existing)
        if not path.exists() or path.stat().st_size == 0:
            return like.iloc[0:0]
        frame = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
        for col in value_cols:
            if col in frame.columns:
                frame[col] = _to_number(frame[col])
        # Parsing column by column leaves one block per column; consolidate so
        # callers can add columns to a wide frame without pandas warning.
        frame = frame.copy()
    else:
        frame = existing.copy()
    for col in text_cols:
        if col in frame.columns:
            frame[col] = frame[col].astype(str)
    return frame


def _to_number(text: pd.Series) -> pd.Series:
    """
    Parse a column read as text into numbers, exactly.

    ``pd.to_numeric`` before pandas 3.1 can land one unit in the last place off
    a full-precision float that ``to_csv`` wrote (about one value in eight), so
    an unchanged stored value would compare as a revision on every run. The
    values it parses as floats are parsed again with ``float``, which is exact;
    text that is not a number still becomes ``NaN``.

    Args:
        text (pd.Series): A column read from CSV as text.

    Returns:
        pd.Series: The parsed numbers.
    """
    numbers = pd.to_numeric(text, errors="coerce")
    if pd.api.types.is_float_dtype(numbers):
        parsed = numbers.notna()
        numbers[parsed] = text[parsed].astype("float64")
    return numbers


def upsert(
    existing: pd.DataFrame,
    fresh: pd.DataFrame,
    key_cols: Sequence[str],
    sort_cols: Sequence[str] | None = None,
    *,
    date_cols: Iterable[str] = ("date",),
    numeric_cols: Iterable[str] = (),
) -> pd.DataFrame:
    """
    Merge ``fresh`` into ``existing``, the fresh row winning on a key clash.

    Stored history that ``fresh`` no longer covers is kept. Date columns are
    unified to timestamps (stored ones are text after a CSV read), and rows
    are sorted by ``sort_cols`` compared as text, except the ``numeric_cols``
    among them, which are compared as numbers (so ``9`` sorts before ``10``).

    Args:
        existing (pd.DataFrame): The stored rows.
        fresh (pd.DataFrame): The fresh rows.
        key_cols (Sequence[str]): The identifying columns.
        sort_cols (Sequence[str] | None): Sort order; ``key_cols`` when
            ``None``.
        date_cols (Iterable[str]): Columns holding dates.
        numeric_cols (Iterable[str]): Sort columns compared as numbers; values
            that are not numbers sort last.

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
    sort_keys = key_strings(combined, order_cols, date_cols=dates)
    for col in set(numeric_cols).intersection(order_cols):
        sort_keys[col] = pd.to_numeric(combined[col], errors="coerce")
    order = sort_keys.sort_values(order_cols).index
    return combined.loc[order].reset_index(drop=True)
