import tempfile
import unittest
from pathlib import Path

from server.v2.composition import RuntimeSettings, build_v2_workflow
from ttml_metadata.v2.transport import HttpResponse


class FakeTransport:
    def request(self, source, method, url, *, headers=None, content=None):
        return HttpResponse(200, {}, b"{}")


class V2CompositionTests(unittest.TestCase):
    def test_vercel_environment_defaults_to_persistent_storage_and_global_limits(self):
        settings = RuntimeSettings.from_env({"VERCEL": "1"})

        self.assertEqual(settings.storage_backend, "vercel")
        self.assertEqual(settings.search_workers, 3)
        self.assertEqual(settings.source_limits["apple_music"], 1)
        self.assertEqual(settings.source_limits["qq_music"], 2)
        self.assertTrue(settings.trust_proxy_headers)
        self.assertEqual(settings.session_ttl_seconds, 86_400)
        self.assertEqual(settings.max_file_bytes, 64 * 1024 * 1024)

    def test_security_and_quota_settings_are_loaded_from_environment(self):
        settings = RuntimeSettings.from_env({
            "ID_MATCH_SESSION_TTL_SECONDS": "30",
            "ID_MATCH_MAX_FILES": "2",
            "ID_MATCH_RATE_LIMIT_REQUESTS": "7",
            "ID_MATCH_TRUST_PROXY_HEADERS": "true",
            "CRON_SECRET": "cron-token",
        })

        self.assertEqual(settings.session_ttl_seconds, 30)
        self.assertEqual(settings.max_files, 2)
        self.assertEqual(settings.request_limit, 7)
        self.assertTrue(settings.trust_proxy_headers)
        self.assertEqual(settings.cleanup_token, "cron-token")

    def test_http_timeout_must_be_positive(self):
        with self.assertRaisesRegex(ValueError, "TTML_HTTP_TIMEOUT_SECONDS"):
            RuntimeSettings.from_env({"TTML_HTTP_TIMEOUT_SECONDS": "0"})

    def test_vercel_backend_requires_cleanup_secret(self):
        settings = RuntimeSettings.from_env({
            "VERCEL": "1",
            "BLOB_READ_WRITE_TOKEN": "blob",
            "KV_REST_API_URL": "https://redis.example",
            "KV_REST_API_TOKEN": "redis",
        })

        with self.assertRaisesRegex(RuntimeError, "CRON_SECRET"):
            build_v2_workflow(settings, transport=FakeTransport())

    def test_runtime_shares_one_transport_and_disables_provider_thread_pools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = RuntimeSettings(
                storage_backend="local",
                local_root=root / "storage",
                work_root=root / "work",
                search_workers=4,
                source_limits={
                    "apple_music": 1,
                    "qq_music": 2,
                    "spotify": 1,
                    "ncm_music": 1,
                },
                http_timeout_seconds=12.0,
                http_attempts=2,
                redis_url=None,
                redis_token=None,
                blob_token=None,
                cors_origins=(),
            )
            transport = FakeTransport()

            workflow = build_v2_workflow(settings, transport=transport)
            adapters = workflow._application._engine._adapters
            clients = [adapter._client for adapter in adapters]

            self.assertTrue(all(client is None or client._transport is transport for client in clients))
            apple = next(adapter for adapter in adapters if adapter.key == "apple_music")
            spotify = next(adapter for adapter in adapters if adapter.key == "spotify")
            ncm = next(adapter for adapter in adapters if adapter.key == "ncm_music")
            self.assertEqual(apple._storefront_workers, 1)
            if spotify._client is not None:
                self.assertEqual(spotify._client._market_workers, 1)
            self.assertEqual(ncm._client._api_workers, 1)
            self.assertEqual(ncm._client._query_workers, 1)


if __name__ == "__main__":
    unittest.main()
