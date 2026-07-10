import json
import unittest

import httpx

from ttml_metadata.network import NetworkPolicy
from ttml_metadata.apple_music import AppleMusicClient
from ttml_metadata.models import SpotifyCredentials
from ttml_metadata.ncm_music import NCMusicClient
from ttml_metadata.qq_music import QQMusicClient
from ttml_metadata.spotify import SpotifyClient
from ttml_metadata.v2.transport import HttpResponse, HttpxTransport


class HttpxTransportTests(unittest.TestCase):
    def test_retries_retryable_status_and_reuses_client_for_same_proxy(self):
        attempts = []
        created = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            if len(attempts) == 1:
                return httpx.Response(503, request=request)
            return httpx.Response(200, json={"ok": True}, request=request)

        def client_factory(proxy):
            created.append(proxy)
            return httpx.Client(transport=httpx.MockTransport(handler))

        transport = HttpxTransport(
            policy=NetworkPolicy(source_proxies={}, global_proxy=None),
            attempts=2,
            retry_delay_seconds=0,
            client_factory=client_factory,
        )

        first = transport.request("qq_music", "GET", "https://example.test/one")
        second = transport.request("qq_music", "GET", "https://example.test/two")

        self.assertEqual(first.json(), {"ok": True})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(created, [None])

    def test_source_proxy_selects_a_distinct_connection_pool(self):
        created = []

        def client_factory(proxy):
            created.append(proxy)
            return httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, content=b"ok", request=request)
                )
            )

        transport = HttpxTransport(
            policy=NetworkPolicy(
                source_proxies={"apple_music": "http://apple-proxy:8080"},
                global_proxy="http://global-proxy:8080",
            ),
            client_factory=client_factory,
        )

        transport.request("apple_music", "GET", "https://example.test/apple")
        transport.request("qq_music", "GET", "https://example.test/qq")
        transport.request("apple_music", "GET", "https://example.test/apple-2")

        self.assertEqual(
            created,
            ["http://apple-proxy:8080", "http://global-proxy:8080"],
        )

    def test_non_retryable_http_error_is_raised_immediately(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(404, request=request)

        transport = HttpxTransport(
            attempts=3,
            retry_delay_seconds=0,
            client_factory=lambda _proxy: httpx.Client(
                transport=httpx.MockTransport(handler)
            ),
        )

        with self.assertRaises(httpx.HTTPStatusError):
            transport.request("spotify", "GET", "https://example.test/missing")
        self.assertEqual(calls, 1)

    def test_provider_clients_use_the_shared_transport_adapter(self):
        class FakeTransport:
            def __init__(self):
                self.calls = []

            def request(self, source, method, url, *, headers=None, content=None):
                self.calls.append((source, method, url, headers, content))
                if "api/token" in url:
                    payload = {"access_token": "token"}
                else:
                    payload = {}
                return HttpResponse(
                    status_code=200,
                    headers={},
                    content=json.dumps(payload).encode("utf-8"),
                )

        transport = FakeTransport()
        AppleMusicClient(transport=transport)._read_text("https://example.test/apple")
        QQMusicClient(transport=transport).search_songs("Song")
        NCMusicClient(transport=transport)._read_json_from_url("https://example.test/ncm")
        spotify = SpotifyClient(
            SpotifyCredentials("client", "secret"),
            transport=transport,
        )
        self.assertEqual(spotify._get_access_token(), "token")
        spotify._read_json_from_url("https://example.test/spotify", "token")

        self.assertEqual(
            [call[0] for call in transport.calls],
            ["apple_music", "qq_music", "ncm_music", "spotify", "spotify"],
        )


if __name__ == "__main__":
    unittest.main()
