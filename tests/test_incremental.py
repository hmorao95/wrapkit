"""Tests for :mod:`wrapkit.incremental`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from wrapkit import incremental

if TYPE_CHECKING:
    from pathlib import Path


def _fresh() -> pd.DataFrame:
    """
    Return a small fetched frame.

    Returns:
        pd.DataFrame: Keys ``period``/``code``, value ``v``.
    """
    return pd.DataFrame(
        {
            "period": ["2024-01", "2024-02", "2024-03"],
            "code": ["01", "01", "02"],
            "v": [1.0, np.nan, 3.0],
        }
    )


def test_key_strings_normalises_dates_and_missing() -> None:
    """Dates become one ISO format and missing values ""."""
    frame = pd.DataFrame(
        {"date": ["2024-01-01", "2024-01-02T00:00:00"], "k": [1, None]}
    )
    out = incremental.key_strings(frame, ["date", "k"])
    assert out["date"].tolist() == ["2024-01-01T00:00:00", "2024-01-02T00:00:00"]
    assert out["k"].tolist() == ["1.0", ""]


def test_select_new_without_history_returns_all() -> None:
    """With nothing stored every row is new."""
    fresh = _fresh()
    assert (
        incremental.select_new(fresh, fresh.iloc[0:0], ["period", "code"], ["v"])
        is fresh
    )


def test_select_new_finds_new_and_revised(tmp_path: Path) -> None:
    """New keys and changed values are selected; NaN equals NaN."""
    stored = _fresh().iloc[:2].copy()
    stored.loc[0, "v"] = 0.5  # revised since
    path = tmp_path / "s.csv"
    stored.to_csv(path, index=False)
    existing = incremental.read_existing(path, _fresh(), ["v"], text_cols=["period"])
    assert existing["code"].tolist() == ["01", "01"]  # leading zero kept
    new = incremental.select_new(_fresh(), existing, ["period", "code"], ["v"])
    assert new["period"].tolist() == ["2024-01", "2024-03"]
    only_new = incremental.select_new(
        _fresh(), existing, ["period", "code"], ["v"], include_revisions=False
    )
    assert only_new["period"].tolist() == ["2024-03"]


def test_read_existing_frame_and_missing_file(tmp_path: Path) -> None:
    """A frame is copied; a missing file is an empty frame shaped like fresh."""
    fresh = _fresh()
    copy = incremental.read_existing(fresh, fresh, text_cols=["period", "nope"])
    assert copy is not fresh
    assert copy.equals(fresh)
    empty = incremental.read_existing(tmp_path / "none.csv", fresh)
    assert empty.empty
    assert list(empty.columns) == list(fresh.columns)


def test_upsert_fresh_wins_and_history_kept(tmp_path: Path) -> None:
    """Clashing keys take the fresh row; old rows stay; dates unify; sorted."""
    stored = pd.DataFrame(
        {"date": ["2023-12-01", "2024-01-01"], "k": ["a", "a"], "value": [9.0, 1.0]}
    )
    fresh = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-02-01"]),
            "k": ["a", "a"],
            "value": [1.5, 2.0],
        }
    )
    out = incremental.upsert(stored, fresh, ["k", "date"], ["k", "date"])
    assert out["value"].tolist() == [9.0, 1.5, 2.0]
    assert str(out["date"].dtype).startswith("datetime64")
    plain = incremental.upsert(
        pd.DataFrame({"p": ["2"], "v": [1]}),
        pd.DataFrame({"p": ["1"], "v": [2]}),
        ["p"],
    )
    assert plain["p"].tolist() == ["1", "2"]
