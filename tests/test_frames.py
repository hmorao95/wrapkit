"""Tests for :mod:`wrapkit.frames`."""

from __future__ import annotations

import builtins
from typing import Any

import pandas as pd
import pytest

from wrapkit import frames


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("stateDescription", "state_description"),
        ("area-name", "area_name"),
        ("  Value (USD) ", "value_usd"),
        ("already_snake", "already_snake"),
    ],
)
def test_snake(raw: str, expected: str) -> None:
    """Names become snake_case."""
    assert frames.snake(raw) == expected


@pytest.mark.parametrize(
    ("raw", "default", "with_acronyms"),
    [
        ("GDPValue", "gdpvalue", "gdp_value"),
        ("isCAMRelevant", "is_camrelevant", "is_cam_relevant"),
        ("tpTsoGCVMin", "tp_tso_gcvmin", "tp_tso_gcv_min"),
        ("pointKey", "point_key", "point_key"),
        ("ID", "id", "id"),
    ],
)
def test_snake_acronyms(raw: str, default: str, with_acronyms: str) -> None:
    """By default acronyms stay glued; ``acronyms=True`` splits them off."""
    assert frames.snake(raw) == default
    assert frames.snake(raw, acronyms=True) == with_acronyms


def test_empty_tidy_dtypes() -> None:
    """The empty shape has the tidy columns and dtypes."""
    empty = frames.empty_tidy(["area", "variable"])
    assert list(empty.columns) == ["date", "area", "variable", "value", "unit"]
    assert str(empty["date"].dtype) == "datetime64[ns]"
    assert str(empty["area"].dtype) == "string"
    assert str(empty["value"].dtype) == "float64"


def test_tidy_frame_orders_casts_and_sorts() -> None:
    """Columns, dtypes and sort order follow the shared shape."""
    raw = pd.DataFrame(
        {
            "extra": [1, 2, 3],
            "value": ["1.5", "x", 3],
            "area": ["B", "A", "A"],
            "date": ["2024-02-01", "2024-02-01", "2024-01-01"],
            "unit": ["MW", "", None],
        }
    )
    tidy = frames.tidy_frame(raw, ["area"])
    assert list(tidy.columns) == ["date", "area", "value", "unit"]
    assert tidy["area"].tolist() == ["A", "A", "B"]
    assert tidy["date"].dt.strftime("%Y-%m").tolist() == [
        "2024-01",
        "2024-02",
        "2024-02",
    ]
    assert tidy["value"].isna().tolist() == [False, True, False]
    assert tidy["unit"].isna().tolist() == [True, True, False]
    assert raw.columns[0] == "extra"  # input untouched


def test_tidy_frame_adds_missing_unit_and_handles_empty() -> None:
    """A missing unit column is all <NA>; an empty input gives the empty shape."""
    tidy = frames.tidy_frame(
        pd.DataFrame({"date": ["2024"], "k": ["a"], "value": [1]}), ["k"]
    )
    assert tidy["unit"].isna().all()
    assert frames.tidy_frame(pd.DataFrame(), ["k"]).columns.tolist() == [
        "date",
        "k",
        "value",
        "unit",
    ]


def test_as_polars() -> None:
    """A pandas frame converts to polars."""
    out = frames.as_polars(pd.DataFrame({"a": [1, 2]}), "x-api")
    assert out.shape == (2, 1)


def test_as_polars_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without polars the error names the wrapper's extra."""
    real_import = builtins.__import__

    def _no_polars(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "polars":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_polars)
    with pytest.raises(ImportError, match=r'pip install "x-api\[polars\]"'):
        frames.as_polars(pd.DataFrame(), "x-api")


@pytest.mark.parametrize(
    ("label", "start", "end"),
    [
        ("2024", "2024-01-01", "2025-01-01"),
        ("2024-Q2", "2024-04-01", "2024-07-01"),
        ("2024q4", "2024-10-01", "2025-01-01"),
        ("2024-06", "2024-06-01", "2024-07-01"),
        ("202412", "2024-12-01", "2025-01-01"),
        # Other month spellings cover the whole month, not its first day.
        ("2024-6", "2024-06-01", "2024-07-01"),
        ("2024/06", "2024-06-01", "2024-07-01"),
        ("06/2024", "2024-06-01", "2024-07-01"),
        ("2024.12", "2024-12-01", "2025-01-01"),
        ("Jun 2024", "2024-06-01", "2024-07-01"),
        (" june 2024 ", "2024-06-01", "2024-07-01"),
        ("2024-06-15", "2024-06-15", "2024-06-15"),
        (" 2024-06-15T12:30 ", "2024-06-15 12:30", "2024-06-15 12:30"),
    ],
)
def test_period_bounds(label: str, start: str, end: str) -> None:
    """Year, quarter and month labels cover their whole period."""
    assert frames.period_start(label) == pd.Timestamp(start)
    assert frames.period_end(label) == pd.Timestamp(end)


@pytest.mark.parametrize("label", ["soon", "", "  ", "2024-13", "13/2024"])
def test_period_bad_label(label: str) -> None:
    """Unparseable labels raise ValueError."""
    with pytest.raises(ValueError, match="Cannot parse period"):
        frames.period_end(label)
