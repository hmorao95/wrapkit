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
import unicodedata
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

    Accents are dropped and German letters spelled out (``Região`` becomes
    ``regiao``, ``Größe`` ``groesse``); other letters, such as ``ø``, are kept.

    Args:
        name (str): The raw column name.
        acronyms (bool): Split a run of capitals from the word that follows.

    Returns:
        str: The lower-case snake_case name.
    """
    name = _plain_letters(name)
    if acronyms:
        name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    spaced = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name)
    return re.sub(r"[\W_]+", "_", spaced).strip("_").lower()


# German letters spelled out, as in "Groesse": an umlaut is its vowel plus "e".
# Other accents are just dropped.
_SPELLED_OUT = {u: unicodedata.normalize("NFKD", u)[0] + "e" for u in "äöüÄÖÜ"}
_SPELLED_OUT["ß"] = "ss"


def _plain_letters(name: str) -> str:
    """
    Drop accents and spell out German letters, keeping every other character.

    Args:
        name (str): A column name.

    Returns:
        str: ``name`` without combining marks, each umlaut spelled as its
        vowel plus ``e`` and ``ß`` as ``ss``.
    """
    for letter, spelled in _SPELLED_OUT.items():
        name = name.replace(letter, spelled)
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


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
# 2024Q2), 2024-06 (or 202406) and the SDMX month 2024-M06 (or 2024M6). Other
# month spellings (2024-6, 2024/06, 06/2024, Jun 2024) are recognised by
# pandas' period parser (_month). pandas.Timestamp reads 2024-M06 as
# 2024-01-01 00:00:06, so that spelling must be caught here.
_YEAR = re.compile(r"^\s*(\d{4})\s*$")
_QUARTER = re.compile(r"^\s*(\d{4})-?Q([1-4])\s*$", re.IGNORECASE)
_MONTH = re.compile(
    r"^\s*(\d{4})(?:-?(0[1-9]|1[0-2])|-?M(0?[1-9]|1[0-2]))\s*$", re.IGNORECASE
)
# A year joined to a letter code (2024-M13, 2024-S1, 2024-W23) that none of the
# above matched: pandas.Timestamp may read the digits as a time of day.
_CODED = re.compile(r"^\s*\d{4}-?[A-Za-z]\d*\s*$")


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
        start = pd.Timestamp(year=int(m[1]), month=int(m[2] or m[3]), day=1)
        return start, start + pd.DateOffset(months=1)
    if (first := _month(label)) is not None:
        return first, first + pd.DateOffset(months=1)
    return None


def _month(label: str) -> pd.Timestamp | None:
    """
    Return the first day of a month label in another spelling.

    ``2024-6``, ``2024/06``, ``06/2024``, ``2024.06`` and ``Jun 2024`` name a
    month; :func:`pandas.Timestamp` would read each as the month's first day,
    so an end bound would stop there. :class:`pandas.Period` infers the
    precision instead (a full date stays a day).

    Args:
        label (str): The period label.

    Returns:
        pd.Timestamp | None: The month's first day, or ``None`` when the label
        is not a month.
    """
    try:
        period = pd.Period(label.strip())
    except (ValueError, TypeError):
        return None
    if not isinstance(period, pd.Period) or period.freqstr != "M":
        return None  # NaT (an empty label) or another precision
    return pd.Timestamp(year=period.year, month=period.month, day=1)


def period_start(label: str) -> pd.Timestamp:
    """
    Return the first instant of a period label.

    Args:
        label (str): ``2024``, ``2024-Q2``, a month (``2024-06``, ``2024-6``,
            ``06/2024``, ``Jun 2024``, ``2024-M06``) or anything :func:`pandas.Timestamp`
            parses (``2024-06-15``, ``2024-06-15T12:00``).

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
    if _CODED.match(label):
        msg = f"Cannot parse period {label!r}."
        raise ValueError(msg)
    try:
        value = pd.Timestamp(label.strip())
    except (ValueError, TypeError) as exc:
        msg = f"Cannot parse period {label!r}."
        raise ValueError(msg) from exc
    if pd.isna(value):
        msg = f"Cannot parse period {label!r}."
        raise ValueError(msg)
    return value
