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

class TtmlMetadataWriterTests(unittest.TestCase):
    def write_ttml(self, text: str, directory: Path) -> Path:
        path = directory / "song.ttml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_script_wrapper_is_not_a_sys_modules_compatibility_facade(self) -> None:
        import fill_ttml_metadata as shim
        import ttml_metadata
        from ttml_metadata import cli

        self.assertIsNot(shim, ttml_metadata)
        self.assertIs(shim.main, cli.main)

    def test_adds_meta_before_itunes_metadata_without_rewriting_outer_ttml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(REFERENCE_STYLE_TTML, Path(tmp))
            before = path.read_text(encoding="utf-8")

            result = update_ttml_metadata(
                path,
                {
                    "musicName": ["Song"],
                    "artists": ["Artist"],
                    "album": ["Album"],
                    "appleMusicId": ["123456789"],
                    "isrc": ["TST000000001"],
                },
                dry_run=False,
            )

            after = path.read_text(encoding="utf-8")
            self.assertEqual(
                before.split("<metadata>", 1)[0],
                after.split("<metadata>", 1)[0],
            )
            self.assertEqual(
                before.rsplit("</metadata>", 1)[1],
                after.rsplit("</metadata>", 1)[1],
            )
            self.assertNotIn("ns0", after)
            self.assertNotIn("ns1", after)
            self.assertNotIn("<?xml", after)
            self.assertEqual(after.count("<head>"), 1)
            self.assertEqual(after.count("<metadata>"), 1)

            expected_insert = (
                '<amll:meta key="musicName" value="Song"/>'
                '<amll:meta key="artists" value="Artist"/>'
                '<amll:meta key="album" value="Album"/>'
                '<amll:meta key="appleMusicId" value="123456789"/>'
                '<amll:meta key="isrc" value="TST000000001"/>'
                "<iTunesMetadata>"
            )
            self.assertIn(expected_insert, after)
            self.assertEqual(result.added["musicName"], ["Song"])
            self.assertIsNotNone(result.backup_path)
            self.assertTrue(result.backup_path.exists())

    def test_replaces_placeholder_values_in_place_and_removes_extra_placeholders(self) -> None:
        text = REFERENCE_STYLE_TTML.replace(
            "<iTunesMetadata>",
            '<amll:meta key="musicName" value="*"/>'
            '<amll:meta key="musicName" value=""/>'
            "<iTunesMetadata>",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(text, Path(tmp))

            result = update_ttml_metadata(
                path,
                {"musicName": ["Real Song"]},
                dry_run=False,
            )

            after = path.read_text(encoding="utf-8")
            self.assertIn('<amll:meta key="musicName" value="Real Song"/>', after)
            self.assertNotIn('<amll:meta key="musicName" value="*"/>', after)
            self.assertNotIn('<amll:meta key="musicName" value=""/>', after)
            self.assertEqual(after.count('key="musicName"'), 1)
            self.assertEqual(result.replaced["musicName"], ["Real Song"])

    def test_existing_real_value_appends_new_unique_values(self) -> None:
        text = REFERENCE_STYLE_TTML.replace(
            "<iTunesMetadata>",
            '<amll:meta key="musicName" value="Existing"/>'
            "<iTunesMetadata>",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(text, Path(tmp))

            result = update_ttml_metadata(
                path,
                {"musicName": ["Existing", "New"]},
                dry_run=False,
            )

            after = path.read_text(encoding="utf-8")
            self.assertIn('<amll:meta key="musicName" value="Existing"/>', after)
            self.assertIn('<amll:meta key="musicName" value="New"/>', after)
            self.assertEqual(after.count('key="musicName" value="Existing"'), 1)
            self.assertEqual(after.count('key="musicName" value="New"'), 1)
            self.assertEqual(result.added["musicName"], ["New"])
            self.assertEqual(result.skipped["musicName"], ["Existing"])
            self.assertIsNotNone(result.backup_path)

    def test_writes_qq_music_id_values_after_album_before_apple_music_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(REFERENCE_STYLE_TTML, Path(tmp))

            update_ttml_metadata(
                path,
                {
                    "album": ["Album"],
                    "qqMusicId": ["235883438", "0035sVym0anwc4"],
                    "spotifyId": ["33e05cb33dd34eddb7d1d3b809dd44e1"],
                    "appleMusicId": ["1691701944"],
                },
                dry_run=False,
            )

            after = path.read_text(encoding="utf-8")
            expected_insert = (
                '<amll:meta key="album" value="Album"/>'
                '<amll:meta key="qqMusicId" value="235883438"/>'
                '<amll:meta key="qqMusicId" value="0035sVym0anwc4"/>'
                '<amll:meta key="spotifyId" value="33e05cb33dd34eddb7d1d3b809dd44e1"/>'
                '<amll:meta key="appleMusicId" value="1691701944"/>'
                "<iTunesMetadata>"
            )
            self.assertIn(expected_insert, after)

    def test_missing_metadata_raises_without_creating_nodes(self) -> None:
        text = (
            '<tt xmlns="http://www.w3.org/ns/ttml" '
            'xmlns:amll="http://www.example.com/ns/amll"><body/></tt>'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(text, Path(tmp))

            with self.assertRaisesRegex(ValueError, "missing <metadata>"):
                update_ttml_metadata(path, {"musicName": ["Song"]}, dry_run=False)

            self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_invalid_updated_ttml_is_not_written_or_backed_up(self) -> None:
        text = (
            '<tt xmlns="http://www.w3.org/ns/ttml" '
            'xmlns:amll="http://www.example.com/ns/amll">'
            "<head><metadata></metadata></head><body><p></body></tt>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(text, Path(tmp))

            with self.assertRaisesRegex(ValueError, "updated TTML is not valid XML"):
                update_ttml_metadata(path, {"musicName": ["Song"]}, dry_run=False)

            self.assertEqual(path.read_text(encoding="utf-8"), text)
            self.assertFalse(path.with_suffix(path.suffix + ".bak").exists())

    def test_missing_amll_namespace_adds_namespace_to_root_tt(self) -> None:
        text = (
            '<tt xmlns="http://www.w3.org/ns/ttml">'
            "<head><metadata></metadata></head><body/></tt>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(text, Path(tmp))

            result = update_ttml_metadata(path, {"musicName": ["Song"]}, dry_run=False)

            after = path.read_text(encoding="utf-8")
            self.assertIn(
                '<tt xmlns="http://www.w3.org/ns/ttml" '
                'xmlns:amll="http://www.example.com/ns/amll">',
                after,
            )
            self.assertIn('<amll:meta key="musicName" value="Song"/>', after)
            self.assertEqual(after.count("xmlns:amll="), 1)
            self.assertEqual(result.added["musicName"], ["Song"])

    def test_missing_amll_namespace_adds_namespace_to_multiline_root_tt(self) -> None:
        text = (
            '<tt xmlns="http://www.w3.org/ns/ttml"\n'
            '    xmlns:ttm="http://www.w3.org/ns/ttml#metadata"\n'
            '    xml:lang="zh-Hans">\n'
            "<head><metadata></metadata></head><body/></tt>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(text, Path(tmp))

            update_ttml_metadata(path, {"musicName": ["Song"]}, dry_run=False)

            after = path.read_text(encoding="utf-8")
            self.assertIn(
                'xml:lang="zh-Hans" xmlns:amll="http://www.example.com/ns/amll">',
                after,
            )
            self.assertIn('<amll:meta key="musicName" value="Song"/>', after)
            self.assertEqual(after.count("xmlns:amll="), 1)

    def test_flattens_mutagen_mp4_itunes_keys_to_metadata_keys(self) -> None:
        tags = {
            "cnID": [1691701944],
            "plID": [1691701942],
            "atID": [152678183],
            "----:com.apple.iTunes:ISRC": [b"TWA472368001"],
        }

        flattened = _flatten_tags(tags)

        self.assertEqual(flattened["itunescatalogid"], ["1691701944"])
        self.assertEqual(flattened["itunesplaylistid"], ["1691701942"])
        self.assertEqual(flattened["itunesalbumtitleid"], ["152678183"])
        self.assertEqual(flattened["isrc"], ["TWA472368001"])


    def test_apple_music_read_text_retries_transient_urlopen_error(self) -> None:
        attempts = 0

        def fake_urlopen(request, timeout):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise urllib.error.URLError("connection reset")
            return FakeHttpResponse(b"apple page")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            text = AppleMusicClient(timeout=1)._read_text("https://music.apple.com/us/search")

        self.assertEqual(text, "apple page")
        self.assertEqual(attempts, 2)

    def test_apple_music_catalog_request_uses_configured_bearer_token_without_scraping_page(self) -> None:
        requests = []

        def fake_urlopen(request, timeout):
            requests.append(request)
            return FakeHttpResponse(b'{"results": {}}')

        with (
            patch.dict(os.environ, {"APPLE_MUSIC_BEARER_TOKEN": "Bearer token-from-env"}, clear=True),
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            payload = AppleMusicClient(timeout=1)._read_catalog_json(
                "cn",
                "https://amp-api.music.apple.com/v1/catalog/cn/search?term=Song&types=songs&limit=25",
            )

        self.assertEqual(payload, {"results": {}})
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].full_url, "https://amp-api.music.apple.com/v1/catalog/cn/search?term=Song&types=songs&limit=25")
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer token-from-env")

    def test_apple_music_bearer_token_accepts_bare_token(self) -> None:
        with patch.dict(os.environ, {"APPLE_MUSIC_BEARER_TOKEN": "token-from-env"}, clear=True):
            client = AppleMusicClient(timeout=1)
            with patch.object(client, "_get_search_page", side_effect=AssertionError("should not scrape Apple page")):
                self.assertEqual(client._get_bearer_token("cn"), "token-from-env")

    def test_apple_music_bearer_token_reads_dotenv_and_environment_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text('APPLE_MUSIC_BEARER_TOKEN="Bearer token-from-file"\n', encoding="utf-8")
            cwd = os.getcwd()

            try:
                os.chdir(tmp)
                with patch.dict(os.environ, {}, clear=True):
                    file_client = AppleMusicClient(timeout=1)
                    with patch.object(file_client, "_get_search_page", side_effect=AssertionError("should not scrape Apple page")):
                        file_token = file_client._get_bearer_token("cn")

                with patch.dict(os.environ, {"APPLE_MUSIC_BEARER_TOKEN": "Bearer token-from-env"}, clear=True):
                    env_client = AppleMusicClient(timeout=1)
                    with patch.object(env_client, "_get_search_page", side_effect=AssertionError("should not scrape Apple page")):
                        env_token = env_client._get_bearer_token("cn")
            finally:
                os.chdir(cwd)

        self.assertEqual(file_token, "token-from-file")
        self.assertEqual(env_token, "token-from-env")

    def test_apple_music_bearer_token_falls_back_to_page_scraping_when_unconfigured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with patch.dict(os.environ, {}, clear=True):
                    client = AppleMusicClient(timeout=1)
                    with (
                        patch.object(client, "_get_search_page", return_value='<script type="module" src="/assets/app.js"></script>'),
                        patch.object(client, "_read_text", return_value='const token = "eyJhbGciOiJFUzI1NiJ9";'),
                    ):
                        self.assertEqual(client._get_bearer_token("cn"), "eyJhbGciOiJFUzI1NiJ9")
            finally:
                os.chdir(cwd)

    def test_collects_metadata_from_all_default_storefronts_without_stopping_at_first_match(self) -> None:
        class RecordingClient:
            def __init__(self):
                self.calls = []

            def fetch_album_tracks(self, store, album_id):
                self.calls.append((store, album_id))
                names = {
                    "cn": ("Song", "Artist", "Album", "111"),
                    "us": ("Song", "Artist", "Album", "444"),
                    "kr": ("노래", "Artist KR", "앨범", "333"),
                    "jp": ("曲", "Artist JP", "アルバム", "222"),
                    "tw": ("Song", "Artist", "Album", "111"),
                }
                name, artist, album, track_id = names[store]
                return [
                    {
                        "id": track_id,
                        "name": name,
                        "artistName": artist,
                        "albumName": album,
                        "isrc": "TST000000001",
                        "discNumber": 1,
                        "trackNumber": 2,
                        "durationInMillis": 180000,
                    }
                ]

            def search_songs(self, store, metadata):
                return []

            def search_artists(self, store, query):
                return []

            def fetch_artist_albums(self, store, artist_id):
                return [], []

        client = RecordingClient()

        result = collect_apple_music_metadata(
            AudioMetadata(
                title="Song",
                playlist_id="999",
                track_number=2,
                disc_number=1,
                duration_seconds=180,
            ),
            client,
        )

        self.assertCountEqual(client.calls, [(store, "999") for store in DEFAULT_STORES])
        self.assertEqual(result.values["musicName"], ["Song", "노래", "曲"])
        self.assertEqual(result.values["artists"], ["Artist", "Artist KR", "Artist JP"])
        self.assertEqual(result.values["album"], ["Album", "앨범", "アルバム"])
        self.assertEqual(result.values["appleMusicId"], ["111", "444", "333", "222"])
        self.assertEqual(result.values["isrc"], ["TST000000001"])
        self.assertEqual(
            result.sources,
            [
                "album:cn:track",
                "album:us:track",
                "album:kr:track",
                "album:jp:track",
                "album:tw:track",
            ],
        )

    def test_apple_music_searches_storefronts_in_parallel_and_merges_in_default_order(self) -> None:
        class SlowSearchClient:
            def fetch_album_tracks(self, store, album_id):
                return []

            def search_songs(self, store, metadata):
                time.sleep(0.08)
                return [
                    AppleMusicTrackCandidate(
                        track_id=f"apple-{store}",
                        title=metadata.title,
                        artists=["Artist"],
                        album="Album",
                        storefront=store,
                        source_index=0,
                    )
                ]

            def search_artists(self, store, query):
                return []

            def fetch_artist_albums(self, store, artist_id):
                return [], []

        with patch.dict(os.environ, {"TTML_APPLE_MUSIC_WORKERS": "3"}, clear=False):
            start = time.perf_counter()
            result = collect_apple_music_metadata(
                AudioMetadata(title="Song", artists=["Artist"], album="Album"),
                SlowSearchClient(),
            )
            elapsed = time.perf_counter() - start

        self.assertLess(elapsed, 0.25)
        self.assertEqual(list(result.candidates_by_storefront), DEFAULT_STORES)
        self.assertEqual([candidate.storefront for candidate in result.selected], DEFAULT_STORES)

    def test_apple_music_worker_count_one_keeps_storefront_calls_serial(self) -> None:
        calls: list[str] = []

        class RecordingSearchClient:
            def fetch_album_tracks(self, store, album_id):
                return []

            def search_songs(self, store, metadata):
                calls.append(store)
                return []

            def search_artists(self, store, query):
                return []

            def fetch_artist_albums(self, store, artist_id):
                return [], []

        with patch.dict(os.environ, {"TTML_APPLE_MUSIC_WORKERS": "1"}, clear=False):
            collect_apple_music_metadata(AudioMetadata(title="Song"), RecordingSearchClient())

        self.assertEqual(calls, DEFAULT_STORES)

    def test_existing_apple_music_id_still_searches_all_storefronts_and_appends_localized_metadata(self) -> None:
        class SearchClient:
            def __init__(self):
                self.search_calls = []

            def fetch_album_tracks(self, store, album_id):
                raise AssertionError("catalog-only metadata should not require album lookup")

            def search_songs(self, store, metadata):
                self.search_calls.append((store, metadata.catalog_id))
                names = {
                    "cn": ("song-cn", "Song", "Artist", "Album"),
                    "us": ("song-us", "Song", "Artist", "Album"),
                    "kr": ("song-kr", "노래", "Artist KR", "앨범"),
                    "jp": ("song-jp", "曲", "Artist JP", "アルバム"),
                    "tw": ("song-tw", "Song", "Artist", "Album"),
                }
                track_id, title, artist, album = names[store]
                return [
                    AppleMusicTrackCandidate(
                        track_id=track_id,
                        title=title,
                        artists=[artist],
                        album=album,
                        storefront=store,
                        source_index=0,
                        isrc="TST000000001",
                        match_source="search",
                    )
                ]

            def search_artists(self, store, query):
                return []

            def fetch_artist_albums(self, store, artist_id):
                return [], []

        client = SearchClient()

        result = collect_apple_music_metadata(
            AudioMetadata(
                title="Song",
                artists=["Artist"],
                album="Album",
                isrc="TST000000001",
                catalog_id="1691701944",
            ),
            client,
        )

        self.assertCountEqual(
            client.search_calls,
            [(store, "1691701944") for store in DEFAULT_STORES],
        )
        self.assertEqual(result.values["appleMusicId"], ["1691701944", "song-cn", "song-us", "song-kr", "song-jp", "song-tw"])
        self.assertEqual(result.values["musicName"], ["Song", "노래", "曲"])
        self.assertEqual(result.values["artists"], ["Artist", "Artist KR", "Artist JP"])
        self.assertEqual(result.values["album"], ["Album", "앨범", "アルバム"])
        self.assertEqual(result.values["isrc"], ["TST000000001"])
        self.assertEqual([candidate.storefront for candidate in result.selected], DEFAULT_STORES)

    def test_apple_music_artist_album_fallback_selects_localized_title_by_date_artist_and_one_second_duration(self) -> None:
        class FallbackClient:
            def __init__(self):
                self.artist_queries = []

            def fetch_album_tracks(self, store, album_id):
                self.fetched_album = (store, album_id)
                return [
                    {
                        "id": "localized",
                        "name": "물음",
                        "artistName": "Sān-Z, HOYO-MiX",
                        "albumName": "물음",
                        "isrc": "FR10S2564999",
                        "durationInMillis": 192000,
                        "releaseDate": "2025-12-18",
                    }
                ]

            def search_songs(self, store, metadata):
                return [
                    AppleMusicTrackCandidate(
                        "weak",
                        "Fearless",
                        ["Sān-Z"],
                        "Fearless",
                        store,
                        0,
                        duration_ms=200000,
                        release_date="2024-01-01",
                        match_source="search",
                    )
                ]

            def search_artists(self, store, query):
                self.artist_queries.append(query)
                if query == "Sān-Z":
                    return [_AppleMusicArtistCandidate("artist-1", "Sān-Z", 0)]
                return [_AppleMusicArtistCandidate("wrong-artist", "Camila Cabello", 0)]

            def fetch_artist_albums(self, store, artist_id):
                return [
                    _AppleMusicAlbumCandidate("old-album", "Old", "2024-01-01", 0),
                    _AppleMusicAlbumCandidate("album-1", "I Ask - Single", "2025-12-18", 1),
                ], []

        client = FallbackClient()

        result = collect_apple_music_metadata(
            AudioMetadata(
                title="I Ask",
                artists=["Sān-Z & HOYO-MiX"],
                album="I Ask - Single",
                duration_seconds=192.4,
                release_date="2025-12-18",
            ),
            client,
            stores=["kr"],
        )

        self.assertEqual(client.fetched_album, ("kr", "album-1"))
        self.assertIn("Sān-Z", client.artist_queries)
        self.assertEqual([(candidate.track_id, candidate.title, candidate.match_source) for candidate in result.selected], [("localized", "물음", "artist-album")])
        self.assertEqual(result.values["musicName"], ["물음"])
        self.assertEqual(result.values["appleMusicId"], ["localized"])

    def test_apple_music_artist_album_fallback_rejects_date_artist_or_duration_mismatch(self) -> None:
        cases = [
            ("bad-date", "2025-12-19", "Sān-Z, HOYO-MiX", 192000),
            ("bad-artist", "2025-12-18", "Other Artist", 192000),
            ("bad-duration", "2025-12-18", "Sān-Z, HOYO-MiX", 194000),
        ]

        for label, release_date, artist_name, duration_ms in cases:
            with self.subTest(label=label):
                class FallbackClient:
                    def fetch_album_tracks(self, store, album_id):
                        return [
                            {
                                "id": label,
                                "name": "물음",
                                "artistName": artist_name,
                                "albumName": "물음",
                                "durationInMillis": duration_ms,
                                "releaseDate": release_date,
                            }
                        ]

                    def search_songs(self, store, metadata):
                        return [
                            AppleMusicTrackCandidate(
                                "weak",
                                "Fearless",
                                ["Sān-Z"],
                                "Fearless",
                                store,
                                0,
                                match_source="search",
                            )
                        ]

                    def search_artists(self, store, query):
                        return [_AppleMusicArtistCandidate("artist-1", "Sān-Z", 0)]

                    def fetch_artist_albums(self, store, artist_id):
                        return [_AppleMusicAlbumCandidate("album-1", "I Ask - Single", "2025-12-18", 0)], []

                result = collect_apple_music_metadata(
                    AudioMetadata(
                        title="I Ask",
                        artists=["Sān-Z & HOYO-MiX"],
                        album="I Ask - Single",
                        duration_seconds=192,
                        release_date="2025-12-18",
                    ),
                    FallbackClient(),
                    stores=["kr"],
                )

                self.assertEqual(result.selected, [])
                self.assertEqual(result.values, {})

    def test_apple_music_instrumental_candidate_is_ranked_low_and_not_auto_selected_unless_source_is_instrumental(self) -> None:
        class SearchClient:
            def __init__(self, candidates):
                self.candidates = candidates

            def fetch_album_tracks(self, store, album_id):
                return []

            def search_songs(self, store, metadata):
                return self.candidates

            def search_artists(self, store, query):
                return []

            def fetch_artist_albums(self, store, artist_id):
                return [], []

        regular = collect_apple_music_metadata(
            AudioMetadata(title="I Ask", artists=["Sān-Z"]),
            SearchClient(
                [
                    AppleMusicTrackCandidate("instrumental", "I Ask - Instrumental", ["Sān-Z"], "I Ask", "us", 0, match_source="search"),
                    AppleMusicTrackCandidate("normal", "I Ask", ["Sān-Z"], "I Ask", "us", 1, match_source="search"),
                ]
            ),
            stores=["us"],
        )
        instrumental_source = collect_apple_music_metadata(
            AudioMetadata(title="I Ask Instrumental", artists=["Sān-Z"]),
            SearchClient(
                [
                    AppleMusicTrackCandidate("instrumental", "I Ask - Instrumental", ["Sān-Z"], "I Ask", "us", 0, match_source="search"),
                ]
            ),
            stores=["us"],
        )

        self.assertEqual([candidate.track_id for candidate in regular.candidates_by_storefront["us"]], ["normal", "instrumental"])
        self.assertEqual([candidate.track_id for candidate in regular.selected], ["normal"])
        self.assertEqual([candidate.track_id for candidate in instrumental_source.selected], ["instrumental"])



    def test_apple_music_album_404_warning_is_suppressed_when_storefront_search_finds_candidate(self) -> None:
        class SearchAfterAlbum404Client:
            def fetch_album_tracks(self, store, album_id):
                raise LookupError("HTTP Error 404: Not Found")

            def search_songs(self, store, metadata):
                return [
                    AppleMusicTrackCandidate(
                        f"{store}-best",
                        "Song" if store == "cn" else f"Song {store}",
                        ["Artist"],
                        "Album",
                        store,
                        0,
                        match_source="search",
                    )
                ]

            def search_artists(self, store, query):
                return []

            def fetch_artist_albums(self, store, artist_id):
                return [], []

        result = collect_apple_music_metadata(
            AudioMetadata(
                title="Song",
                artists=["Artist"],
                album="Album",
                playlist_id="album-only-in-one-region",
            ),
            SearchAfterAlbum404Client(),
        )

        self.assertEqual(set(result.candidates_by_storefront), set(DEFAULT_STORES))
        self.assertEqual(result.errors, [])


    def test_catalog_id_without_playlist_only_writes_existing_song_id(self) -> None:
        class NoLookupClient:
            def fetch_album_tracks(self, store, album_id):
                raise AssertionError("catalog-only metadata should not require album lookup")

            def search_songs(self, store, metadata):
                return []

            def search_artists(self, store, query):
                return []

            def fetch_artist_albums(self, store, artist_id):
                return [], []

        result = collect_apple_music_metadata(
            AudioMetadata(catalog_id="1691701944"),
            NoLookupClient(),
        )

        self.assertEqual(result.values, {"appleMusicId": ["1691701944"]})
        self.assertEqual(result.sources, ["catalog"])
        self.assertEqual(result.errors, [])

    def test_missing_catalog_and_playlist_reports_clear_reason(self) -> None:
        class NoLookupClient:
            def fetch_album_tracks(self, store, album_id):
                raise AssertionError("missing ids should not require album lookup")

            def search_songs(self, store, metadata):
                return []

            def search_artists(self, store, query):
                return []

            def fetch_artist_albums(self, store, artist_id):
                return [], []

        result = collect_apple_music_metadata(
            AudioMetadata(),
            NoLookupClient(),
        )

        self.assertEqual(result.values, {})
        self.assertEqual(result.sources, ["missing-apple-music-id"])
        self.assertEqual(result.errors, ["音频中未读取到 Apple Music 歌曲 ID 或专辑 ID"])


    def test_printing_non_gbk_metadata_does_not_raise_on_windows_console_encoding(self) -> None:
        stream = TextIOWrapper(tempfile.TemporaryFile(), encoding="gbk")

        try:
            _safe_print("lookup warning: 앨범", file=stream)
        finally:
            stream.close()

if __name__ == "__main__":
    unittest.main()

