from __future__ import annotations

import http.client
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import TypeVar

DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_DELAY_SECONDS = 0.2

_RETRYABLE_HTTP_STATUS_CODES = {408, 429}

T = TypeVar("T")


def retry_call(
    operation: Callable[[], T],
    *,
    attempts: int = DEFAULT_RETRY_ATTEMPTS,
    delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
    sleep_func: Callable[[float], None] = time.sleep,
) -> T:
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    for index in range(attempts):
        try:
            return operation()
        except Exception as exc:
            if index == attempts - 1 or not _is_retryable_network_error(exc):
                raise
            _close_error_response(exc)
            sleep_func(delay_seconds * (2**index))

    raise RuntimeError("unreachable retry state")


def urlopen_with_retry(
    request: urllib.request.Request,
    *,
    timeout: int,
):
    return retry_call(lambda: urllib.request.urlopen(request, timeout=timeout))


def _is_retryable_network_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _RETRYABLE_HTTP_STATUS_CODES or 500 <= exc.code <= 599
    return isinstance(
        exc,
        (
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
            ConnectionError,
            http.client.IncompleteRead,
            http.client.RemoteDisconnected,
        ),
    )


def _close_error_response(exc: Exception) -> None:
    close = getattr(exc, "close", None)
    if callable(close):
        close()
