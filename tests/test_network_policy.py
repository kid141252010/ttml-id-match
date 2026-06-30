import os
import unittest
from unittest.mock import patch

from ttml_metadata.network import NetworkPolicy, proxy_url_for_source


class NetworkPolicyTests(unittest.TestCase):
    def test_source_proxy_takes_precedence_over_global_proxy(self):
        environ = {
            "TTML_PROXY_ALL": "http://global-proxy:8080",
            "TTML_PROXY_APPLE_MUSIC": "http://apple-proxy:8080",
            "TTML_PROXY_QQ_MUSIC": "http://qq-proxy:8080",
            "TTML_PROXY_NCM_MUSIC": "http://ncm-proxy:8080",
            "TTML_PROXY_SPOTIFY": "http://spotify-proxy:8080",
        }

        policy = NetworkPolicy.from_env(environ)

        self.assertEqual(policy.proxy_for("apple_music"), "http://apple-proxy:8080")
        self.assertEqual(policy.proxy_for("qq_music"), "http://qq-proxy:8080")
        self.assertEqual(policy.proxy_for("ncm_music"), "http://ncm-proxy:8080")
        self.assertEqual(policy.proxy_for("spotify"), "http://spotify-proxy:8080")
        self.assertEqual(policy.proxy_for("unknown"), "http://global-proxy:8080")

    def test_standard_proxy_environment_is_global_fallback(self):
        environ = {"HTTPS_PROXY": "http://standard-proxy:8080"}

        self.assertEqual(NetworkPolicy.from_env(environ).proxy_for("apple_music"), "http://standard-proxy:8080")

    def test_empty_or_disabled_proxy_values_are_ignored(self):
        environ = {"TTML_PROXY_ALL": "none", "HTTPS_PROXY": ""}

        self.assertIsNone(NetworkPolicy.from_env(environ).proxy_for("apple_music"))

    def test_proxy_url_for_source_reads_current_environment(self):
        with patch.dict(os.environ, {"TTML_PROXY_SPOTIFY": "http://spotify-proxy:8080"}, clear=True):
            self.assertEqual(proxy_url_for_source("spotify"), "http://spotify-proxy:8080")

    def test_positive_integer_config_rejects_invalid_environment_value(self):
        from ttml_metadata.config import load_positive_int_config

        with patch.dict(os.environ, {"TTML_APPLE_MUSIC_WORKERS": "fast"}, clear=True):
            with self.assertRaisesRegex(ValueError, "TTML_APPLE_MUSIC_WORKERS"):
                load_positive_int_config("TTML_APPLE_MUSIC_WORKERS", default=3)


if __name__ == "__main__":
    unittest.main()
