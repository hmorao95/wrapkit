"""Tests for :mod:`wrapkit.incremental`."""

from __future__ import annotations

import warnings
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
    assert out["k"].tolist() == ["1", ""]


def test_key_strings_writes_whole_decimals_as_integers() -> None:
    """``110.0`` and ``110`` give one key; codes and real decimals are kept."""
    floats = pd.DataFrame({"k": [110.0, -3.0, 2.5, np.nan]})
    text = pd.DataFrame({"k": ["110.0", "-3.00", "2.50", "01", "1e3", "A.0"]})
    assert incremental.key_strings(floats, ["k"])["k"].tolist() == [
        "110",
        "-3",
        "2.5",
        "",
    ]
    assert incremental.key_strings(text, ["k"])["k"].tolist() == [
        "110",
        "-3",
        "2.50",
        "01",
        "1e3",
        "A.0",
    ]


def test_int_and_float_keys_match_across_runs(tmp_path: Path) -> None:
    """A revision is not added as a new row when the key's dtype flips."""
    path = tmp_path / "s.csv"
    # First run: the key column had a gap, so it was float and stored as 110.0.
    pd.DataFrame({"k": [110.0, np.nan], "v": [1.0, 2.0]}).to_csv(path, index=False)
    # Next run: no gap, so the key is int; the value for 110 was revised.
    fresh = pd.DataFrame({"k": [110], "v": [5.0]})
    existing = incremental.read_existing(path, fresh, ["v"])
    new = incremental.select_new(fresh, existing, ["k"], ["v"])
    assert new["v"].tolist() == [5.0]
    merged = incremental.upsert(existing, new, ["k"])
    assert len(merged) == 2  # 110 replaced, the blank-key row kept
    assert sorted(merged["v"].tolist()) == [2.0, 5.0]


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


def test_read_existing_empty_file(tmp_path: Path) -> None:
    """A zero-byte CSV counts as no prior extract instead of crashing."""
    path = tmp_path / "empty.csv"
    path.touch()
    out = incremental.read_existing(path, _fresh(), ["v"])
    assert out.empty
    assert list(out.columns) == list(_fresh().columns)


def test_select_new_wide_frame_does_not_fragment(tmp_path: Path) -> None:
    """Hundreds of value columns, read back from a CSV, compare without a warning."""
    values = [f"m{i}" for i in range(200)]
    stored = pd.DataFrame({"k": ["a", "b"], **{c: [1.0, 2.0] for c in values}})
    path = tmp_path / "wide.csv"
    stored.to_csv(path, index=False)
    fresh = stored.copy()
    fresh.loc[1, "m150"] = 9.0
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        existing = incremental.read_existing(path, fresh, values)
        new = incremental.select_new(fresh, existing, ["k"], values)
        existing["extra"] = 1  # the returned frame is consolidated too
    assert new["k"].tolist() == ["b"]


def test_upsert_numeric_sort() -> None:
    """``numeric_cols`` sort as numbers, text from a CSV included."""
    stored = pd.DataFrame({"code": ["10", "9"], "year": ["2024", "2024"]})
    fresh = pd.DataFrame({"code": [100, 2], "year": ["2024", "2024"]})
    by_text = incremental.upsert(stored, fresh, ["code", "year"], date_cols=())
    assert [str(c) for c in by_text["code"]] == ["10", "100", "2", "9"]
    by_number = incremental.upsert(
        stored, fresh, ["code", "year"], date_cols=(), numeric_cols=["code"]
    )
    assert [str(c) for c in by_number["code"]] == ["2", "9", "10", "100"]


def test_read_existing_reads_floats_back_exactly(tmp_path: Path) -> None:
    """Full-precision floats survive the CSV round trip bit for bit."""
    values = np.random.default_rng(0).random(2000) * 1000
    fresh = pd.DataFrame({"k": [str(i) for i in range(len(values))], "v": values})
    fresh.loc[0, "v"] = np.nan
    path = tmp_path / "s.csv"
    fresh.to_csv(path, index=False)
    existing = incremental.read_existing(path, fresh, ["v"])
    assert existing["v"].dtype == "float64"
    assert existing["v"].isna().tolist() == fresh["v"].isna().tolist()
    assert (existing["v"].iloc[1:].to_numpy() == values[1:]).all()
    assert incremental.select_new(fresh, existing, ["k"], ["v"]).empty


def test_read_existing_number_parse_keeps_ints_and_junk(tmp_path: Path) -> None:
    """Whole numbers stay integers and text that is not a number is missing."""
    path = tmp_path / "s.csv"
    path.write_text("k,n,v\na,1,x\nb,2,1.5\n", encoding="utf-8")
    existing = incremental.read_existing(path, pd.DataFrame(), ["n", "v"])
    assert existing["n"].tolist() == [1, 2]
    assert pd.api.types.is_integer_dtype(existing["n"])
    assert existing["v"].isna().tolist() == [True, False]
    assert existing["v"].iloc[1] == 1.5


def test_read_existing_keeps_na_like_codes(tmp_path: Path) -> None:
    """Codes such as "NA" (Namibia) survive a CSV round trip; blanks stay NaN."""
    fresh = pd.DataFrame(
        {
            "date": ["2024-01-01"] * 4,
            "country": ["NA", "None", "null", "US"],
            "v": [1.0, 2.0, 3.0, np.nan],
        }
    )
    path = tmp_path / "s.csv"
    fresh.to_csv(path, index=False)
    existing = incremental.read_existing(path, fresh, ["v"])
    assert existing["country"].tolist() == ["NA", "None", "null", "US"]
    assert existing["v"].isna().tolist() == [False, False, False, True]
    new = incremental.select_new(fresh, existing, ["date", "country"], ["v"])
    assert new.empty
    merged = incremental.upsert(existing, fresh, ["date", "country"])
    assert len(merged) == 4


def test_select_new_compares_nullable_missing_values() -> None:
    """A value going between <NA> and text counts as a revision, not a crash."""
    existing = pd.DataFrame(
        {"date": ["2024-01-01", "2024-01-02"], "v": [1.0, 2.0], "unit": ["TWh", None]}
    )
    fresh = existing.copy()
    fresh["unit"] = pd.array([pd.NA, pd.NA], dtype="string")
    new = incremental.select_new(fresh, existing, ["date"], ["v", "unit"])
    assert new["date"].tolist() == ["2024-01-01"]
    back = incremental.select_new(existing, fresh, ["date"], ["v", "unit"])
    assert back["date"].tolist() == ["2024-01-01"]


def test_key_strings_keeps_dst_hours_and_fractions_apart() -> None:
    """Aware times key in UTC; a fraction of a second is part of the key."""
    stamps = pd.to_datetime(
        ["2024-10-27T02:00:00+02:00", "2024-10-27T02:00:00+01:00"], utc=True
    ).tz_convert("Europe/Berlin")
    aware = incremental.key_strings(pd.DataFrame({"date": stamps}), ["date"])
    assert aware["date"].tolist() == ["2024-10-27T00:00:00", "2024-10-27T01:00:00"]
    naive = pd.DataFrame(
        {"date": pd.to_datetime(["2024-01-01 00:00:00.1", "2024-01-01 00:00:00.9"])}
    )
    assert incremental.key_strings(naive, ["date"])["date"].tolist() == [
        "2024-01-01T00:00:00.1",
        "2024-01-01T00:00:00.9",
    ]
    whole = pd.DataFrame({"date": ["2024-01-01", None]})
    assert incremental.key_strings(whole, ["date"])["date"].tolist() == [
        "2024-01-01T00:00:00",
        "",
    ]


def test_dst_hours_survive_a_csv_round_trip(tmp_path: Path) -> None:
    """Both 02:00 hours of the DST change are kept and match on the next run."""
    stamps = pd.to_datetime(
        [
            "2024-10-27T01:00:00+02:00",
            "2024-10-27T02:00:00+02:00",
            "2024-10-27T02:00:00+01:00",
            "2024-10-27T03:00:00+01:00",
        ],
        utc=True,
    ).tz_convert("Europe/Berlin")
    fresh = pd.DataFrame({"date": stamps, "v": [1.0, 2.0, 3.0, 4.0]})
    path = tmp_path / "x.csv"
    first = incremental.upsert(fresh.iloc[0:0], fresh, ["date"])
    assert len(first) == 4
    first.to_csv(path, index=False)
    stored = incremental.read_existing(path, fresh, ["v"])
    assert incremental.select_new(fresh, stored, ["date"], ["v"]).empty
    merged = incremental.upsert(stored, fresh, ["date"])
    assert len(merged) == 4
    assert str(merged["date"].dt.tz) == "Europe/Berlin"
    assert merged["date"].tolist() == stamps.tolist()


def test_read_existing_text_cols_keep_missing(tmp_path: Path) -> None:
    """A blank stored label stays missing, so it matches the fresh row again."""
    path = tmp_path / "x.csv"
    pd.DataFrame({"period": ["2024-01", None], "v": [1.0, 2.0]}).to_csv(
        path, index=False
    )
    fresh = pd.DataFrame({"period": ["2024-01", None], "v": [1.0, 2.0]})
    stored = incremental.read_existing(path, fresh, ["v"], text_cols=["period"])
    assert stored["period"].isna().tolist() == [False, True]
    assert stored["period"].iloc[0] == "2024-01"
    assert incremental.select_new(fresh, stored, ["period"], ["v"]).empty
