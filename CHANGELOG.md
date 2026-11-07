# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.8] - 2026-11-07

### Changed

- `snake` drops accents and spells out German letters instead of turning
  them into underscores: `Região` becomes `regiao` (was `regi_o`), `Größe`
  `groesse` (was `gr_e`). Other letters, such as `ø`, are kept. Column names
  that had such letters change in the wrappers that use `snake`.

### Fixed

- `RedactFilter` now also masks secrets in a record's traceback and stack,
  not only its message. `run()` logs every expected error with its
  traceback, and a `requests` error quotes the full URL, so an API key in
  the query string reached `--log-file` in clear text. A traceback that held
  a secret is kept as cleaned text only (the record's `exc_info` is
  cleared); one without a secret is left as it was.
- `key_strings` (and so `select_new` and `upsert`) now keeps a date's time
  zone and fraction of a second in the key. Times with a zone are keyed in
  UTC, so the two 02:00 hours of the night DST ends no longer share a key,
  and neither do two stamps in the same second; `upsert` used to keep one
  row and drop the other. `upsert` now also reads back stored times with
  mixed UTC offsets (both sides of a DST change) and returns them in the
  fresh column's zone. Naive whole-second dates key exactly as before.
- `read_existing(text_cols=...)` keeps a missing value missing. On pandas
  before 3 it became the text `"nan"`, which never matched the fresh row's
  key again, so `select_new` returned that row as new on every run.
- `AsyncThrottle` works under more than one event loop. Its lock used to
  belong to the first loop it queued callers in, so a client reused under a
  second `asyncio.run()` raised `RuntimeError` on its next burst of requests.
- `period_start` and `period_end` read the SDMX month spelling `2024-M06`
  (also `2024M06`, `2024-M6`, `2024m6`) as June 2024. pandas read it as
  `2024-01-01 00:00:06`, so an `end` bound silently kept only January 1.
  Other year-plus-code labels that are not supported (`2024-M13`,
  `2024-S1`, `2024-W23`) now raise `ValueError` instead of being read as a
  time of day.
- `DiskCache` with `ttl_seconds=0` (or less) never serves an entry. A file
  written a moment ago could carry a time a hair ahead of the clock, and the
  negative age counted as fresh.
- `literal_keeping_codes` no longer quotes the zero-led parts of a date or a
  time: `2024-01-15` used to arrive as `2024-'01'-15`, `06/2024` as
  `'06'/2024`. Codes such as `01` are still kept as text.
- `resolve_out` keeps `./data.csv` (or `.\data.csv`) in the current
  directory, as its docstring says; only a bare name goes to `outputs/`.
- `get_with_retries` and `aget_with_retries` no longer retry a failed
  certificate check (an `SSLError` is a `ConnectionError`), which used to
  cost about 15 s of backoff before the same error. Other SSL errors, such
  as a dropped handshake, are still retried.

## [0.2.7] - 2026-11-07

### Fixed

- `period_start` and `period_end` now read month labels in any common
  spelling (`2024-6`, `2024/06`, `06/2024`, `2024.06`, `Jun 2024`) as the
  whole month. They used to fall through to `pandas.Timestamp`, which reads
  them as the month's first day, so an `end="2024-6"` bound stopped on
  June 1 instead of covering June. `2024-06`, `202406`, years, quarters and
  full dates behave as before.

## [0.2.6] - 2026-11-04

### Fixed

- `read_existing` now reads stored numbers back exactly. Before pandas 3.1,
  `pd.to_numeric` parsed about one full-precision float in eight one unit in
  the last place off the value `to_csv` wrote, so `select_new` counted those
  unchanged rows as revisions and `update_csv` rewrote them on every run
  (pandas 2.3, which Python 3.10 gets, and 3.0). Floats are now parsed with
  `float`; integers and text that is not a number are handled as before.

## [0.2.5] - 2026-11-04

### Fixed

- `key_strings` (and so `select_new` and `upsert`) now writes a whole
  number spelled as a decimal as an integer: `110.0` and `110` give the same
  key. pandas spells a number by its column's dtype, and a numeric key column
  turns float when a fetch has a gap in it, so the same key used to read
  `110` in one run and `110.0` in the next (or in the stored CSV). A revised
  row was then added next to the old one instead of replacing it. Codes
  without a decimal point, such as `"01"`, are unchanged; a text key spelled
  `"110.0"` now also matches `"110"`.

## [0.2.4] - 2026-11-04

### Fixed

- `read_existing` no longer turns codes such as `"NA"` (Namibia,
  Netherlands Antilles), `"None"` or `"null"` into missing values when it
  reads a stored CSV. Only an empty field counts as missing now. Before,
  those rows lost their key, so every update re-added them as new and the
  file gained a duplicate row with a blank key.
- `select_new` no longer crashes with "Cannot mask with non-boolean array
  containing NA" when a compared column is a nullable dtype and one side is
  `<NA>`, for example a unit or flag that appears or disappears. That change
  now counts as a revision.
- `DiskCache.put` writes through a temp file and `Path.replace`, so an
  interrupted write no longer leaves a truncated entry. `DiskCache.get`
  treats an entry it cannot decode as a miss rather than raising on every
  call until the TTL runs out.

## [0.2.3] - 2026-11-04

### Changed

- `.gitignore` now also ignores LaTeX build files (`*.aux`, `*.log`,
  `*.synctex.gz`, `*.fdb_latexmk` and the like) and Jupyter checkpoints
  (`.ipynb_checkpoints/`). No change to the package.

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
- `select_new` and `key_strings` build their work frames in one step, and
  `read_existing` returns a consolidated frame after parsing a CSV. With more
  than about 100 value columns, pandas warned about a fragmented frame, an
  error under a warnings-as-errors test setup.

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

[Unreleased]: https://github.com/hmorao95/wrapkit/compare/v0.2.8...HEAD
[0.2.8]: https://github.com/hmorao95/wrapkit/compare/v0.2.7...v0.2.8
[0.2.7]: https://github.com/hmorao95/wrapkit/compare/v0.2.6...v0.2.7
[0.2.6]: https://github.com/hmorao95/wrapkit/compare/v0.2.5...v0.2.6
[0.2.5]: https://github.com/hmorao95/wrapkit/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/hmorao95/wrapkit/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/hmorao95/wrapkit/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/hmorao95/wrapkit/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/hmorao95/wrapkit/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/hmorao95/wrapkit/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/hmorao95/wrapkit/releases/tag/v0.1.0
