"""Tests for :mod:`wrapkit.cache`."""

from __future__ import annotations

import logging
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from wrapkit import cache


def _temp_files(directory: Path) -> list[str]:
    """
    List the temp files in a cache directory.

    Args:
        directory (Path): The cache directory.

    Returns:
        list[str]: Sorted names of its ``*.tmp`` files.
    """
    return sorted(p.name for p in directory.iterdir() if p.suffix == ".tmp")


def _link_or_skip(kind: str, link: Path, target: Path) -> None:
    """
    Create a hard or symbolic link, or skip the test where the OS refuses.

    Creating a symlink on Windows needs a privilege (or Developer Mode).

    Args:
        kind (str): ``"hard"`` or ``"symbolic"``.
        link (Path): Where the link goes.
        target (Path): The file it points to.
    """
    try:
        if kind == "hard":
            link.hardlink_to(target)
        else:
            link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a {kind} link here: {exc}")


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


def test_disk_cache_unreadable_entry_is_a_miss(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A truncated entry (say, an interrupted write) is a miss, not an error."""
    lg = logging.getLogger("wrapkit_cache_test")
    store = cache.DiskCache(tmp_path, 60.0, logger=lg)
    file = store.path("e", [])
    assert file is not None
    file.write_text('{"a": [1, 2', encoding="utf-8")
    with caplog.at_level(logging.DEBUG, logger="wrapkit_cache_test"):
        assert store.get("e", []) == (False, None)
    assert "unreadable" in caplog.text
    store.put("e", [], {"a": [1, 2]})
    assert store.get("e", []) == (True, {"a": [1, 2]})


def test_disk_cache_put_leaves_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write that fails part way leaves the old entry and no temp file."""
    store = cache.DiskCache(tmp_path, 60.0)
    store.put("e", [], {"a": 1})

    def _fail(*_a: object, **_k: object) -> None:
        """Simulate the swap failing (disk full, killed process)."""
        msg = "disk full"
        raise OSError(msg)

    monkeypatch.setattr("pathlib.Path.replace", _fail)
    with pytest.raises(OSError, match="disk full"):
        store.put("e", [], {"a": 2})
    assert store.get("e", []) == (True, {"a": 1})
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


@pytest.mark.parametrize("kind", ["hard", "symbolic"])
def test_disk_cache_put_never_writes_through_a_planted_temp_name(
    tmp_path: Path, kind: str
) -> None:
    """A link planted at the old guessable temp name, and its target, stay as they were."""
    directory = tmp_path / "c"
    store = cache.DiskCache(directory, 60.0)
    file = store.path("e", [("x", "1")])
    assert file is not None
    canary = tmp_path / "canary.txt"
    canary.write_text("CANARY", encoding="utf-8")
    # The name put used to write, <entry>.<pid>.tmp: anyone who can write the
    # directory could guess it and plant a link there first.
    planted = file.with_name(f"{file.name}.{os.getpid()}.tmp")
    _link_or_skip(kind, planted, canary)
    store.put("e", [("x", "1")], {"a": 1})
    assert canary.read_text(encoding="utf-8") == "CANARY"
    assert _temp_files(directory) == [planted.name]
    assert stat.S_ISREG(file.lstat().st_mode)
    assert not file.samefile(canary)
    assert file.read_bytes() == b'{"a": 1}'
    assert store.get("e", [("x", "1")]) == (True, {"a": 1})


def test_disk_cache_puts_never_share_a_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second put of the same key, landing mid-put, writes its own temp file."""
    store = cache.DiskCache(tmp_path, 60.0)
    file = store.path("e", [])
    assert file is not None
    real_replace = Path.replace
    temps: list[Path] = []

    def _replace(self: Path, target: Path) -> Path:
        """Run a whole second put between the first one's write and its swap."""
        temps.append(self)
        if len(temps) == 1:
            store.put("e", [], {"by": "second"})
        return real_replace(self, target)

    monkeypatch.setattr("pathlib.Path.replace", _replace)
    store.put("e", [], {"by": "first"})
    first, second = temps
    assert first != second
    for temp in temps:
        assert temp.parent == tmp_path
        assert temp.name.startswith(f"{file.name}.")
        assert temp.name.endswith(".tmp")
        assert temp.name != f"{file.name}.{os.getpid()}.tmp"
    # Each put swapped in its own complete file, so the last swap wins.
    assert store.get("e", []) == (True, {"by": "first"})
    assert _temp_files(tmp_path) == []


def test_disk_cache_put_unserialisable_creates_nothing(tmp_path: Path) -> None:
    """An answer JSON cannot encode raises before any file is created."""
    store = cache.DiskCache(tmp_path, 60.0)
    with pytest.raises(TypeError, match="not JSON serializable"):
        store.put("e", [], {1, 2})
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_disk_cache_entry_is_owner_only(tmp_path: Path) -> None:
    """On POSIX an entry is created readable and writable by its owner only."""
    store = cache.DiskCache(tmp_path, 60.0)
    store.put("e", [], {"a": 1})
    file = store.path("e", [])
    assert file is not None
    assert stat.S_IMODE(file.stat().st_mode) == 0o600


def test_disk_cache_symlinked_entry_is_a_miss(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A symlink at the entry name is a miss, and put replaces the link itself."""
    lg = logging.getLogger("wrapkit_cache_test")
    store = cache.DiskCache(tmp_path / "c", 60.0, logger=lg)
    file = store.path("e", [])
    assert file is not None
    target = tmp_path / "elsewhere.json"
    target.write_text('{"a": 1}', encoding="utf-8")
    _link_or_skip("symbolic", file, target)
    with caplog.at_level(logging.DEBUG, logger="wrapkit_cache_test"):
        assert store.get("e", []) == (False, None)
    assert "unreadable" in caplog.text
    store.put("e", [], {"a": 2})
    assert target.read_text(encoding="utf-8") == '{"a": 1}'
    assert not file.is_symlink()
    assert store.get("e", []) == (True, {"a": 2})


def test_disk_cache_entry_that_is_not_a_file_is_never_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A directory at the entry name is a miss, decided without opening it."""
    lg = logging.getLogger("wrapkit_cache_test")
    store = cache.DiskCache(tmp_path, 60.0, logger=lg)
    file = store.path("e", [])
    assert file is not None
    file.mkdir()

    def _no_read(*_a: object, **_k: object) -> str:
        """Fail the test if get opens the entry."""
        msg = "get opened an entry that is not a regular file"
        raise AssertionError(msg)

    monkeypatch.setattr("pathlib.Path.read_text", _no_read)
    with caplog.at_level(logging.DEBUG, logger="wrapkit_cache_test"):
        assert store.get("e", []) == (False, None)
    assert "unreadable" in caplog.text


def test_disk_cache_fifo_entry_is_a_miss(tmp_path: Path) -> None:
    """A FIFO at the entry name is a miss instead of blocking the read forever."""
    if sys.platform == "win32":
        pytest.skip("FIFOs are POSIX only")
    store = cache.DiskCache(tmp_path, 60.0)
    file = store.path("e", [])
    assert file is not None
    os.mkfifo(file)
    assert store.get("e", []) == (False, None)


def test_disk_cache_zero_ttl_never_hits(tmp_path: Path) -> None:
    """A zero TTL refetches even when the file's time is ahead of the clock."""
    store = cache.DiskCache(tmp_path, 0)
    store.put("e", [("a", "1")], {"x": 1})
    file = store.path("e", [("a", "1")])
    assert file is not None
    ahead = time.time() + 5
    os.utime(file, (ahead, ahead))
    assert store.get("e", [("a", "1")]) == (False, None)
    fresh = cache.DiskCache(tmp_path, 60)
    assert fresh.get("e", [("a", "1")]) == (True, {"x": 1})
