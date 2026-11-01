"""Tests for :mod:`wrapkit.io`."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from wrapkit import io

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("name", "fmt"),
    [
        ("a.xlsx", "excel"),
        ("a.XLS", "excel"),
        ("a.parquet", "parquet"),
        ("a.json", "json"),
        ("a.csv", "csv"),
        ("a.txt", "csv"),
    ],
)
def test_frame_format(name: str, fmt: str) -> None:
    """The extension picks the format."""
    assert io.frame_format(name) == fmt


def _frame() -> pd.DataFrame:
    """
    Return a frame with a timezone-aware column.

    Returns:
        pd.DataFrame: Two rows.
    """
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-02"]).tz_localize("UTC"),
            "value": [1.0, 2.0],
        }
    )


def test_write_each_format(tmp_path: Path) -> None:
    """Every format round-trips the values."""
    frame = _frame()
    csv = io.write_frame(frame, tmp_path / "a.csv")
    assert pd.read_csv(csv)["value"].tolist() == [1.0, 2.0]
    xlsx = io.write_frame(frame, tmp_path / "a.xlsx")
    back = pd.read_excel(xlsx, sheet_name="data")
    assert back["value"].tolist() == [1.0, 2.0]
    assert frame["date"].dt.tz is not None  # input untouched
    parquet = io.write_frame(frame, str(tmp_path / "a.parquet"))
    assert pd.read_parquet(parquet)["value"].tolist() == [1.0, 2.0]
    js = io.write_frame(frame, tmp_path / "a.json")
    records = json.loads(js.read_text("utf-8"))
    assert records[0]["value"] == 1.0
    assert records[0]["date"].startswith("2024-01-01T00:00:00")


def test_write_explicit_format(tmp_path: Path) -> None:
    """An explicit format overrides the extension."""
    path = io.write_frame(_frame(), tmp_path / "a.dat", "json")
    assert json.loads(path.read_text("utf-8"))[1]["value"] == 2.0
