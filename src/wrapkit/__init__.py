"""
Shared infrastructure for the data-API wrappers.

The modules, each importable on its own:

* :mod:`wrapkit.log`: package logger, CLI log handlers, secret redaction.
* :mod:`wrapkit.cache`: the ``DEFAULT_CACHE_DIR`` sentinel and an on-disk JSON
  cache whose file names never contain a secret.
* :mod:`wrapkit.http`: retries with backoff and ``Retry-After``, a request-rate
  throttle (sync and async), the User-Agent pattern.
* :mod:`wrapkit.frames`: the tidy output shape, polars conversion, period
  start/end parsing.
* :mod:`wrapkit.incremental`: new/revised-row selection and upserts for
  ``new_observations`` and ``update_csv``.
* :mod:`wrapkit.io`: write a frame to CSV, Excel, Parquet or JSON by extension.
* :mod:`wrapkit.cli`: python-fire helpers and the error-handling runner.
"""

from __future__ import annotations

__version__ = "0.2.7"

__all__ = ["__version__"]
