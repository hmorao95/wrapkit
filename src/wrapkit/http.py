"""
HTTP helpers: retries with backoff, a request-rate throttle, a User-Agent.

:func:`get_with_retries` (``requests``) and :func:`aget_with_retries`
(``httpx``) retry rate limits (429), server hiccups (500, 502, 503, 504) and
transport failures (refused or dropped connections, timeouts, truncated
bodies). The wait doubles each time, with jitter, and a numeric
``Retry-After`` header on a 429 or 503 wins; any wait is capped. Every other
answer is returned at once, whatever its status: the caller decides what an
error looks like for its API.

Retry lines are logged at WARNING with only a caller-chosen label and a short
reason (``HTTP 503`` or the exception class name), never the URL, so a key in
the query string cannot leak through them.
"""

from __future__ import annotations

import asyncio
import random
import ssl
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
import requests

if TYPE_CHECKING:
    import logging

__all__ = [
    "ASYNC_RETRY_ERRORS",
    "RETRY_ERRORS",
    "AsyncThrottle",
    "RetryPolicy",
    "Throttle",
    "aget_with_retries",
    "get_with_retries",
    "user_agent",
]

#: ``requests`` transport failures worth another try. Anything else, such as a
#: malformed URL, fails at once, and so does a failed certificate check (an
#: ``SSLError`` is a ``ConnectionError``; see :func:`_cannot_heal`).
RETRY_ERRORS: tuple[type[Exception], ...] = (
    requests.ConnectionError,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
)

#: ``httpx`` transport failures worth another try.
ASYNC_RETRY_ERRORS: tuple[type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)


def _cannot_heal(exc: BaseException) -> bool:
    """
    Tell whether a transport error is a certificate failure a retry won't fix.

    ``requests`` and ``httpx`` wrap :class:`ssl.SSLCertVerificationError` a few
    layers deep (as an argument, a ``reason`` or the cause). Other SSL errors,
    such as a connection dropped mid-handshake, can be transient and are still
    retried.

    Args:
        exc (BaseException): The error a request raised.

    Returns:
        bool: ``True`` when a certificate check failed somewhere in the chain.
    """
    seen: set[int] = set()
    pending: list[object] = [exc]
    while pending:
        err = pending.pop()
        if not isinstance(err, BaseException) or id(err) in seen:
            continue
        seen.add(id(err))
        if isinstance(err, ssl.SSLCertVerificationError):
            return True
        pending.extend((err.__cause__, err.__context__, getattr(err, "reason", None)))
        pending.extend(err.args)
    return False


def _jitter() -> float:
    """
    Return a random factor that spreads retry waits by plus or minus 20%.

    Clients that failed together would otherwise retry together. Tests patch
    this (or pass ``jitter=False``) to get exact waits.

    Returns:
        float: A factor between 0.8 and 1.2.
    """
    return random.uniform(0.8, 1.2)  # ruff: ignore[suspicious-non-cryptographic-random-usage] - jitter, not cryptography


@dataclass(frozen=True)
class RetryPolicy:
    """
    When to retry a request and how long to wait.

    Attributes:
        max_retries (int): Retries after the first attempt (``0`` disables
            retrying).
        statuses (frozenset[int]): HTTP statuses that are retried.
        retry_after_statuses (frozenset[int]): Statuses whose numeric
            ``Retry-After`` header sets the wait.
        backoff (float): First wait in seconds, doubled for each further retry.
        max_wait (float): Cap on any single wait, ``Retry-After`` included.
        jitter (bool): Spread waits by plus or minus 20%.
    """

    max_retries: int = 4
    statuses: frozenset[int] = frozenset({429, 500, 502, 503, 504})
    retry_after_statuses: frozenset[int] = frozenset({429, 503})
    backoff: float = 1.0
    max_wait: float = 30.0
    jitter: bool = True

    def wait(
        self, attempt: int, status: int | None = None, retry_after: str | None = None
    ) -> float:
        """
        Return how long to wait after failed attempt ``attempt``.

        HTTP-date ``Retry-After`` values are not parsed and fall back to the
        backoff.

        Args:
            attempt (int): The attempt that just failed, starting at 1.
            status (int | None): Its HTTP status, or ``None`` for a transport
                error.
            retry_after (str | None): Its ``Retry-After`` header, if any.

        Returns:
            float: Seconds to wait.
        """
        if status in self.retry_after_statuses and retry_after:
            try:
                seconds = float(retry_after.strip())
            except ValueError:
                seconds = -1.0
            if seconds >= 0:  # also False for NaN
                return min(seconds, self.max_wait)
        factor = _jitter() if self.jitter else 1.0
        return min(self.backoff * 2.0 ** (attempt - 1) * factor, self.max_wait)


class Throttle:
    """
    Keep at least ``1 / requests_per_second`` seconds between requests.

    Call :meth:`wait` right before each network request (never for cache hits).
    The first request never waits.
    """

    def __init__(
        self,
        requests_per_second: float | None,
        logger: logging.Logger | None = None,
    ) -> None:
        """
        Set the rate.

        Args:
            requests_per_second (float | None): The cap, or ``None`` (or ``0``)
                for no throttle.
            logger (logging.Logger | None): Receives a DEBUG line per sleep.
        """
        self.interval = 1.0 / requests_per_second if requests_per_second else 0.0
        self.logger = logger
        self._last: float | None = None

    def _shortfall(self) -> float:
        """
        Return how long the next request must wait (``0.0`` for none).

        Returns:
            float: Seconds still to wait.
        """
        if not self.interval or self._last is None:
            return 0.0
        return self.interval - (time.monotonic() - self._last)

    def _log(self, wait: float) -> None:
        """
        Log a sleep at DEBUG.

        Args:
            wait (float): The sleep in seconds.
        """
        if self.logger is not None:
            self.logger.debug("Throttling: sleeping %.3fs", wait)

    def wait(self) -> None:
        """Sleep just enough to respect the rate, then record the request time."""
        if not self.interval:
            return
        wait = self._shortfall()
        if wait > 0:
            self._log(wait)
            time.sleep(wait)
        self._last = time.monotonic()


class AsyncThrottle(Throttle):
    """
    The async twin of :class:`Throttle`; its waits do not block the loop.

    One throttle can serve several event loops in turn (one ``asyncio.run`` per
    call, for example): its lock is made for the loop that is running.
    """

    def __init__(
        self,
        requests_per_second: float | None,
        logger: logging.Logger | None = None,
    ) -> None:
        """
        Set the rate.

        Args:
            requests_per_second (float | None): The cap, or ``None`` (or ``0``)
                for no throttle.
            logger (logging.Logger | None): Receives a DEBUG line per sleep.
        """
        super().__init__(requests_per_second, logger)
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def await_turn(self) -> None:
        """Sleep just enough to respect the rate; concurrent callers queue up."""
        if not self.interval:
            return
        loop = asyncio.get_running_loop()
        if self._lock is None or self._loop is not loop:
            # A lock that once had a waiter belongs to that loop for good.
            self._lock, self._loop = asyncio.Lock(), loop
        async with self._lock:
            wait = self._shortfall()
            if wait > 0:
                self._log(wait)
                await asyncio.sleep(wait)
            self._last = time.monotonic()


def _log_retry(
    logger: logging.Logger | None,
    label: str,
    wait: float,
    attempt: int,
    attempts: int,
    reason: str,
) -> None:
    """
    Log at WARNING that a request is about to be retried.

    Args:
        logger (logging.Logger | None): Where to log, if anywhere.
        label (str): What is being requested, without any secret.
        wait (float): Seconds until the retry.
        attempt (int): The attempt about to be made, starting at 2.
        attempts (int): Total attempts allowed.
        reason (str): Why the previous attempt failed.
    """
    if logger is not None:
        logger.warning(
            "Retrying GET %s in %.1fs (attempt %d/%d): %s",
            label,
            wait,
            attempt,
            attempts,
            reason,
        )


def get_with_retries(
    session: requests.Session,
    url: str,
    *,
    policy: RetryPolicy | None = None,
    throttle: Throttle | None = None,
    logger: logging.Logger | None = None,
    label: str | None = None,
    **kwargs: Any,
) -> requests.Response:
    """
    GET ``url`` with ``session``, retrying transient failures.

    Args:
        session (requests.Session): The session to use.
        url (str): The URL.
        policy (RetryPolicy | None): Retry rules; the default
            :class:`RetryPolicy` when ``None``.
        throttle (Throttle | None): Waited on before every attempt.
        logger (logging.Logger | None): Receives retry warnings and a DEBUG
            line per answer.
        label (str | None): Secret-free name of the request for log lines;
            the URL when ``None`` (only safe when the URL holds no key).
        **kwargs (Any): Passed to ``session.get`` (``params``, ``timeout``,
            ``headers``, ...).

    Returns:
        requests.Response: The final answer, whatever its status.

    Raises:
        requests.RequestException: The last transport error, once retries are
            used up, or at once when it is not worth retrying. Its message may
            contain the full URL: redact before showing it.
    """
    policy = policy or RetryPolicy()
    name = url if label is None else label
    attempts = policy.max_retries + 1
    attempt = 1
    while True:
        if throttle is not None:
            throttle.wait()
        started = time.monotonic()
        try:
            resp = session.get(url, **kwargs)
        except requests.RequestException as exc:
            if (
                attempt >= attempts
                or not isinstance(exc, RETRY_ERRORS)
                or _cannot_heal(exc)
            ):
                raise
            reason = type(exc).__name__
            wait = policy.wait(attempt)
        else:
            if logger is not None:
                logger.debug(
                    "HTTP %s for %s in %.2fs",
                    resp.status_code,
                    name,
                    time.monotonic() - started,
                )
            if attempt >= attempts or resp.status_code not in policy.statuses:
                return resp
            reason = f"HTTP {resp.status_code}"
            wait = policy.wait(
                attempt, resp.status_code, resp.headers.get("Retry-After")
            )
        attempt += 1
        _log_retry(logger, name, wait, attempt, attempts, reason)
        time.sleep(wait)


async def aget_with_retries(
    client: httpx.AsyncClient,
    url: str,
    *,
    policy: RetryPolicy | None = None,
    throttle: AsyncThrottle | None = None,
    logger: logging.Logger | None = None,
    label: str | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """
    The async twin of :func:`get_with_retries`, for ``httpx``.

    Args:
        client (httpx.AsyncClient): The client to use.
        url (str): The URL.
        policy (RetryPolicy | None): Retry rules; the default
            :class:`RetryPolicy` when ``None``.
        throttle (AsyncThrottle | None): Awaited before every attempt.
        logger (logging.Logger | None): Receives retry warnings and a DEBUG
            line per answer.
        label (str | None): Secret-free name of the request for log lines;
            the URL when ``None``.
        **kwargs (Any): Passed to ``client.get``.

    Returns:
        httpx.Response: The final answer, whatever its status.

    Raises:
        httpx.HTTPError: The last transport error, once retries are used up,
            or at once when it is not worth retrying.
    """
    policy = policy or RetryPolicy()
    name = url if label is None else label
    attempts = policy.max_retries + 1
    attempt = 1
    while True:
        if throttle is not None:
            await throttle.await_turn()
        started = time.monotonic()
        try:
            resp = await client.get(url, **kwargs)
        except httpx.HTTPError as exc:
            if (
                attempt >= attempts
                or not isinstance(exc, ASYNC_RETRY_ERRORS)
                or _cannot_heal(exc)
            ):
                raise
            reason = type(exc).__name__
            wait = policy.wait(attempt)
        else:
            if logger is not None:
                logger.debug(
                    "HTTP %s for %s in %.2fs",
                    resp.status_code,
                    name,
                    time.monotonic() - started,
                )
            if attempt >= attempts or resp.status_code not in policy.statuses:
                return resp
            reason = f"HTTP {resp.status_code}"
            wait = policy.wait(
                attempt, resp.status_code, resp.headers.get("Retry-After")
            )
        attempt += 1
        _log_retry(logger, name, wait, attempt, attempts, reason)
        await asyncio.sleep(wait)


def user_agent(product: str, version: str, url: str) -> str:
    """
    Build the browser-like User-Agent the wrappers send.

    Some APIs reject requests without a browser-like User-Agent and answer with
    an error page instead of data.

    Args:
        product (str): Product token, such as ``"eia-api-wrapper"``.
        version (str): Its version.
        url (str): A URL identifying the data source or the project.

    Returns:
        str: ``Mozilla/5.0 (compatible; <product>/<version>; +<url>)``.
    """
    return f"Mozilla/5.0 (compatible; {product}/{version}; +{url})"
