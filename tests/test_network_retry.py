import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO, TextIOWrapper
import base64
import json
import os
from pathlib import Path
from unittest.mock import patch

from ttml_metadata.apple_music import AppleMusicClient, collect_apple_music_metadata, is_valid_apple_music_song_id, _format_apple_music_candidate, _format_apple_music_candidate_list, _parse_apple_music_artist_album_candidates, _sync_apple_music_result_values
from ttml_metadata.audio import read_audio_metadata, _flatten_tags
from ttml_metadata.cli import main
from ttml_metadata.console import _safe_print
from ttml_metadata.models import AudioMetadata, AppleMusicMetadataResult, AppleMusicTrackCandidate, DEFAULT_STORES, DEFAULT_SPOTIFY_MARKETS, SPOTIFY_SEARCH_LIMIT, NCMusicCandidate, NCMusicSearchContext, NCMusicSearchResult, QQMusicCandidate, QQMusicSearchResult, SpotifyCredentials, SpotifySearchResult, SpotifyTrackCandidate, _AppleMusicAlbumCandidate, _AppleMusicArtistCandidate
from ttml_metadata.ncm_music import NCMusicClient, collect_ncm_music_metadata, _parse_ncm_music_candidates
from ttml_metadata.qq_music import QQMusicClient, collect_qq_music_metadata, _parse_qq_music_candidates
from ttml_metadata.spotify import SpotifyClient, collect_spotify_metadata, load_spotify_credentials, _parse_spotify_candidates, _spotify_candidate_score, _spotify_search_queries
from ttml_metadata.text_utils import (
    split_artists,
    _text_match_score,
)
from ttml_metadata.ttml import (
    normalize_ttml_language,
    read_ttml_metadata,
    update_ttml_metadata,
)


class FakeHttpResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self.payload


REFERENCE_STYLE_TTML = (
    '<tt xmlns="http://www.w3.org/ns/ttml" '
    'xmlns:ttm="http://www.w3.org/ns/ttml#metadata" '
    'xmlns:tts="http://www.w3.org/ns/ttml#styling" '
    'xmlns:amll="http://www.example.com/ns/amll" '
    'xmlns:itunes="http://itunes.apple.com/lyric-ttml-extensions" '
    'xml:lang="zh-Hans" itunes:timing="Word">'
    '<head><metadata>'
    '<ttm:agent type="person" xml:id="v1"/>'
    '<amll:meta key="ttmlAuthorGithubLogin" value="kid141252010"/>'
    '<iTunesMetadata><songwriters><songwriter>A</songwriter></songwriters></iTunesMetadata>'
    '</metadata></head>'
    '<body dur="01:00.000"><div><p begin="00:00.000" end="00:01.000">x</p></div></body>'
    '</tt>'
)

class NetworkRetryTests(unittest.TestCase):
    def test_retry_call_returns_after_transient_url_error(self) -> None:
        from ttml_metadata.network import retry_call

        attempts = 0

        def operation():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise urllib.error.URLError("temporary outage")
            return "ok"

        result = retry_call(operation, sleep_func=lambda delay: None)

        self.assertEqual(result, "ok")
        self.assertEqual(attempts, 2)

    def test_retry_call_retries_http_429_and_500(self) -> None:
        from ttml_metadata.network import retry_call

        errors = [
            urllib.error.HTTPError("https://example.invalid", 429, "rate limited", {}, BytesIO()),
            urllib.error.HTTPError("https://example.invalid", 500, "server error", {}, BytesIO()),
        ]
        attempts = 0

        def operation():
            nonlocal attempts
            attempts += 1
            if attempts <= len(errors):
                raise errors[attempts - 1]
            return "ok"

        result = retry_call(operation, sleep_func=lambda delay: None)

        self.assertEqual(result, "ok")
        self.assertEqual(attempts, 3)

    def test_retry_call_does_not_retry_http_404(self) -> None:
        from ttml_metadata.network import retry_call

        attempts = 0

        def operation():
            nonlocal attempts
            attempts += 1
            raise urllib.error.HTTPError("https://example.invalid", 404, "not found", {}, BytesIO())

        with self.assertRaises(urllib.error.HTTPError) as raised:
            retry_call(operation, sleep_func=lambda delay: None)

        raised.exception.close()
        self.assertEqual(attempts, 1)

    def test_retry_call_raises_last_error_after_attempts(self) -> None:
        from ttml_metadata.network import retry_call

        attempts = 0

        def operation():
            nonlocal attempts
            attempts += 1
            raise urllib.error.URLError(f"temporary outage {attempts}")

        with self.assertRaisesRegex(urllib.error.URLError, "temporary outage 3"):
            retry_call(operation, sleep_func=lambda delay: None)

        self.assertEqual(attempts, 3)

if __name__ == "__main__":
    unittest.main()

