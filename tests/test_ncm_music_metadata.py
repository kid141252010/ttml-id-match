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

class NCMusicMetadataTests(unittest.TestCase):
    def test_match_score_normalizes_traditional_chinese(self) -> None:
        self.assertEqual(_text_match_score("浪費眼淚", "浪费眼泪"), 2)
        self.assertEqual(_text_match_score("Ella陳嘉樺", "陈嘉桦"), 1)

    def test_parses_ncm_music_candidates_from_cloudsearch_songs_and_requires_id(self) -> None:
        payload = {
            "result": {
                "songs": [
                    {
                        "id": 224116257,
                        "name": "玫瑰少年",
                        "alia": ["Live"],
                        "tns": ["Rose Boy"],
                        "ar": [{"name": "蔡依林"}, {"name": "五月天"}],
                        "al": {"name": "UGLY BEAUTY"},
                    },
                    {
                        "name": "missing id",
                        "ar": [{"name": "Nobody"}],
                    },
                    {
                        "id": "415233914",
                        "title": "玫瑰少年 - From THE FIRST TAKE",
                        "alias": ["THE FIRST TAKE"],
                        "artists": [{"name": "蔡依林"}],
                        "album": {"title": "玫瑰少年 - From THE FIRST TAKE"},
                    },
                ]
            }
        }

        candidates = _parse_ncm_music_candidates(payload)

        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].song_id, "224116257")
        self.assertEqual(candidates[0].title, "玫瑰少年")
        self.assertEqual(candidates[0].aliases, ["Live", "Rose Boy"])
        self.assertEqual(candidates[0].artists, ["蔡依林", "五月天"])
        self.assertEqual(candidates[0].album, "UGLY BEAUTY")
        self.assertEqual(candidates[1].song_id, "415233914")
        self.assertEqual(candidates[1].artists, ["蔡依林"])
        self.assertEqual(candidates[1].album, "玫瑰少年 - From THE FIRST TAKE")

    def test_ncm_client_returns_fastest_successful_candidate_response(self) -> None:
        def read_json(url: str):
            if "slow.invalid" in url:
                time.sleep(0.05)
                return {"result": {"songs": [{"id": 1, "name": "Slow"}]}}
            return {"result": {"songs": [{"id": 2, "name": "Fast"}]}}

        client = NCMusicClient(
            api_bases=["https://slow.invalid", "https://fast.invalid"],
            read_json=read_json,
        )

        candidates = client.search_songs("玫瑰少年")

        self.assertEqual([candidate.song_id for candidate in candidates], ["2"])

    def test_ncm_title_queries_run_in_parallel_and_keep_candidate_order(self) -> None:
        def read_json(url: str):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["keywords"][0]
            time.sleep(0.07)
            return {"result": {"songs": [{"id": query, "name": query}]}}

        client = NCMusicClient(
            api_bases=["https://music163.example"],
            read_json=read_json,
        )

        with patch.dict(os.environ, {"TTML_NCM_QUERY_WORKERS": "2"}, clear=False):
            start = time.perf_counter()
            candidates = client.search_songs(NCMusicSearchContext(titles=["First", "Second"]))
            elapsed = time.perf_counter() - start

        self.assertLess(elapsed, 0.12)
        self.assertEqual([candidate.song_id for candidate in candidates], ["First", "Second"])

    def test_ncm_search_retries_transient_urlopen_error(self) -> None:
        attempts = 0
        payload = {"result": {"songs": [{"id": 224116257, "name": "玫瑰少年"}]}}

        def fake_urlopen(request, timeout):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise urllib.error.URLError("temporary outage")
            return FakeHttpResponse(json.dumps(payload).encode("utf-8"))

        client = NCMusicClient(
            timeout=1,
            api_bases=["https://music163.example"],
        )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            candidates = client.search_songs(NCMusicSearchContext(titles=["玫瑰少年"]))

        self.assertEqual([candidate.song_id for candidate in candidates], ["224116257"])
        self.assertEqual(attempts, 2)

    def test_ncm_search_url_uses_documented_single_song_search_window(self) -> None:
        url = NCMusicClient._build_search_url("https://music163.example", "玫瑰少年")
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)

        self.assertEqual(parsed.path, "/cloudsearch")
        self.assertEqual(params["keywords"], ["玫瑰少年"])
        self.assertEqual(params["limit"], ["100"])
        self.assertEqual(params["offset"], ["0"])
        self.assertEqual(params["type"], ["1"])

    def test_ncm_artist_album_urls_use_documented_windows(self) -> None:
        artist_url = NCMusicClient._build_artist_search_url("https://music163.example", "陈嘉桦")
        artist_parsed = urllib.parse.urlparse(artist_url)
        artist_params = urllib.parse.parse_qs(artist_parsed.query)

        self.assertEqual(artist_parsed.path, "/cloudsearch")
        self.assertEqual(artist_params["keywords"], ["陈嘉桦"])
        self.assertEqual(artist_params["limit"], ["10"])
        self.assertEqual(artist_params["offset"], ["0"])
        self.assertEqual(artist_params["type"], ["100"])

        albums_url = NCMusicClient._build_artist_album_url("https://music163.example", "7647")
        albums_parsed = urllib.parse.urlparse(albums_url)
        albums_params = urllib.parse.parse_qs(albums_parsed.query)

        self.assertEqual(albums_parsed.path, "/artist/album")
        self.assertEqual(albums_params["id"], ["7647"])
        self.assertEqual(albums_params["limit"], ["50"])

        album_url = NCMusicClient._build_album_url("https://music163.example", "3129832")
        album_parsed = urllib.parse.urlparse(album_url)
        album_params = urllib.parse.parse_qs(album_parsed.query)

        self.assertEqual(album_parsed.path, "/album")
        self.assertEqual(album_params["id"], ["3129832"])

    def test_ncm_client_falls_back_when_fastest_response_fails(self) -> None:
        def read_json(url: str):
            if "fast-fail.invalid" in url:
                raise RuntimeError("temporary outage")
            time.sleep(0.01)
            return {"result": {"songs": [{"id": 3, "name": "Fallback"}]}}

        client = NCMusicClient(
            api_bases=["https://fast-fail.invalid", "https://slow-valid.invalid"],
            read_json=read_json,
        )

        candidates = client.search_songs("玫瑰少年")

        self.assertEqual([candidate.song_id for candidate in candidates], ["3"])


    def test_ranks_ncm_candidates_by_title_artist_album_and_contains(self) -> None:
        class SearchClient:
            def search_songs(self, context):
                self.context = context
                return [
                    NCMusicCandidate("235883438", "玫瑰少年", [], ["五月天"], "玫瑰少年", 0),
                    NCMusicCandidate("224116257", "玫瑰少年", [], ["JOLIN蔡依林"], "UGLY BEAUTY", 1),
                    NCMusicCandidate(
                        "415233914",
                        "玫瑰少年 - From THE FIRST TAKE",
                        [],
                        ["蔡依林"],
                        "玫瑰少年 - From THE FIRST TAKE",
                        2,
                    ),
                ]

        client = SearchClient()

        result = collect_ncm_music_metadata(
            AudioMetadata(title="玫瑰少年", artists=["蔡依林"], album="UGLY BEAUTY"),
            client,
        )

        self.assertEqual(client.context.titles, ["玫瑰少年"])
        self.assertEqual([candidate.song_id for candidate in result.candidates], ["224116257", "415233914", "235883438"])

    def test_ncm_ranking_considers_candidates_after_the_first_five_raw_results(self) -> None:
        class SearchClient:
            def search_songs(self, context):
                self.context = context
                return [
                    NCMusicCandidate(str(index), f"Other {index}", [], ["Other"], "Other Album", index)
                    for index in range(7)
                ] + [
                    NCMusicCandidate("1375248354", "玫瑰少年", [], ["蔡依林"], "UGLY BEAUTY", 7)
                ]

        result = collect_ncm_music_metadata(
            AudioMetadata(title="玫瑰少年", artists=["蔡依林"], album="UGLY BEAUTY"),
            SearchClient(),
        )

        self.assertEqual(result.candidates[0].song_id, "1375248354")

    def test_ncm_context_uses_selected_qq_candidate_as_search_hint(self) -> None:
        class SearchClient:
            def search_songs(self, context):
                self.context = context
                return []

        client = SearchClient()

        collect_ncm_music_metadata(
            AudioMetadata(title="浪費眼淚"),
            client,
            qq_music_candidate=QQMusicCandidate(
                "102347867",
                "003Pbr2b1nKveQ",
                "浪费眼泪",
                "",
                ["Ella陈嘉桦"],
                "WHY NOT",
                0,
            ),
        )

        self.assertEqual(client.context.titles, ["浪費眼淚", "浪费眼泪"])
        self.assertEqual(client.context.artists, ["Ella陈嘉桦"])
        self.assertEqual(client.context.albums, ["WHY NOT"])

    def test_ncm_context_without_qq_candidate_uses_original_metadata_only(self) -> None:
        class SearchClient:
            def search_songs(self, context):
                self.context = context
                return []

        client = SearchClient()

        collect_ncm_music_metadata(
            AudioMetadata(title="差一點", artists=["Ella陳嘉樺"], album="WHY NOT"),
            client,
        )

        self.assertEqual(client.context.titles, ["差一點", "差一点"])
        self.assertEqual(client.context.artists, ["Ella陳嘉樺", "Ella陈嘉桦"])
        self.assertEqual(client.context.albums, ["WHY NOT"])

    def test_ncm_client_adds_album_song_candidates_from_matching_artist_album(self) -> None:
        requested_urls: list[str] = []

        def read_json(url: str):
            requested_urls.append(url)
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)

            if parsed.path == "/cloudsearch" and params.get("type") == ["1"]:
                return {
                    "result": {
                        "songs": [
                            {
                                "id": 1454005949,
                                "name": "不再浪费眼泪",
                                "ar": [{"name": "丁燕铃"}],
                                "al": {"name": "等你等到白了头"},
                            }
                        ]
                    }
                }
            if parsed.path == "/cloudsearch" and params.get("type") == ["100"]:
                return {
                    "result": {
                        "artists": [
                            {"id": 7647, "name": "陈嘉桦", "alias": ["Ella"]},
                            {"id": 12709, "name": "S.H.E"},
                        ]
                    }
                }
            if parsed.path == "/artist/album":
                return {
                    "hotAlbums": [
                        {"id": 3129832, "name": "Why Not"},
                        {"id": 999, "name": "Other"},
                    ]
                }
            if parsed.path == "/album":
                return {
                    "songs": [
                        {
                            "id": 31653812,
                            "name": "浪费眼泪",
                            "ar": [{"name": "陈嘉桦"}],
                            "al": {"name": "Why Not"},
                        },
                        {
                            "id": 31653810,
                            "name": "差一点",
                            "ar": [{"name": "陈嘉桦"}],
                            "al": {"name": "Why Not"},
                        },
                    ]
                }
            raise AssertionError(f"unexpected URL: {url}")

        client = NCMusicClient(api_bases=["https://music163.example"], read_json=read_json)

        candidates = client.search_songs(
            NCMusicSearchContext(
                titles=["浪費眼淚", "浪费眼泪"],
                artists=["Ella陳嘉樺", "Ella陈嘉桦"],
                albums=["WHY NOT"],
            )
        )
        result = collect_ncm_music_metadata(
            AudioMetadata(title="浪費眼淚", artists=["Ella陳嘉樺"], album="WHY NOT"),
            client,
            qq_music_candidate=QQMusicCandidate(
                "102347867",
                "003Pbr2b1nKveQ",
                "浪费眼泪",
                "",
                ["Ella陈嘉桦"],
                "WHY NOT",
                0,
            ),
        )

        self.assertIn("31653812", [candidate.song_id for candidate in candidates])
        self.assertEqual(result.candidates[0].song_id, "31653812")
        self.assertIn("/artist/album", "\n".join(requested_urls))
        self.assertIn("/album", "\n".join(requested_urls))






if __name__ == "__main__":
    unittest.main()

