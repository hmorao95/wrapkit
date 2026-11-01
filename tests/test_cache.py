"""Tests for :mod:`wrapkit.cache`."""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

import pytest

from wrapkit import cache

if TYPE_CHECKING:
    from pathlib import Path


def test_default_sentinel_repr() -> None:
    """The sentinel reads as its public name."""
    assert repr(cache.DEFAULT_CACHE_DIR) == "DEFAULT_CACHE_DIR"


def test_resolve_default_uses_current_home(home: Path) -> None:
    """The default resolves under the home directory at call time."""
    assert cache.resolve_cache_dir(cache.DEFAULT_CACHE_DIR, "pkg") == (
        home / ".cache" / "pkg"
    )


@pytest.mark.parametrize("value", [None, ""])
def test_resolve_falsy_disables(value: str | None) -> None:
    """None and "" mean no cache."""
    assert cache.resolve_cache_dir(value, "pkg") is None


def test_resolve_path(tmp_path: Path) -> None:
    """Any other value is the directory."""
    assert cache.resolve_cache_dir(str(tmp_path), "pkg") == tmp_path


def test_cache_path_hides_secrets_and_ignores_order(tmp_path: Path) -> None:
    """Secret parameters and parameter order do not change the name."""
    a = cache.cache_path(
        tmp_path, "e", [("api_key", "1"), ("x", "a"), ("y", 2)], hidden=["api_key"]
    )
    b = cache.cache_path(
        tmp_path, "e", [("y", "2"), ("x", "a"), ("api_key", "9")], hidden=["api_key"]
    )
    assert a == b
    assert a is not None
    assert a.suffix == ".json"
    assert cache.cache_path(tmp_path, "other", [("x", "a")]) != a


def test_cache_path_off() -> None:
    """No directory means no path."""
    assert cache.cache_path(None, "e", None) is None


def test_disk_cache_round_trip(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A stored answer is a hit while fresh, a miss once expired."""
    lg = logging.getLogger("wrapkit_cache_test")
    store = cache.DiskCache(tmp_path / "c", 60.0, hidden=["k"], logger=lg)
    assert (tmp_path / "c").is_dir()
    assert store.get("e", [("k", "s")]) == (False, None)
    store.put("e", [("k", "s")], None)
    with caplog.at_level(logging.DEBUG, logger="wrapkit_cache_test"):
        assert store.get("e", [("k", "other")]) == (True, None)
        file = store.path("e", [])
        assert file is not None
        old = time.time() - 120
        os.utime(file, (old, old))
        assert store.get("e", []) == (False, None)
    assert "Cache hit" in caplog.text
    assert "Cache expired" in caplog.text


def test_disk_cache_off_never_stores() -> None:
    """A cache with no directory never hits and never writes."""
    store = cache.DiskCache(None, 60.0)
    store.put("e", [], {"a": 1})
    assert store.get("e", []) == (False, None)


def test_disk_cache_without_logger(tmp_path: Path) -> None:
    """A hit works with no logger configured."""
    store = cache.DiskCache(tmp_path, 60.0)
    store.put("e", None, {"a": [1, 2]})
    assert store.get("e", None) == (True, {"a": [1, 2]})
