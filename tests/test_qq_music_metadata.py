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

class QQMusicMetadataTests(unittest.TestCase):
    def test_qq_music_request_uses_exact_mobile_search_payload(self) -> None:
        request = QQMusicClient()._build_search_request("玫瑰少年")

        self.assertEqual(request.full_url, "http://u.y.qq.com/cgi-bin/musicu.fcg")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Accept-language"), "zh-CN")
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(request.get_header("User-agent"), "QQMusic 14090508(android 12)")
        self.assertEqual(request.get_header("Content-type"), "application/json")

        payload = json.loads((request.data or b"").decode("utf-8"))
        self.assertEqual(
            payload["comm"],
            {
                "ct": "11",
                "cv": "14090508",
                "v": "14090508",
                "tmeAppID": "qqmusic",
                "phonetype": "EBG-AN10",
                "deviceScore": "553.47",
                "devicelevel": "50",
                "newdevicelevel": "20",
                "rom": "HuaWei/EMOTION/EmotionUI_14.2.0",
                "os_ver": "12",
                "OpenUDID": "0",
                "OpenUDID2": "0",
                "QIMEI36": "0",
                "udid": "0",
                "chid": "0",
                "aid": "0",
                "oaid": "0",
                "taid": "0",
                "tid": "0",
                "wid": "0",
                "uid": "0",
                "sid": "0",
                "modeSwitch": "6",
                "teenMode": "0",
                "ui_mode": "2",
                "nettype": "1020",
                "v4ip": "",
            },
        )
        self.assertEqual(
            payload["req"],
            {
                "module": "music.search.SearchCgiService",
                "method": "DoSearchForQQMusicMobile",
                "param": {
                    "search_type": 0,
                    "query": "玫瑰少年",
                    "page_num": 1,
                    "num_per_page": 30,
                    "highlight": 0,
                    "nqc_flag": 0,
                    "multi_zhida": 0,
                    "cat": 2,
                    "grp": 1,
                    "sin": 0,
                    "sem": 0,
                },
            },
        )

    def test_qq_music_search_retries_transient_urlopen_error(self) -> None:
        attempts = 0
        payload = {
            "req": {
                "data": {
                    "body": {
                        "item_song": [
                            {
                                "id": 224116257,
                                "mid": "001hrIGe3flaPr",
                                "name": "玫瑰少年",
                                "singer": [{"name": "蔡依林"}],
                                "album": {"name": "UGLY BEAUTY"},
                            }
                        ]
                    }
                }
            }
        }

        def fake_urlopen(request, timeout):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise urllib.error.URLError("temporary outage")
            return FakeHttpResponse(json.dumps(payload).encode("utf-8"))

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            candidates = QQMusicClient(timeout=1).search_songs("玫瑰少年")

        self.assertEqual([candidate.song_id for candidate in candidates], ["224116257"])
        self.assertEqual(attempts, 2)

    def test_parses_qq_music_candidates_from_item_song_and_requires_id_and_mid(self) -> None:
        payload = {
            "req": {
                "data": {
                    "body": {
                        "item_song": [
                            {
                                "id": 235883438,
                                "mid": "0035sVym0anwc4",
                                "name": "玫瑰少年",
                                "title": "玫瑰少年",
                                "subtitle": "Live",
                                "singer": [{"name": "蔡依林"}, {"name": "五月天"}],
                                "album": {"name": "UGLY BEAUTY", "title": "UGLY BEAUTY"},
                            },
                            {
                                "songid": 1,
                                "name": "missing mid",
                            },
                        ]
                    }
                }
            }
        }

        candidates = _parse_qq_music_candidates(payload)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].song_id, "235883438")
        self.assertEqual(candidates[0].mid, "0035sVym0anwc4")
        self.assertEqual(candidates[0].title, "玫瑰少年")
        self.assertEqual(candidates[0].subtitle, "Live")
        self.assertEqual(candidates[0].artists, ["蔡依林", "五月天"])
        self.assertEqual(candidates[0].album, "UGLY BEAUTY")

    def test_ranks_qq_candidates_by_title_artist_album_and_contains(self) -> None:
        class SearchClient:
            def search_songs(self, query):
                self.query = query
                return [
                    QQMusicCandidate("235883438", "0035sVym0anwc4", "玫瑰少年", "", ["五月天"], "玫瑰少年", 0),
                    QQMusicCandidate("224116257", "001hrIGe3flaPr", "玫瑰少年", "", ["JOLIN蔡依林"], "UGLY BEAUTY", 1),
                    QQMusicCandidate("415233914", "003YUKMv2dcOZq", "玫瑰少年 - From THE FIRST TAKE", "", ["蔡依林"], "玫瑰少年 - From THE FIRST TAKE", 2),
                ]

        client = SearchClient()

        result = collect_qq_music_metadata(
            AudioMetadata(title="玫瑰少年", artists=["蔡依林"], album="UGLY BEAUTY"),
            client,
        )

        self.assertEqual(client.query, "玫瑰少年")
        self.assertEqual([candidate.song_id for candidate in result.candidates], ["224116257", "415233914", "235883438"])





if __name__ == "__main__":
    unittest.main()

