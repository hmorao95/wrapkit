"""
On-disk JSON response cache with a freshness window.

Every wrapper's ``cache_dir`` argument defaults to :data:`DEFAULT_CACHE_DIR`,
which :func:`resolve_cache_dir` turns into ``~/.cache/<package>`` when the
client is built (never at import, so a changed home directory is honoured).
``None`` or ``""`` means no on-disk cache.

A :class:`DiskCache` stores one JSON file per request. The file name is a
SHA-256 digest of the endpoint and the sorted request parameters with the secret
ones left out, so a key never reaches a file name and two callers with
different keys share one cached answer.
"""

from __future__ import annotations

import enum
import hashlib
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

if TYPE_CHECKING:
    import logging
    from collections.abc import Iterable, Sequence

__all__ = [
    "DEFAULT_CACHE_DIR",
    "CacheDir",
    "DefaultCacheDir",
    "DiskCache",
    "cache_path",
    "resolve_cache_dir",
]


class DefaultCacheDir(enum.Enum):
    """
    Type of :data:`DEFAULT_CACHE_DIR`, the "use the default cache" marker.

    A one-member enum rather than a ready-made path, so that the directory is
    resolved when a client is constructed, not when a module is imported. Being
    an enum member, it is a distinct value type checkers can narrow on, and it
    can never be confused with ``None``, which means "no on-disk cache".
    """

    DEFAULT_CACHE_DIR = "DEFAULT_CACHE_DIR"

    def __repr__(self) -> str:
        """
        Return the public name, so signatures read ``cache_dir=DEFAULT_CACHE_DIR``.

        Returns:
            str: The literal ``"DEFAULT_CACHE_DIR"``.
        """
        return "DEFAULT_CACHE_DIR"


#: Default value of a ``cache_dir`` argument: cache in ``~/.cache/<package>``.
DEFAULT_CACHE_DIR = DefaultCacheDir.DEFAULT_CACHE_DIR

#: What a ``cache_dir`` argument accepts. ``Union`` keeps it valid at runtime on
#: Python 3.10.
CacheDir = Union[str, Path, DefaultCacheDir, None]  # ruff: ignore[non-pep604-annotation-union]


def resolve_cache_dir(value: CacheDir, package: str) -> Path | None:
    """
    Turn a ``cache_dir`` argument into the cache directory to use, if any.

    Nothing is created here; :class:`DiskCache` creates the directory.

    Args:
        value (CacheDir): The value passed to a client constructor.
        package (str): Directory name under ``~/.cache`` for the default.

    Returns:
        Path | None: ``~/.cache/<package>`` under the *current* home directory
        for :data:`DEFAULT_CACHE_DIR`, ``None`` for ``None`` or any other falsy
        value (caching off), otherwise the given path.
    """
    if isinstance(value, DefaultCacheDir):
        return Path.home() / ".cache" / package
    return Path(value) if value else None


def cache_path(
    cache_dir: Path | None,
    endpoint: str,
    params: Iterable[tuple[str, Any]] | None,
    *,
    hidden: Iterable[str] = (),
    suffix: str = ".json",
) -> Path | None:
    """
    Return the cache file for a request, or ``None`` when caching is off.

    Args:
        cache_dir (Path | None): Cache directory, or ``None`` if caching is off.
        endpoint (str): The path or URL being requested.
        params (Iterable[tuple[str, Any]] | None): Request parameters as
            (name, value) pairs.
        hidden (Iterable[str]): Names of secret parameters, left out of the
            digest so a key never influences (or reaches) the file name.
        suffix (str): File-name extension.

    Returns:
        Path | None: The cache file path, or ``None`` if caching is disabled.
    """
    if not cache_dir:
        return None
    skip = set(hidden)
    signature = sorted((str(k), str(v)) for k, v in params or () if k not in skip)
    blob = json.dumps([endpoint, signature], separators=(",", ":"))
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}{suffix}"


class DiskCache:
    """
    A directory of cached JSON answers, each fresh for ``ttl_seconds``.

    Only successful answers should be stored: callers :meth:`put` after their
    own error checks. Reads and writes use UTF-8.
    """

    def __init__(
        self,
        directory: Path | None,
        ttl_seconds: float,
        *,
        hidden: Iterable[str] = (),
        logger: logging.Logger | None = None,
    ) -> None:
        """
        Set up the cache and create its directory.

        Args:
            directory (Path | None): Cache directory, or ``None`` for a cache
                that never hits and never stores.
            ttl_seconds (float): How long an entry stays fresh.
            hidden (Iterable[str]): Secret parameter names left out of the key.
            logger (logging.Logger | None): Receives DEBUG lines for hits and
                expired entries.
        """
        self.directory = directory
        self.ttl_seconds = ttl_seconds
        self.hidden = tuple(hidden)
        self.logger = logger
        if directory:
            directory.mkdir(parents=True, exist_ok=True)

    def path(
        self, endpoint: str, params: Sequence[tuple[str, Any]] | None
    ) -> Path | None:
        """
        Return the cache file for a request (see :func:`cache_path`).

        Args:
            endpoint (str): The path or URL being requested.
            params (Sequence[tuple[str, Any]] | None): Request parameters.

        Returns:
            Path | None: The cache file, or ``None`` if caching is disabled.
        """
        return cache_path(self.directory, endpoint, params, hidden=self.hidden)

    def get(
        self, endpoint: str, params: Sequence[tuple[str, Any]] | None
    ) -> tuple[bool, Any]:
        """
        Look up a fresh cached answer.

        Args:
            endpoint (str): The path or URL being requested.
            params (Sequence[tuple[str, Any]] | None): Request parameters.

        Returns:
            tuple[bool, Any]: ``(True, payload)`` on a fresh hit, otherwise
            ``(False, None)``. A JSON ``null`` payload is a valid hit.
        """
        file = self.path(endpoint, params)
        if file is None or not file.exists():
            return False, None
        age = time.time() - file.stat().st_mtime
        if age >= self.ttl_seconds:
            self._debug("Cache expired: %s (age %.0fs)", endpoint, age)
            return False, None
        self._debug("Cache hit: %s (age %.0fs)", endpoint, age)
        return True, json.loads(file.read_text("utf-8"))

    def put(
        self, endpoint: str, params: Sequence[tuple[str, Any]] | None, payload: object
    ) -> None:
        """
        Store an answer (a no-op when caching is off).

        Args:
            endpoint (str): The path or URL that was requested.
            params (Sequence[tuple[str, Any]] | None): Request parameters.
            payload (object): A JSON-serialisable answer.
        """
        file = self.path(endpoint, params)
        if file is not None:
            file.write_text(json.dumps(payload), encoding="utf-8")

    def _debug(self, msg: str, *args: object) -> None:
        """
        Log at DEBUG on the configured logger, if any.

        Args:
            msg (str): The format string.
            *args (object): Its arguments.
        """
        if self.logger is not None:
            self.logger.debug(msg, *args)
