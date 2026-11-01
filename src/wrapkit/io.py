"""
Write a DataFrame to CSV, Excel, Parquet or JSON, chosen by file extension.

The wrappers' ``to_csv`` / ``to_excel`` / ``to_parquet`` / ``to_json`` methods
and their CLIs' ``--out`` flag all route through :func:`write_frame`, so every
wrapper writes the same way: no index, an Excel sheet named ``data`` with
timezone-naive datetimes (Excel has no time zones), and JSON as a list of
records with ISO dates.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import pandas as pd

__all__ = ["FrameFormat", "frame_format", "write_frame"]

#: The formats :func:`write_frame` writes.
FrameFormat = Literal["csv", "excel", "parquet", "json"]


def frame_format(path: str | Path) -> FrameFormat:
    """
    Pick the output format from a file name.

    Args:
        path (str | Path): The destination.

    Returns:
        FrameFormat: ``excel`` for ``.xlsx``/``.xls``, ``parquet`` for
        ``.parquet``, ``json`` for ``.json``, otherwise ``csv``.
    """
    suffix = Path(path).suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return "excel"
    if suffix == ".parquet":
        return "parquet"
    if suffix == ".json":
        return "json"
    return "csv"


def write_frame(
    frame: pd.DataFrame, path: str | Path, fmt: FrameFormat | None = None
) -> Path:
    """
    Write ``frame`` to ``path``.

    Args:
        frame (pd.DataFrame): The data.
        path (str | Path): The destination; its parent must exist.
        fmt (FrameFormat | None): The format, or ``None`` to choose by
            extension (see :func:`frame_format`).

    Returns:
        Path: The path written.
    """
    path = Path(path)
    kind = fmt or frame_format(path)
    if kind == "excel":
        out = frame.copy()
        for col in out.columns:
            if getattr(out[col].dtype, "tz", None) is not None:
                out[col] = out[col].dt.tz_localize(None)
        out.to_excel(path, index=False, sheet_name="data")
    elif kind == "parquet":
        frame.to_parquet(path, index=False)
    elif kind == "json":
        frame.to_json(path, orient="records", date_format="iso", indent=2)
    else:
        frame.to_csv(path, index=False)
    return path
