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

class SpotifyMetadataTests(unittest.TestCase):
    def test_read_audio_metadata_reads_release_date_tags(self) -> None:
        class FakeAudio:
            tags = {"date": ["2024-06-19"], "title": ["I Ask"], "artist": ["Sān-Z"]}
            info = type("Info", (), {"length": 185.2})()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.flac"
            path.write_bytes(b"")

            with patch("mutagen.File", return_value=FakeAudio()):
                metadata = read_audio_metadata(path)

        self.assertEqual(metadata.release_date, "2024-06-19")
        self.assertEqual(metadata.duration_seconds, 185.2)

    def test_load_spotify_credentials_reads_dotenv_and_environment_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "SPOTIFY_CLIENT_ID=from-file",
                        'SPOTIFY_CLIENT_SECRET="file secret"',
                    ]
                ),
                encoding="utf-8",
            )

            file_credentials = load_spotify_credentials(env_path=env_path, environ={})
            env_credentials = load_spotify_credentials(
                env_path=env_path,
                environ={
                    "SPOTIFY_CLIENT_ID": "from-env",
                    "SPOTIFY_CLIENT_SECRET": "env-secret",
                },
            )

        self.assertTrue(file_credentials.enabled)
        self.assertEqual(file_credentials.client_id, "from-file")
        self.assertEqual(file_credentials.client_secret, "file secret")
        self.assertTrue(env_credentials.enabled)
        self.assertEqual(env_credentials.client_id, "from-env")
        self.assertEqual(env_credentials.client_secret, "env-secret")

    def test_load_spotify_credentials_is_disabled_when_either_secret_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("SPOTIFY_CLIENT_ID=from-file\n", encoding="utf-8")

            credentials = load_spotify_credentials(env_path=env_path, environ={})

        self.assertFalse(credentials.enabled)
        self.assertEqual(credentials.client_id, "from-file")
        self.assertIsNone(credentials.client_secret)

    def test_spotify_token_request_uses_client_credentials_flow(self) -> None:
        request = SpotifyClient(SpotifyCredentials("client-id", "client-secret"))._build_token_request()

        self.assertEqual(request.full_url, "https://accounts.spotify.com/api/token")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/x-www-form-urlencoded")
        self.assertEqual(request.data.decode("utf-8"), "grant_type=client_credentials")
        auth_scheme, auth_value = request.get_header("Authorization").split(" ", 1)
        self.assertEqual(auth_scheme, "Basic")
        self.assertEqual(base64.b64decode(auth_value).decode("utf-8"), "client-id:client-secret")

    def test_spotify_token_request_retries_transient_urlopen_error(self) -> None:
        attempts = 0
        token_payload = json.dumps({"access_token": "token"}).encode("utf-8")
        client = SpotifyClient(SpotifyCredentials("client-id", "client-secret"), timeout=1)

        def fake_urlopen(request, timeout):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise urllib.error.URLError("temporary outage")
            return FakeHttpResponse(token_payload)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            token = client._get_access_token()

        self.assertEqual(token, "token")
        self.assertEqual(attempts, 2)

    def test_spotify_api_json_request_retries_transient_urlopen_error(self) -> None:
        attempts = 0
        api_payload = json.dumps({"tracks": {"items": []}}).encode("utf-8")
        client = SpotifyClient(SpotifyCredentials("client-id", "client-secret"), timeout=1)

        def fake_urlopen(request, timeout):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise urllib.error.URLError("temporary outage")
            return FakeHttpResponse(api_payload)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            payload = client._read_json_from_url("https://api.spotify.com/v1/search?q=song", "token")

        self.assertEqual(payload, {"tracks": {"items": []}})
        self.assertEqual(attempts, 2)

    def test_spotify_search_queries_prefer_loose_candidate_pool_before_strict_metadata_query(self) -> None:
        queries = _spotify_search_queries(
            AudioMetadata(
                title="Amazing Grace",
                artists=["邓紫棋"],
                album="Amazing Grace - Single",
                isrc="HKA972401000",
            )
        )

        self.assertEqual(
            queries,
            [
                "isrc:HKA972401000",
                "Amazing Grace 邓紫棋 Amazing Grace - Single",
                "Amazing Grace",
                "track:Amazing Grace artist:邓紫棋 album:Amazing Grace - Single",
            ],
        )

    def test_spotify_search_url_uses_track_search_market_limit_and_supplied_query(self) -> None:
        client = SpotifyClient(SpotifyCredentials("client-id", "client-secret"))
        url = client._build_search_url_for_query(
            "Amazing Grace 邓紫棋 Amazing Grace - Single",
            "JP",
        )
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)

        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "api.spotify.com")
        self.assertEqual(parsed.path, "/v1/search")
        self.assertEqual(DEFAULT_SPOTIFY_MARKETS, ["US", "KR", "JP", "TW"])
        self.assertEqual(params["type"], ["track"])
        self.assertEqual(params["market"], ["JP"])
        self.assertEqual(params["limit"], [str(SPOTIFY_SEARCH_LIMIT)])
        self.assertEqual(params["q"], ["Amazing Grace 邓紫棋 Amazing Grace - Single"])

    def test_spotify_artist_album_and_album_detail_urls_use_market_and_pagination(self) -> None:
        client = SpotifyClient(SpotifyCredentials("client-id", "client-secret"))
        artist_url = client._build_artist_search_url("HOYO-MiX", "KR")
        albums_url = client._build_artist_albums_url("artist-1", "JP", offset=20)
        album_url = client._build_album_url("album-1", "TW")
        artist = urllib.parse.urlparse(artist_url)
        artist_params = urllib.parse.parse_qs(artist.query)
        albums = urllib.parse.urlparse(albums_url)
        albums_params = urllib.parse.parse_qs(albums.query)
        album = urllib.parse.urlparse(album_url)
        album_params = urllib.parse.parse_qs(album.query)

        self.assertEqual(artist.path, "/v1/search")
        self.assertEqual(artist_params["type"], ["artist"])
        self.assertEqual(artist_params["market"], ["KR"])
        self.assertEqual(artist_params["q"], ["HOYO-MiX"])
        self.assertEqual(albums.path, "/v1/artists/artist-1/albums")
        self.assertEqual(albums_params["include_groups"], ["album,single"])
        self.assertEqual(albums_params["market"], ["JP"])
        self.assertEqual(albums_params["limit"], ["10"])
        self.assertEqual(albums_params["offset"], ["20"])
        self.assertEqual(album.path, "/v1/albums/album-1")
        self.assertEqual(album_params["market"], ["TW"])

    def test_spotify_search_keeps_collecting_loose_candidates_after_isrc_hit(self) -> None:
        requested_queries: list[str] = []

        def read_json(url: str, access_token: str) -> dict:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["q"][0]
            requested_queries.append(query)
            if query.startswith("isrc:"):
                return {
                    "tracks": {
                        "items": [
                            {
                                "id": "5cofkYnlrYaXesdVpP6xeP",
                                "name": "Amazing Grace",
                                "artists": [{"name": "G.E.M."}],
                                "album": {"name": "Amazing Grace"},
                                "external_ids": {"isrc": "HKA972401000"},
                            }
                        ]
                    }
                }
            return {
                "tracks": {
                    "items": [
                        {
                            "id": f"loose-{index}",
                            "name": f"Amazing Grace {index}",
                            "artists": [{"name": "Someone"}],
                            "album": {"name": "Other"},
                        }
                        for index in range(5)
                    ]
                }
            }

        client = SpotifyClient(
            SpotifyCredentials("client-id", "client-secret"),
            markets=["TW"],
            read_json=read_json,
        )
        client._access_token = "token"

        candidates = client.search_tracks(
            AudioMetadata(
                title="Amazing Grace",
                artists=["邓紫棋"],
                album="Amazing Grace - Single",
                isrc="HKA972401000",
            )
        )

        self.assertEqual(
            requested_queries,
            [
                "isrc:HKA972401000",
                "Amazing Grace 邓紫棋 Amazing Grace - Single",
            ],
        )
        self.assertEqual(
            [candidate.track_id for candidate in candidates],
            ["5cofkYnlrYaXesdVpP6xeP", "loose-0", "loose-1", "loose-2", "loose-3", "loose-4"],
        )

    def test_spotify_searches_markets_in_parallel_and_keeps_market_order(self) -> None:
        def read_json(url: str, access_token: str) -> dict:
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            market = params["market"][0]
            time.sleep(0.07)
            return {
                "tracks": {
                    "items": [
                        {
                            "id": f"{market}-{index}",
                            "name": "Song",
                            "artists": [{"name": "Artist"}],
                            "album": {"name": "Album"},
                        }
                        for index in range(5)
                    ]
                }
            }

        client = SpotifyClient(
            SpotifyCredentials("client-id", "client-secret"),
            markets=["US", "KR", "JP", "TW"],
            read_json=read_json,
        )
        client._access_token = "token"

        with patch.dict(os.environ, {"TTML_SPOTIFY_MARKET_WORKERS": "2"}, clear=False):
            start = time.perf_counter()
            candidates = client.search_tracks(AudioMetadata(title="Song", artists=["Artist"], album="Album"))
            elapsed = time.perf_counter() - start

        self.assertLess(elapsed, 0.20)
        self.assertEqual(
            [candidate.track_id for candidate in candidates[::5]],
            ["US-0", "KR-0", "JP-0", "TW-0"],
        )

    def test_spotify_search_falls_back_to_title_and_strict_queries_when_loose_query_has_too_few_results(self) -> None:
        requested_queries: list[str] = []

        def read_json(url: str, access_token: str) -> dict:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["q"][0]
            requested_queries.append(query)
            if query.startswith("isrc:"):
                return {"tracks": {"items": []}}
            if query == "Amazing Grace 邓紫棋 Amazing Grace - Single":
                return {"tracks": {"items": []}}
            if query == "Amazing Grace":
                return {
                    "tracks": {
                        "items": [
                            {
                                "id": "title-only",
                                "name": "Amazing Grace",
                                "artists": [{"name": "G.E.M."}],
                                "album": {"name": "Amazing Grace"},
                            }
                        ]
                    }
                }
            return {
                "tracks": {
                    "items": [
                        {
                            "id": "fallback-id",
                            "name": "Amazing Grace",
                            "artists": [{"name": "G.E.M."}],
                            "album": {"name": "Amazing Grace"},
                        }
                    ]
                }
            }

        client = SpotifyClient(
            SpotifyCredentials("client-id", "client-secret"),
            markets=["TW"],
            read_json=read_json,
        )
        client._access_token = "token"

        candidates = client.search_tracks(
            AudioMetadata(
                title="Amazing Grace",
                artists=["邓紫棋"],
                album="Amazing Grace - Single",
                isrc="HKA972401000",
            )
        )

        self.assertEqual(
            requested_queries,
            [
                "isrc:HKA972401000",
                "Amazing Grace 邓紫棋 Amazing Grace - Single",
                "Amazing Grace",
                "track:Amazing Grace artist:邓紫棋 album:Amazing Grace - Single",
            ],
        )
        self.assertEqual([candidate.track_id for candidate in candidates], ["title-only", "fallback-id"])

    def test_parse_spotify_candidates_from_tracks_items_and_requires_id(self) -> None:
        payload = {
            "tracks": {
                "items": [
                    {
                        "id": "33e05cb33dd34eddb7d1d3b809dd44e1",
                        "name": "玫瑰少年",
                        "artists": [{"name": "蔡依林"}, {"name": "五月天"}],
                        "album": {"name": "UGLY BEAUTY"},
                        "external_ids": {"isrc": "TST000000001"},
                    },
                    {
                        "name": "missing id",
                        "artists": [{"name": "Nobody"}],
                    },
                ]
            }
        }

        candidates = _parse_spotify_candidates(payload, market="TW")

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].track_id, "33e05cb33dd34eddb7d1d3b809dd44e1")
        self.assertEqual(candidates[0].title, "玫瑰少年")
        self.assertEqual(candidates[0].artists, ["蔡依林", "五月天"])
        self.assertEqual(candidates[0].album, "UGLY BEAUTY")
        self.assertEqual(candidates[0].market, "TW")
        self.assertEqual(candidates[0].isrc, "TST000000001")

    def test_parse_spotify_candidates_includes_album_release_duration_and_match_source(self) -> None:
        payload = {
            "tracks": {
                "items": [
                    {
                        "id": "track-1",
                        "name": "I Ask",
                        "duration_ms": 185000,
                        "artists": [{"name": "Sān-Z"}],
                        "album": {
                            "id": "album-1",
                            "name": "I Ask - Single",
                            "release_date": "2024-06-19",
                            "release_date_precision": "day",
                        },
                        "external_ids": {"isrc": "QZDA62401111"},
                    }
                ]
            }
        }

        candidates = _parse_spotify_candidates(payload, market="US")

        self.assertEqual(candidates[0].duration_ms, 185000)
        self.assertEqual(candidates[0].release_date, "2024-06-19")
        self.assertEqual(candidates[0].release_date_precision, "day")
        self.assertEqual(candidates[0].album_id, "album-1")
        self.assertEqual(candidates[0].match_source, "search")

    def test_spotify_search_uses_artist_album_fallback_when_track_search_has_no_candidates(self) -> None:
        requested: list[tuple[str, dict[str, list[str]]]] = []

        def read_json(url: str, access_token: str) -> dict:
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            requested.append((parsed.path, params))

            if parsed.path == "/v1/search" and params.get("type") == ["track"]:
                return {"tracks": {"items": []}}
            if parsed.path == "/v1/search" and params.get("type") == ["artist"]:
                return {"artists": {"items": [{"id": "artist-1", "name": "Sān-Z"}]}}
            if parsed.path == "/v1/artists/artist-1/albums":
                return {
                    "items": [
                        {
                            "id": "album-old",
                            "name": "Old",
                            "release_date": "2023-01-01",
                            "release_date_precision": "day",
                        },
                        {
                            "id": "album-1",
                            "name": "I Ask - Single",
                            "release_date": "2024-06-19",
                            "release_date_precision": "day",
                        },
                    ]
                }
            if parsed.path == "/v1/albums/album-1":
                return {
                    "id": "album-1",
                    "name": "I Ask - Single",
                    "release_date": "2024-06-19",
                    "release_date_precision": "day",
                    "artists": [{"name": "Sān-Z"}],
                    "tracks": {
                        "items": [
                            {
                                "id": "track-1",
                                "name": "물음",
                                "duration_ms": 185000,
                                "artists": [{"name": "Sān-Z"}],
                            },
                            {
                                "id": "wrong-duration",
                                "name": "물음 - Instrumental",
                                "duration_ms": 210000,
                                "artists": [{"name": "Sān-Z"}],
                            },
                        ]
                    },
                }
            if parsed.path == "/v1/tracks/track-1":
                return {
                    "id": "track-1",
                    "name": "물음",
                    "duration_ms": 185000,
                    "artists": [{"name": "Sān-Z"}],
                    "album": {
                        "id": "album-1",
                        "name": "물음",
                        "release_date": "2024-06-19",
                        "release_date_precision": "day",
                    },
                    "external_ids": {"isrc": "QZDA62401111"},
                }
            raise AssertionError(f"unexpected URL: {url}")

        client = SpotifyClient(
            SpotifyCredentials("client-id", "client-secret"),
            markets=["US"],
            read_json=read_json,
        )
        client._access_token = "token"

        candidates = client.search_tracks(
            AudioMetadata(
                title="I Ask",
                artists=["Sān-Z"],
                album="I Ask - Single",
                release_date="2024-06-19",
                duration_seconds=185,
            )
        )

        self.assertIn("/v1/artists/artist-1/albums", [path for path, _ in requested])
        self.assertEqual([candidate.track_id for candidate in candidates], ["track-1"])
        self.assertEqual(candidates[0].match_source, "artist-album")
        self.assertEqual(candidates[0].title, "물음")
        self.assertEqual(candidates[0].isrc, "QZDA62401111")

    def test_spotify_search_does_not_use_artist_album_fallback_when_search_candidate_is_strong(self) -> None:
        requested_paths: list[str] = []

        def read_json(url: str, access_token: str) -> dict:
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            requested_paths.append(parsed.path)
            if parsed.path == "/v1/search" and params.get("type") == ["track"]:
                return {
                    "tracks": {
                        "items": [
                            {
                                "id": "search-hit",
                                "name": "I Ask",
                                "duration_ms": 185000,
                                "artists": [{"name": "Sān-Z"}],
                                "album": {
                                    "id": "album-1",
                                    "name": "I Ask - Single",
                                    "release_date": "2024-06-19",
                                    "release_date_precision": "day",
                                },
                            }
                        ]
                    }
                }
            raise AssertionError(f"unexpected fallback URL: {url}")

        client = SpotifyClient(
            SpotifyCredentials("client-id", "client-secret"),
            markets=["US"],
            read_json=read_json,
        )
        client._access_token = "token"

        candidates = client.search_tracks(
            AudioMetadata(
                title="I Ask",
                artists=["Sān-Z"],
                album="I Ask - Single",
                release_date="2024-06-19",
                duration_seconds=185,
            )
        )

        self.assertEqual([candidate.track_id for candidate in candidates], ["search-hit"])
        self.assertNotIn("/v1/artists", "\n".join(requested_paths))

    def test_spotify_artist_album_fallback_rejects_date_or_duration_mismatch(self) -> None:
        def read_json(url: str, access_token: str) -> dict:
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/v1/search" and params.get("type") == ["track"]:
                return {"tracks": {"items": []}}
            if parsed.path == "/v1/search" and params.get("type") == ["artist"]:
                return {"artists": {"items": [{"id": "artist-1", "name": "Sān-Z"}]}}
            if parsed.path == "/v1/artists/artist-1/albums":
                return {
                    "items": [
                        {
                            "id": "wrong-date",
                            "name": "I Ask - Single",
                            "release_date": "2024-06-18",
                            "release_date_precision": "day",
                        },
                        {
                            "id": "wrong-duration",
                            "name": "I Ask - Single",
                            "release_date": "2024-06-19",
                            "release_date_precision": "day",
                        },
                    ]
                }
            if parsed.path == "/v1/albums/wrong-duration":
                return {
                    "id": "wrong-duration",
                    "name": "I Ask - Single",
                    "release_date": "2024-06-19",
                    "release_date_precision": "day",
                    "artists": [{"name": "Sān-Z"}],
                    "tracks": {
                        "items": [
                            {
                                "id": "track-1",
                                "name": "I Ask",
                                "duration_ms": 190000,
                                "artists": [{"name": "Sān-Z"}],
                            }
                        ]
                    },
                }
            raise AssertionError(f"unexpected URL: {url}")

        client = SpotifyClient(
            SpotifyCredentials("client-id", "client-secret"),
            markets=["US"],
            read_json=read_json,
        )
        client._access_token = "token"

        candidates = client.search_tracks(
            AudioMetadata(
                title="I Ask",
                artists=["Sān-Z"],
                album="I Ask - Single",
                release_date="2024-06-19",
                duration_seconds=185,
            )
        )

        self.assertEqual(candidates, [])

    def test_collect_spotify_metadata_keeps_best_candidate_per_market_even_with_same_track_id(self) -> None:
        class SearchClient:
            def search_tracks(self, metadata):
                self.metadata = metadata
                return [
                    SpotifyTrackCandidate(
                        "same-id",
                        "Amazing Grace",
                        ["G.E.M."],
                        "Amazing Grace",
                        "US",
                        1,
                        isrc="HKA972401000",
                    ),
                    SpotifyTrackCandidate(
                        "same-id",
                        "어메이징 그레이스",
                        ["G.E.M. 덩쯔치"],
                        "어메이징 그레이스",
                        "KR",
                        2,
                        isrc="HKA972401000",
                    ),
                    SpotifyTrackCandidate(
                        "same-id",
                        "アメイジング・グレイス",
                        ["G.E.M."],
                        "アメイジング・グレイス",
                        "JP",
                        3,
                        isrc="HKA972401000",
                    ),
                    SpotifyTrackCandidate(
                        "same-id",
                        "Amazing Grace",
                        ["G.E.M.邓紫棋"],
                        "Amazing Grace",
                        "TW",
                        4,
                        isrc="HKA972401000",
                    ),
                    SpotifyTrackCandidate(
                        "unrelated",
                        "Amazing Grace",
                        ["Someone"],
                        "Other",
                        "US",
                        5,
                    ),
                ]

        client = SearchClient()

        result = collect_spotify_metadata(
            AudioMetadata(
                title="Amazing Grace",
                artists=["邓紫棋"],
                album="Amazing Grace - Single",
                isrc="HKA972401000",
            ),
            client,
        )

        self.assertEqual(client.metadata.title, "Amazing Grace")
        self.assertEqual([candidate.track_id for candidate in result.candidates], ["same-id", "unrelated"])
        self.assertEqual(list(result.candidates_by_market), ["US", "KR", "JP", "TW"])
        self.assertEqual([candidate.market for candidate in result.selected], ["US", "KR", "JP", "TW"])
        self.assertEqual([candidate.title for candidate in result.selected], ["Amazing Grace", "어메이징 그레이스", "アメイジング・グレイス", "Amazing Grace"])
        self.assertGreater(
            _spotify_candidate_score(client.metadata, result.candidates_by_market["US"][0]),
            _spotify_candidate_score(client.metadata, result.candidates_by_market["US"][1]),
        )









if __name__ == "__main__":
    unittest.main()

