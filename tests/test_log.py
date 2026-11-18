"""Tests for :mod:`wrapkit.log`."""

from __future__ import annotations

import logging
import logging.handlers
from typing import TYPE_CHECKING

import pytest

from wrapkit import log

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

# Every logger in urllib3 2.x and httpcore 1.x that writes a request URL or HTTP
# headers. A filter on a parent logger does not run on its children's records,
# so install() must cover each of these names exactly.
URL_OR_HEADER_LOGGERS = (
    "urllib3.connectionpool",
    "urllib3.util.retry",
    "httpx",
    "urllib3.connection",
    "urllib3.poolmanager",
    "httpcore.http11",
    "httpcore.http2",
)

SECRET = "supersecretkey123"  # ruff: ignore[hardcoded-password-string] - a dummy key
URL = f"https://h:443/v2/x?api_key={SECRET}&y=1"
LOCATION = f"https://h/v2/x/?api_key={SECRET}"


@pytest.fixture
def logger() -> Iterator[logging.Logger]:
    """
    Yield a fresh logger and remove its handlers and filters afterwards.

    Yields:
        logging.Logger: The logger ``wrapkit_test``.
    """
    lg = log.package_logger("wrapkit_test")
    yield lg
    for handler in list(lg.handlers):
        lg.removeHandler(handler)
        handler.close()
    for flt in list(lg.filters):
        lg.removeFilter(flt)
    lg.setLevel(logging.NOTSET)


def test_package_logger_adds_one_null_handler(logger: logging.Logger) -> None:
    """Calling it twice keeps a single NullHandler."""
    again = log.package_logger("wrapkit_test")
    assert again is logger
    nulls = [h for h in logger.handlers if isinstance(h, logging.NullHandler)]
    assert len(nulls) == 1


def test_redact_masks_param_and_registered_secret() -> None:
    """The parameter value and registered secrets become ***."""
    flt = log.RedactFilter(["api_key"])
    flt.register("SECRETSECRET")
    text = "GET /x?api_key=abc&y=1 used SECRETSECRET"
    assert flt.redact(text) == "GET /x?api_key=***&y=1 used ***"


def test_redact_word_boundary() -> None:
    """``key`` does not match inside ``api_key``."""
    flt = log.RedactFilter(["key"])
    assert flt.redact("?api_key=abc&key=def") == "?api_key=abc&key=***"


def test_redact_without_params_only_masks_secrets() -> None:
    """With no parameter names only registered secrets are masked."""
    flt = log.RedactFilter()
    flt.register("longsecret1")
    assert flt.redact("a=1 longsecret1") == "a=1 ***"


def test_redact_masks_overlapping_secrets_whole() -> None:
    """A secret containing a shorter one is masked whole, not in pieces."""
    flt = log.RedactFilter()
    for secret in ("username1", "username1-password", "1-password"):
        flt.register(secret)
    assert flt.redact("login username1-password") == "login ***"


def test_register_ignores_short_and_none() -> None:
    """Short or missing secrets are not registered."""
    flt = log.RedactFilter()
    flt.register(None)
    flt.register("short")
    assert flt.secrets == set()


def test_filter_freezes_cleaned_message(
    logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    """A record with a secret is rewritten; others pass untouched."""
    flt = log.RedactFilter(["token"])
    logger.addFilter(flt)
    logger.propagate = True
    with caplog.at_level(logging.DEBUG, logger="wrapkit_test"):
        logger.warning("url %s", "https://h/?token=zzz")
        logger.warning("plain %s", "text")
    assert caplog.records[0].getMessage() == "url https://h/?token=***"
    assert caplog.records[0].args == ()
    assert caplog.records[1].args == ("text",)


@pytest.fixture
def installed() -> Iterator[log.RedactFilter]:
    """
    Yield a filter installed as a wrapper does, then detach it everywhere.

    Yields:
        log.RedactFilter: A filter for ``api_key`` with :data:`SECRET`
        registered, on the HTTP loggers and ``wrapkit_test``.
    """
    flt = log.RedactFilter(["api_key"]).install("wrapkit_test")
    flt.register(SECRET)
    yield flt
    for name in (*log.HTTP_LOGGERS, "wrapkit_test"):
        logging.getLogger(name).removeFilter(flt)


def test_install_attaches_once() -> None:
    """install() covers the HTTP loggers and extra names, without duplicates."""
    assert set(URL_OR_HEADER_LOGGERS) <= set(log.HTTP_LOGGERS)
    flt = log.RedactFilter(["api_key"])
    try:
        assert flt.install("wrapkit_test") is flt
        flt.install("wrapkit_test")
        for name in (*log.HTTP_LOGGERS, "wrapkit_test"):
            assert logging.getLogger(name).filters.count(flt) == 1
    finally:
        for name in (*log.HTTP_LOGGERS, "wrapkit_test"):
            logging.getLogger(name).removeFilter(flt)


@pytest.mark.parametrize(
    ("name", "level", "msg", "args"),
    [
        # urllib3/connection.py: a response header block that fails to parse.
        pytest.param(
            "urllib3.connection",
            logging.WARNING,
            "Failed to parse headers (url=%s): %s",
            (URL, "[MissingHeaderBodySeparatorDefect()], unparsed data: ''"),
            id="urllib3.connection",
        ),
        # urllib3/poolmanager.py: a redirect followed by a PoolManager.
        pytest.param(
            "urllib3.poolmanager",
            logging.INFO,
            "Redirecting %s -> %s",
            (URL, LOCATION),
            id="urllib3.poolmanager",
        ),
        # httpcore/_trace.py formats the message itself; these are the
        # response-header traces of http11.py and http2.py.
        pytest.param(
            "httpcore.http11",
            logging.DEBUG,
            "receive_response_headers.complete return_value="
            + repr(
                (
                    b"HTTP/1.1",
                    301,
                    b"Moved Permanently",
                    [(b"Location", LOCATION.encode()), (b"X-Echo", SECRET.encode())],
                )
            ),
            (),
            id="httpcore.http11",
        ),
        pytest.param(
            "httpcore.http2",
            logging.DEBUG,
            "receive_response_headers.complete return_value="
            + repr((301, [(b"location", LOCATION.encode())])),
            (),
            id="httpcore.http2",
        ),
    ],
)
def test_install_masks_child_http_loggers(
    installed: log.RedactFilter,
    caplog: pytest.LogCaptureFixture,
    name: str,
    level: int,
    msg: str,
    args: tuple[object, ...],
) -> None:
    """A key logged on a URL- or header-writing logger reaches handlers masked."""
    assert installed in logging.getLogger(name).filters
    # A handler on the parent ("urllib3" or "httpcore"), besides caplog's on root.
    parent = logging.getLogger(name.split(".", maxsplit=1)[0])
    buffer = logging.handlers.BufferingHandler(capacity=100)
    parent.addHandler(buffer)
    try:
        with caplog.at_level(logging.DEBUG, logger=name):
            logging.getLogger(name).log(level, msg, *args)
        at_parent = [r.getMessage() for r in buffer.buffer]
    finally:
        parent.removeHandler(buffer)
        buffer.close()
    at_root = [r.getMessage() for r in caplog.records if r.name == name]
    assert len(at_root) == len(at_parent) == 1
    for message in (*at_root, *at_parent):
        assert SECRET not in message
        assert "api_key=***" in message
    assert SECRET not in caplog.text


def test_loggable_hides_names() -> None:
    """Hidden parameters are left out of the log text."""
    params = [("api_key", "s"), ("a", "1"), ("b", "2")]
    assert log.loggable(params, ["api_key"]) == "a=1&b=2"


def test_configure_logging_rejects_unknown_level(logger: logging.Logger) -> None:
    """An unknown level fails before any handler is touched."""
    with pytest.raises(ValueError, match="log_level must be one of"):
        log.configure_logging(logger, "loud")


def test_configure_logging_rejects_numeric_level(logger: logging.Logger) -> None:
    """Fire turns ``--log-level 10`` into an int; it fails cleanly, no crash."""
    with pytest.raises(ValueError, match="got 10"):
        log.configure_logging(logger, 10)


def test_configure_logging_replaces_its_handlers(logger: logging.Logger) -> None:
    """A second call replaces the CLI handlers instead of stacking them."""
    log.configure_logging(logger, "INFO")
    log.configure_logging(logger, "debug")
    ours = [h for h in logger.handlers if getattr(h, log.CLI_HANDLER_FLAG, False)]
    assert len(ours) == 1
    assert logger.level == logging.DEBUG


def test_configure_logging_custom_flag(logger: logging.Logger) -> None:
    """The marker attribute can be chosen by the caller."""
    log.configure_logging(logger, "WARNING", flag="_mine")
    assert any(getattr(h, "_mine", False) for h in logger.handlers)
    assert logger.level == logging.WARNING


def test_file_log_is_plain_debug(logger: logging.Logger, tmp_path: Path) -> None:
    """The file gets DEBUG records and tracebacks with no ANSI codes."""
    path = tmp_path / "run.log"
    log.configure_logging(logger, "ERROR", str(path))
    logger.debug("detail")
    try:
        msg = "boom"
        raise RuntimeError(msg)  # ruff: ignore[raise-within-try]
    except RuntimeError:
        logger.exception("failed")
    for handler in logger.handlers:
        handler.flush()
    text = path.read_text("utf-8")
    assert "detail" in text
    assert "RuntimeError: boom" in text
    assert "\x1b[" not in text
    assert logger.level == logging.DEBUG


def test_plain_formatter_restores_cache() -> None:
    """A colored cache is re-rendered plain, then put back after formatting."""
    try:
        msg = "boom"
        raise RuntimeError(msg)  # ruff: ignore[raise-within-try]
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            "n", logging.ERROR, __file__, 1, "m", (), sys.exc_info()
        )
    record.exc_text = "\x1b[31mcolored\x1b[0m"
    out = log.PlainFormatter("%(message)s").format(record)
    assert "RuntimeError: boom" in out
    assert "\x1b[" not in out
    assert record.exc_text == "\x1b[31mcolored\x1b[0m"


def test_plain_formatter_uses_redacted_text() -> None:
    """A record cleaned by RedactFilter has only text; it is written as is."""
    record = logging.LogRecord("n", logging.ERROR, __file__, 1, "m", (), None)
    record.exc_text = "Traceback: api_key=***"
    out = log.PlainFormatter("%(message)s").format(record)
    assert out == "m\nTraceback: api_key=***"


def test_filter_redacts_tracebacks_in_the_log_file(
    logger: logging.Logger, tmp_path: Path
) -> None:
    """A key quoted in an exception message is masked in the logged traceback."""
    path = tmp_path / "run.log"
    log.configure_logging(logger, "ERROR", str(path))
    flt = log.RedactFilter(["api_key"]).install(logger.name)
    flt.register("supersecretkey123")
    try:
        msg = "Max retries exceeded with url: /v2?api_key=supersecretkey123"
        raise ConnectionError(msg)  # ruff: ignore[raise-within-try]
    except ConnectionError:
        logger.debug("Command failed", exc_info=True)
        logger.debug("no secret here", exc_info=(None, None, None))
    logger.warning("where supersecretkey123", stack_info=True)
    for handler in logger.handlers:
        handler.flush()
    text = path.read_text("utf-8")
    assert "supersecretkey123" not in text
    assert "ConnectionError: Max retries exceeded with url: /v2?api_key=***" in text
    assert "Traceback (most recent call last)" in text
    for name in log.HTTP_LOGGERS:
        logging.getLogger(name).removeFilter(flt)


def test_filter_keeps_clean_tracebacks_structured() -> None:
    """A traceback without a secret keeps its exc_info for other handlers."""
    flt = log.RedactFilter(["api_key"])
    try:
        msg = "boom"
        raise RuntimeError(msg)  # ruff: ignore[raise-within-try]
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            "n", logging.ERROR, __file__, 1, "m", (), sys.exc_info()
        )
    assert flt.filter(record)
    assert record.exc_info is not None
    assert record.exc_text is None


def test_filter_redacts_a_cached_traceback() -> None:
    """Traceback text another handler already rendered is cleaned too."""
    flt = log.RedactFilter(["token"])
    record = logging.LogRecord("n", logging.ERROR, __file__, 1, "m", (), None)
    record.exc_text = "Traceback ... ValueError: /x?token=abc"
    assert flt.filter(record)
    assert record.exc_text == "Traceback ... ValueError: /x?token=***"
