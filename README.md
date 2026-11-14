# wrapkit

[![CI](https://github.com/hmorao95/wrapkit/actions/workflows/ci.yml/badge.svg)](https://github.com/hmorao95/wrapkit/actions/workflows/ci.yml)

Shared infrastructure for my data-API wrappers (`eia-api`, `entsoe-api`,
`ine-pt-api` and the rest). Each wrapper used to carry its own copy of the same
plumbing; `wrapkit` holds it once, so a fix lands in one place.

It holds only generic, secret-free code. Endpoints, request building, response
parsing and each wrapper's public API stay in the wrapper.

## Install

wrapkit is installed from GitHub, pinned to a release tag (no credentials
needed):

```bash
pip install "wrapkit @ git+https://github.com/hmorao95/wrapkit@v0.2.8"
```

In a wrapper's `pyproject.toml`:

```toml
dependencies = ["wrapkit @ git+https://github.com/hmorao95/wrapkit@v0.2.8"]

[tool.hatch.metadata]
allow-direct-references = true
```

In a clone, `uv sync` uses Python 3.12, set in `.python-version` (uv downloads
it if needed). On Windows, avoid 3.10 and 3.11 in a clone whose path has
non-ASCII characters, such as `C:\Users\João\...`. Those versions read the
editable install's `.pth` file in the ANSI code page, so the path comes out
garbled and `import wrapkit` fails with `ModuleNotFoundError`. On 3.10, setting
`PYTHONUTF8=1` fixes it. On 3.11 it does not, so move the clone to an
ASCII-only path or set `PYTHONPATH=src`. A regular, non-editable install is not
affected.

## Modules

| Module | What it gives a wrapper |
|---|---|
| `wrapkit.log` | `package_logger`, `configure_logging` (colored stderr console, plain DEBUG log file), `PlainFormatter`, `RedactFilter` (masks a key's query parameter and registered secrets on the `urllib3`/`httpx` loggers), `loggable` |
| `wrapkit.cache` | the `DEFAULT_CACHE_DIR` sentinel, `resolve_cache_dir` (`None` or `""` = no cache), `cache_path` and `DiskCache` (JSON files named by a digest that leaves secrets out) |
| `wrapkit.http` | `RetryPolicy` (429/5xx and transport errors, doubling backoff with jitter, numeric `Retry-After`, capped waits), `get_with_retries` (requests), `aget_with_retries` (httpx), `Throttle` / `AsyncThrottle`, `user_agent` |
| `wrapkit.frames` | `tidy_frame` / `empty_tidy` (date, dimensions, value, unit), `as_polars`, `snake`, `period_start` / `period_end` (an end at year, quarter or month precision, such as `2024`, `2024-Q2`, `2024-06` or `Jun 2024`, covers the whole period) |
| `wrapkit.incremental` | `key_strings`, `select_new`, `read_existing`, `upsert`: the pieces of `new_observations` and `update_csv` |
| `wrapkit.io` | `write_frame` (CSV, Excel, Parquet or JSON by extension) |
| `wrapkit.cli` | `resolve_out`, `as_text`, `bind_bool_flags`, `literal_keeping_codes`, `set_parse_fns`, and `run` (short `Error: ...` line and exit 1 for expected errors) |

## Example

```python
from wrapkit import cache, http, log

_log = log.package_logger("my_api")
_redact = log.RedactFilter(["api_key"]).install("my_api")


class Client:
    def __init__(self, api_key, cache_dir=cache.DEFAULT_CACHE_DIR):
        _redact.register(api_key)
        self.cache = cache.DiskCache(
            cache.resolve_cache_dir(cache_dir, "my_api"),
            ttl_seconds=86400,
            hidden=["api_key"],
            logger=_log,
        )
        self.throttle = http.Throttle(5.0, _log)
```

## Versioning

Semantic versioning. Wrappers pin a tag, so a new wrapkit release only reaches
a wrapper when its pin is bumped.

## License

MIT
