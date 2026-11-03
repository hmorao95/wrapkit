# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.2] - 2026-11-03

### Added

- `snake(name, acronyms=True)` keeps a run of capitals as a word of its own:
  `GDPValue` becomes `gdp_value` and `isCAMRelevant` `is_cam_relevant`. The
  default is unchanged (`gdpvalue`).
- `upsert(..., numeric_cols=[...])` sorts those columns as numbers, so `9`
  comes before `10` even when the stored rows were read back as text. Other
  sort columns are still compared as text.

### Fixed

- `read_existing` treats a zero-byte CSV as no prior extract. Before, pandas
  raised `EmptyDataError` on it.
- `select_new` and `key_strings` build their work frames in one step. With
  more than about 100 value columns, adding them one by one made pandas warn
  about a fragmented frame, an error under a warnings-as-errors test setup.

## [0.2.1] - 2026-11-02

### Fixed

- `RedactFilter.redact` masks registered secrets longest first. Before, a
  secret that contains a shorter registered one (a password that includes the
  user name) could be masked in pieces, leaving a fragment in the log line.
- `configure_logging` accepts a non-string level and rejects it with its usual
  `ValueError`. Fire turns `--log-level 10` into an int, which used to crash on
  `.upper()`.
- `wrapkit.cli.run` returns what the command returned, as `fire.Fire` does, so
  a wrapper whose `main()` passes that value on can use it.

## [0.2.0] - 2026-11-01

### Added

- `wrapkit.cli.run` takes `error_prefix` (default `"Error"`) and
  `failure_message` (default `"Command failed"`), so a CLI in another language
  prints and logs its own words (ine-pt-api uses Portuguese). The
  defaults keep 0.1.0's output.

### Changed

- `pre-commit` is no longer a dev dependency: run the hooks with
  `uvx pre-commit` (for example `uvx pre-commit install`). This drops
  `virtualenv` and `filelock` from `uv.lock`; filelock has no patched release
  for Python 3.10.

## [0.1.0] - 2026-11-01

### Added

- `wrapkit.log`: package logger, CLI console and file handlers, plain-text file
  tracebacks, secret redaction on the HTTP loggers.
- `wrapkit.cache`: `DEFAULT_CACHE_DIR`, `resolve_cache_dir`, `cache_path`,
  `DiskCache`.
- `wrapkit.http`: `RetryPolicy`, `get_with_retries`, `aget_with_retries`,
  `Throttle`, `AsyncThrottle`, `user_agent`.
- `wrapkit.frames`: tidy-shape helpers, `as_polars`, `snake`, `period_start`,
  `period_end`.
- `wrapkit.incremental`: `key_strings`, `select_new`, `read_existing`, `upsert`.
- `wrapkit.io`: `write_frame`, `frame_format`.
- `wrapkit.cli`: `resolve_out`, `as_text`, `bind_bool_flags`,
  `literal_keeping_codes`, `set_parse_fns`, `run`.

[Unreleased]: https://github.com/hmorao95/wrapkit/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/hmorao95/wrapkit/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/hmorao95/wrapkit/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/hmorao95/wrapkit/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/hmorao95/wrapkit/releases/tag/v0.1.0
