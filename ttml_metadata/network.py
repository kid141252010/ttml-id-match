from __future__ import annotations

import http.client
import os
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_DELAY_SECONDS = 0.2

_RETRYABLE_HTTP_STATUS_CODES = {408, 429}

T = TypeVar("T")


_SOURCE_PROXY_ENV = {
    "apple_music": "TTML_PROXY_APPLE_MUSIC",
    "qq_music": "TTML_PROXY_QQ_MUSIC",
    "ncm_music": "TTML_PROXY_NCM_MUSIC",
    "spotify": "TTML_PROXY_SPOTIFY",
}


@dataclass(frozen=True)
class NetworkPolicy:
    source_proxies: dict[str, str]
    global_proxy: str | None = None

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "NetworkPolicy":
        values = environ if environ is not None else os.environ
        source_proxies = {
            source: proxy
            for source, env_name in _SOURCE_PROXY_ENV.items()
            if (proxy := _clean_proxy_value(values.get(env_name)))
        }
        global_proxy = (
            _clean_proxy_value(values.get("TTML_PROXY_ALL"))
            or _clean_proxy_value(values.get("HTTPS_PROXY"))
            or _clean_proxy_value(values.get("HTTP_PROXY"))
        )
        return cls(source_proxies=source_proxies, global_proxy=global_proxy)

    def proxy_for(self, source: str) -> str | None:
        return self.source_proxies.get(source) or self.global_proxy


def proxy_url_for_source(source: str, environ: dict[str, str] | None = None) -> str | None:
    return NetworkPolicy.from_env(environ).proxy_for(source)


def _clean_proxy_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.casefold() in {"0", "false", "none", "off", "no"}:
        return None
    return cleaned


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
    proxy_url: str | None = None,
):
    return retry_call(lambda: _urlopen(request, timeout=timeout, proxy_url=proxy_url))


def _urlopen(
    request: urllib.request.Request,
    *,
    timeout: int,
    proxy_url: str | None = None,
):
    if not proxy_url:
        return urllib.request.urlopen(request, timeout=timeout)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler(
            {
                "http": proxy_url,
                "https": proxy_url,
            }
        )
    )
    return opener.open(request, timeout=timeout)


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
