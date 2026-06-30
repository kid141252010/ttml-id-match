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

from fill_ttml_metadata import (
    AudioMetadata,
    AppleMusicMetadataResult,
    AppleMusicClient,
    AppleMusicTrackCandidate,
    DEFAULT_STORES,
    DEFAULT_SPOTIFY_MARKETS,
    SPOTIFY_SEARCH_LIMIT,
    NCMusicCandidate,
    NCMusicClient,
    NCMusicSearchContext,
    NCMusicSearchResult,
    PairMetadata,
    QQMusicCandidate,
    QQMusicClient,
    QQMusicSearchResult,
    SpotifyClient,
    SpotifyCredentials,
    SpotifySearchResult,
    SpotifyTrackCandidate,
    collect_apple_music_metadata,
    collect_ncm_music_metadata,
    collect_qq_music_metadata,
    collect_spotify_metadata,
    confirm_apple_music_candidates,
    confirm_ncm_music_candidates,
    confirm_qq_music_candidates,
    confirm_spotify_candidates,
    find_directory_work_items,
    load_spotify_credentials,
    main,
    normalize_ttml_language,
    read_audio_metadata,
    read_ttml_metadata,
    split_artists,
    update_ttml_metadata,
    values_from_metadata,
    WorkItem,
    _collect_ncm_music_metadata_for_pairs,
    _flatten_tags,
    _AppleMusicAlbumCandidate,
    _AppleMusicArtistCandidate,
    _prepare_work_item,
    _parse_ncm_music_candidates,
    _parse_qq_music_candidates,
    _parse_spotify_candidates,
    _process_prepared_pair,
    _spotify_candidate_score,
    _spotify_search_queries,
    _safe_print,
    _text_match_score,
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


class TtmlMetadataWriterTests(unittest.TestCase):
    def write_ttml(self, text: str, directory: Path) -> Path:
        path = directory / "song.ttml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_split_package_exports_are_available_through_compatibility_shim(self) -> None:
        import fill_ttml_metadata as shim
        import ttml_metadata

        self.assertIs(shim.main, ttml_metadata.main)
        self.assertIs(shim.update_ttml_metadata, ttml_metadata.update_ttml_metadata)
        self.assertIs(shim.split_artists, ttml_metadata.split_artists)
        self.assertTrue(callable(shim._safe_print))

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

    def test_splits_ampersand_joined_artists_for_metadata_writing(self) -> None:
        self.assertEqual(split_artists(["Sān-Z & HOYO-MiX"]), ["Sān-Z", "HOYO-MiX"])

        values = values_from_metadata(AudioMetadata(title="I Ask", artists=["Sān-Z & HOYO-MiX"]))

        self.assertEqual(values["artists"], ["Sān-Z", "HOYO-MiX"])

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

        self.assertEqual(client.calls, [(store, "999") for store in DEFAULT_STORES])
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

        self.assertEqual(client.search_calls, [(store, "1691701944") for store in DEFAULT_STORES])
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

    def test_rejecting_apple_music_best_prompts_for_five_choices_per_storefront_and_updates_values(self) -> None:
        result = AppleMusicMetadataResult(
            candidates_by_storefront={
                "cn": [
                    AppleMusicTrackCandidate(f"cn-{index}", f"CN Song {index}", ["A"], "Album", "cn", index, match_source="search")
                    for index in range(1, 7)
                ],
                "us": [
                    AppleMusicTrackCandidate(f"us-{index}", f"US Song {index}", ["A"], "Album", "us", index, match_source="search")
                    for index in range(1, 4)
                ],
            }
        )
        result.candidates = [candidate for candidates in result.candidates_by_storefront.values() for candidate in candidates]
        pair = PairMetadata(
            Path("song.flac"),
            Path("song.ttml"),
            AudioMetadata(title="Song"),
            result,
            QQMusicSearchResult(),
        )
        answers = iter(["N", "4", ""])
        printed: list[str] = []

        confirm_apple_music_candidates(
            [pair],
            dry_run=False,
            input_func=lambda prompt: next(answers),
            print_func=lambda *values, **kwargs: printed.append(" ".join(str(value) for value in values)),
        )

        self.assertEqual([candidate.track_id for candidate in result.selected], ["cn-4"])
        self.assertEqual(result.values["appleMusicId"], ["cn-4"])
        self.assertTrue(any("CN Apple Music 候选" in line for line in printed))
        self.assertTrue(any("US Apple Music 候选" in line for line in printed))
        self.assertTrue(any("[cn-1]" in line for line in printed))
        self.assertTrue(any("[cn-5]" in line for line in printed))
        self.assertFalse(any("[cn-6]" in line for line in printed))

    def test_apple_music_best_output_lists_each_storefront_best_even_when_manual_only(self) -> None:
        result = AppleMusicMetadataResult(
            candidates_by_storefront={
                "cn": [AppleMusicTrackCandidate("cn-best", "Song", ["Artist"], "Album", "cn", 0, match_source="search")],
                "us": [AppleMusicTrackCandidate("us-best", "Localized US", ["Artist"], "Album", "us", 0, match_source="search")],
                "kr": [AppleMusicTrackCandidate("kr-best", "현지화", ["Artist"], "Album", "kr", 0, match_source="search")],
                "jp": [AppleMusicTrackCandidate("jp-best", "ローカライズ", ["Artist"], "Album", "jp", 0, match_source="search")],
                "tw": [AppleMusicTrackCandidate("tw-best", "本地化", ["Artist"], "Album", "tw", 0, match_source="search")],
            }
        )
        result.candidates = [candidate for candidates in result.candidates_by_storefront.values() for candidate in candidates]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.ttml"
            path.write_text(REFERENCE_STYLE_TTML, encoding="utf-8")
            pair = PairMetadata(
                Path("song.flac"),
                path,
                AudioMetadata(title="Song", artists=["Artist"], album="Album"),
                result,
                QQMusicSearchResult(),
            )
            stdout = StringIO()

            confirm_apple_music_candidates(
                [pair],
                dry_run=True,
                input_func=lambda prompt: (_ for _ in ()).throw(AssertionError("dry-run should not prompt")),
                print_func=lambda *values, **kwargs: None,
            )
            with redirect_stdout(stdout):
                _process_prepared_pair(pair, dry_run=True)

        output = stdout.getvalue()
        self.assertEqual([candidate.track_id for candidate in result.selected], ["cn-best"])
        for label in ["CN", "US", "KR", "JP", "TW"]:
            self.assertIn(f"{label}: ", output)

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

    def test_values_from_metadata_dedupes_apple_music_id_but_keeps_storefront_metadata_variants(self) -> None:
        values = values_from_metadata(
            AudioMetadata(title="Amazing Grace", artists=["邓紫棋"], album="Amazing Grace - Single"),
            apple_music_candidates=[
                AppleMusicTrackCandidate("same-id", "Amazing Grace", ["G.E.M."], "Amazing Grace", "us", 0),
                AppleMusicTrackCandidate("same-id", "어메이징 그레이스", ["G.E.M. 덩쯔치"], "어메이징 그레이스", "kr", 1),
                AppleMusicTrackCandidate("same-id", "アメイジング・グレイス", ["G.E.M."], "アメイジング・グレイス", "jp", 2, isrc="HKA972401000"),
            ],
        )

        self.assertEqual(values["appleMusicId"], ["same-id"])
        self.assertEqual(values["musicName"], ["Amazing Grace", "어메이징 그레이스", "アメイジング・グレイス"])
        self.assertEqual(values["artists"], ["邓紫棋", "G.E.M.", "G.E.M. 덩쯔치"])
        self.assertEqual(values["album"], ["Amazing Grace - Single", "Amazing Grace", "어메이징 그레이스", "アメイジング・グレイス"])
        self.assertEqual(values["isrc"], ["HKA972401000"])

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

    def test_legacy_storefront_cli_options_are_removed(self) -> None:
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main(["--store", "jp", "--dry-run"])

        self.assertNotEqual(raised.exception.code, 0)

    def test_printing_non_gbk_metadata_does_not_raise_on_windows_console_encoding(self) -> None:
        stream = TextIOWrapper(tempfile.TemporaryFile(), encoding="gbk")

        try:
            _safe_print("lookup warning: 앨범", file=stream)
        finally:
            stream.close()


class TtmlLanguageNormalizationTests(unittest.TestCase):
    def write_ttml(self, text: str, directory: Path) -> Path:
        path = directory / "song.ttml"
        path.write_text(text, encoding="utf-8")
        return path

    def zh_hant_ttml(self, body: str, metadata_inner: str | None = None) -> str:
        metadata = (
            '<amll:meta key="musicName" value="浪費眼淚"/>'
            '<iTunesMetadata><songwriters><songwriter>黃韻玲</songwriter></songwriters></iTunesMetadata>'
            if metadata_inner is None
            else metadata_inner
        )
        return (
            '<tt xmlns="http://www.w3.org/ns/ttml" '
            'xmlns:amll="http://www.example.com/ns/amll" '
            'xml:lang="zh-Hant">'
            f"<head><metadata>{metadata}</metadata></head>"
            f"<body>{body}</body>"
            "</tt>"
        )

    def test_normalizes_zh_hant_body_text_and_removes_target_layers_without_rewriting_metadata(self) -> None:
        body = (
            '<div><p begin="00:00.000" end="00:01.000">輪到我出場 我不會怯場</p>'
            '<translations>'
            '<translation type="replacement" xml:lang="zh-Hans"><p>替換文字</p></translation>'
            '<translation type="subtitle" xml:lang="en"><p>Keep English</p></translation>'
            '</translations>'
            '<transliterations>'
            '<transliteration xml:lang="zh-Latn-pinyin"><p>lun dao wo chu chang</p></transliteration>'
            '<transliteration xml:lang="ja-Latn"><p>keep latin</p></transliteration>'
            '</transliterations>'
            "</div>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(self.zh_hant_ttml(body), Path(tmp))

            result = normalize_ttml_language(path, dry_run=False)

            after = path.read_text(encoding="utf-8")
            self.assertTrue(result.changed)
            self.assertTrue(result.language_changed)
            self.assertTrue(result.body_text_changed)
            self.assertEqual(result.removed_translations, 1)
            self.assertEqual(result.removed_transliterations, 1)
            self.assertIsNotNone(result.backup_path)
            self.assertTrue(result.backup_path.exists())
            self.assertIn('xml:lang="zh-Hans"', after)
            self.assertNotIn('xml:lang="zh-Hant"', after)
            self.assertIn("轮到我出场 我不会怯场", after)
            self.assertNotIn("輪到我出場 我不會怯場", after)
            self.assertIn('<songwriter>黃韻玲</songwriter>', after)
            self.assertIn('<amll:meta key="musicName" value="浪費眼淚"/>', after)
            self.assertNotIn('type="replacement" xml:lang="zh-Hans"', after)
            self.assertNotIn('xml:lang="zh-Latn-pinyin"', after)
            self.assertIn('type="subtitle" xml:lang="en"', after)
            self.assertIn('xml:lang="ja-Latn"', after)

    def test_zh_hans_file_is_unchanged_and_does_not_create_backup(self) -> None:
        text = self.zh_hant_ttml('<div><p>已经是简体</p></div>').replace('xml:lang="zh-Hant"', 'xml:lang="zh-Hans"')
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(text, Path(tmp))

            result = normalize_ttml_language(path, dry_run=False)

            self.assertFalse(result.changed)
            self.assertIsNone(result.backup_path)
            self.assertEqual(path.read_text(encoding="utf-8"), text)
            self.assertFalse(path.with_suffix(path.suffix + ".bak").exists())

    def test_dry_run_reports_changes_without_writing_or_backup(self) -> None:
        text = self.zh_hant_ttml('<div><p>自帶燈光 自帶氣場</p></div>')
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(text, Path(tmp))

            result = normalize_ttml_language(path, dry_run=True)

            self.assertTrue(result.changed)
            self.assertTrue(result.language_changed)
            self.assertTrue(result.body_text_changed)
            self.assertIsNone(result.backup_path)
            self.assertEqual(path.read_text(encoding="utf-8"), text)
            self.assertFalse(path.with_suffix(path.suffix + ".bak").exists())

    def test_metadata_update_reuses_normalization_backup_for_same_file(self) -> None:
        text = self.zh_hant_ttml(
            '<div><p>聽不見 大聲為我鼓掌</p></div>',
            metadata_inner='<amll:meta key="musicName" value="*"/>',
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(text, Path(tmp))
            backup_paths: dict[Path, Path] = {}

            normalization = normalize_ttml_language(path, dry_run=False, backup_paths=backup_paths)
            update = update_ttml_metadata(path, {"musicName": ["Real Song"]}, dry_run=False, backup_paths=backup_paths)

            self.assertIsNotNone(normalization.backup_path)
            self.assertEqual(update.backup_path, normalization.backup_path)
            self.assertTrue(path.with_suffix(path.suffix + ".bak").exists())
            self.assertFalse(path.with_suffix(path.suffix + ".bak1").exists())

    def test_main_normalizes_language_even_when_metadata_lookup_fails(self) -> None:
        text = self.zh_hant_ttml('<div><p>誰要不溫不火</p></div>', metadata_inner="")
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(text, Path(tmp))
            stderr = StringIO()

            with redirect_stderr(stderr):
                exit_code = main(["--ttml", str(path)])

            after = path.read_text(encoding="utf-8")
            self.assertEqual(exit_code, 1)
            self.assertIn("TTML 中未读取到歌名", stderr.getvalue())
            self.assertIn('xml:lang="zh-Hans"', after)
            self.assertIn("谁要不温不火", after)
            self.assertTrue(path.with_suffix(path.suffix + ".bak").exists())


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

    def test_values_from_metadata_adds_qq_ids_and_changed_fields_and_subtitle_to_music_name(self) -> None:
        values = values_from_metadata(
            AudioMetadata(title="玫瑰少年", artists=["蔡依林"], album="UGLY BEAUTY"),
            qq_music_candidate=QQMusicCandidate(
                "224116257",
                "001hrIGe3flaPr",
                "玫瑰少年",
                "Ugly Beauty Remix",
                ["JOLIN蔡依林"],
                "Ugly Beauty",
                0,
            ),
        )

        self.assertEqual(values["qqMusicId"], ["224116257", "001hrIGe3flaPr"])
        self.assertEqual(values["musicName"], ["玫瑰少年", "Ugly Beauty Remix"])
        self.assertEqual(values["artists"], ["蔡依林", "JOLIN蔡依林"])
        self.assertEqual(values["album"], ["UGLY BEAUTY", "Ugly Beauty"])

    def test_dry_run_qq_confirmation_selects_best_without_prompting(self) -> None:
        result = QQMusicSearchResult(
            candidates=[
                QQMusicCandidate("1", "mid1", "Best", "", ["A"], "Album", 0),
                QQMusicCandidate("2", "mid2", "Backup", "", ["A"], "Album", 1),
            ]
        )
        pair = PairMetadata(Path("a.flac"), Path("a.ttml"), AudioMetadata(title="Best"), AppleMusicMetadataResult(), result)

        confirm_qq_music_candidates(
            [pair],
            dry_run=True,
            input_func=lambda prompt: (_ for _ in ()).throw(AssertionError("dry-run should not prompt")),
            print_func=lambda *values, **kwargs: None,
        )

        self.assertEqual(result.selected, result.candidates[0])

    def test_accepting_best_qq_candidates_uses_all_first_choices(self) -> None:
        first = QQMusicSearchResult(
            candidates=[
                QQMusicCandidate("1", "mid1", "One", "", ["A"], "Album", 0),
                QQMusicCandidate("2", "mid2", "Two", "", ["A"], "Album", 1),
            ]
        )
        second = QQMusicSearchResult(candidates=[QQMusicCandidate("3", "mid3", "Three", "", ["B"], "Album", 0)])
        pairs = [
            PairMetadata(Path("one.flac"), Path("one.ttml"), AudioMetadata(title="One"), AppleMusicMetadataResult(), first),
            PairMetadata(Path("two.flac"), Path("two.ttml"), AudioMetadata(title="Two"), AppleMusicMetadataResult(), second),
        ]

        confirm_qq_music_candidates(
            pairs,
            dry_run=False,
            input_func=lambda prompt: "Y",
            print_func=lambda *values, **kwargs: None,
        )

        self.assertEqual(first.selected, first.candidates[0])
        self.assertEqual(second.selected, second.candidates[0])

    def test_rejecting_best_qq_candidates_prompts_for_one_of_five_choices(self) -> None:
        result = QQMusicSearchResult(
            candidates=[
                QQMusicCandidate(str(index), f"mid{index}", f"Song {index}", "", ["A"], "Album", index)
                for index in range(1, 7)
            ]
        )
        pair = PairMetadata(Path("song.flac"), Path("song.ttml"), AudioMetadata(title="Song"), AppleMusicMetadataResult(), result)
        answers = iter(["N", "3"])

        confirm_qq_music_candidates(
            [pair],
            dry_run=False,
            input_func=lambda prompt: next(answers),
            print_func=lambda *values, **kwargs: None,
        )

        self.assertEqual(result.selected, result.candidates[2])


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

    def test_collect_ncm_metadata_for_pairs_uses_configured_workers(self) -> None:
        active = 0
        max_active = 0
        lock = threading.Lock()

        class SearchClient:
            def search_songs(self, context):
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                try:
                    time.sleep(0.03)
                    return [NCMusicCandidate(str(context.titles[0]), context.titles[0])]
                finally:
                    with lock:
                        active -= 1

        pairs = [
            PairMetadata(
                None,
                Path(f"{index}.ttml"),
                AudioMetadata(title=str(index)),
                AppleMusicMetadataResult(),
                QQMusicSearchResult(),
            )
            for index in range(4)
        ]

        _collect_ncm_music_metadata_for_pairs(pairs, SearchClient(), max_workers=3)

        self.assertEqual(max_active, 3)
        self.assertEqual([pair.ncm_music_metadata.candidates[0].song_id for pair in pairs], ["0", "1", "2", "3"])

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

    def test_values_from_metadata_adds_ncm_id_and_changed_fields(self) -> None:
        values = values_from_metadata(
            AudioMetadata(title="玫瑰少年", artists=["蔡依林"], album="UGLY BEAUTY"),
            ncm_music_candidate=NCMusicCandidate(
                "224116257",
                "玫瑰少年",
                ["Rose Boy"],
                ["JOLIN蔡依林"],
                "Ugly Beauty",
                0,
            ),
        )

        self.assertEqual(values["ncmMusicId"], ["224116257"])
        self.assertEqual(values["musicName"], ["玫瑰少年", "Rose Boy"])
        self.assertEqual(values["artists"], ["蔡依林", "JOLIN蔡依林"])
        self.assertEqual(values["album"], ["UGLY BEAUTY", "Ugly Beauty"])

    def test_dry_run_ncm_confirmation_selects_best_without_prompting(self) -> None:
        result = NCMusicSearchResult(
            candidates=[
                NCMusicCandidate("1", "Best", [], ["A"], "Album", 0),
                NCMusicCandidate("2", "Backup", [], ["A"], "Album", 1),
            ]
        )
        pair = PairMetadata(
            Path("a.flac"),
            Path("a.ttml"),
            AudioMetadata(title="Best"),
            AppleMusicMetadataResult(),
            QQMusicSearchResult(),
            result,
        )

        confirm_ncm_music_candidates(
            [pair],
            dry_run=True,
            input_func=lambda prompt: (_ for _ in ()).throw(AssertionError("dry-run should not prompt")),
            print_func=lambda *values, **kwargs: None,
        )

        self.assertEqual(result.selected, result.candidates[0])

    def test_accepting_best_ncm_candidates_uses_all_first_choices(self) -> None:
        first = NCMusicSearchResult(
            candidates=[
                NCMusicCandidate("1", "One", [], ["A"], "Album", 0),
                NCMusicCandidate("2", "Two", [], ["A"], "Album", 1),
            ]
        )
        second = NCMusicSearchResult(candidates=[NCMusicCandidate("3", "Three", [], ["B"], "Album", 0)])
        pairs = [
            PairMetadata(Path("one.flac"), Path("one.ttml"), AudioMetadata(title="One"), AppleMusicMetadataResult(), QQMusicSearchResult(), first),
            PairMetadata(Path("two.flac"), Path("two.ttml"), AudioMetadata(title="Two"), AppleMusicMetadataResult(), QQMusicSearchResult(), second),
        ]

        confirm_ncm_music_candidates(
            pairs,
            dry_run=False,
            input_func=lambda prompt: "Y",
            print_func=lambda *values, **kwargs: None,
        )

        self.assertEqual(first.selected, first.candidates[0])
        self.assertEqual(second.selected, second.candidates[0])

    def test_rejecting_best_ncm_candidates_prompts_for_one_of_five_choices(self) -> None:
        result = NCMusicSearchResult(
            candidates=[
                NCMusicCandidate(str(index), f"Song {index}", [], ["A"], "Album", index)
                for index in range(1, 7)
            ]
        )
        pair = PairMetadata(Path("song.flac"), Path("song.ttml"), AudioMetadata(title="Song"), AppleMusicMetadataResult(), QQMusicSearchResult(), result)
        answers = iter(["N", "3"])

        confirm_ncm_music_candidates(
            [pair],
            dry_run=False,
            input_func=lambda prompt: next(answers),
            print_func=lambda *values, **kwargs: None,
        )

        self.assertEqual(result.selected, result.candidates[2])

    def test_rejecting_ncm_candidates_lists_sorted_top_five_options(self) -> None:
        result = NCMusicSearchResult(
            candidates=[
                NCMusicCandidate("best", "玫瑰少年", [], ["蔡依林"], "UGLY BEAUTY", 7),
                NCMusicCandidate("second", "玫瑰少年", [], ["蔡依林"], "Other", 6),
                NCMusicCandidate("third", "玫瑰少年", [], ["Other"], "UGLY BEAUTY", 5),
                NCMusicCandidate("fourth", "玫瑰少年", [], ["Other"], "Other", 4),
                NCMusicCandidate("fifth", "Other", [], ["蔡依林"], "UGLY BEAUTY", 3),
                NCMusicCandidate("sixth", "Other", [], ["Other"], "Other", 2),
            ]
        )
        pair = PairMetadata(Path("song.flac"), Path("song.ttml"), AudioMetadata(title="Song"), AppleMusicMetadataResult(), QQMusicSearchResult(), result)
        answers = iter(["N", "5"])
        printed: list[str] = []

        confirm_ncm_music_candidates(
            [pair],
            dry_run=False,
            input_func=lambda prompt: next(answers),
            print_func=lambda *values, **kwargs: printed.append(" ".join(str(value) for value in values)),
        )

        self.assertEqual(result.selected.song_id if result.selected else None, "fifth")
        self.assertTrue(any("[best]" in line for line in printed))
        self.assertTrue(any("[fifth]" in line for line in printed))
        self.assertFalse(any("[sixth]" in line for line in printed))


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

    def test_dry_run_spotify_confirmation_selects_market_best_without_prompting(self) -> None:
        result = SpotifySearchResult(
            candidates=[
                SpotifyTrackCandidate("us-best", "Amazing Grace", ["G.E.M."], "Amazing Grace", "US", 0, isrc="HKA972401000"),
                SpotifyTrackCandidate("kr-best", "어메이징 그레이스", ["G.E.M."], "어메이징 그레이스", "KR", 0, isrc="HKA972401000"),
                SpotifyTrackCandidate("backup", "Amazing Grace", ["Other"], "Other", "US", 1),
            ],
            candidates_by_market={
                "US": [
                    SpotifyTrackCandidate("us-best", "Amazing Grace", ["G.E.M."], "Amazing Grace", "US", 0, isrc="HKA972401000"),
                    SpotifyTrackCandidate("us-backup", "Amazing Grace", ["Other"], "Other", "US", 1),
                ],
                "KR": [
                    SpotifyTrackCandidate("kr-best", "어메이징 그레이스", ["G.E.M."], "어메이징 그레이스", "KR", 0, isrc="HKA972401000"),
                ],
            },
        )
        pair = PairMetadata(
            Path("a.flac"),
            Path("a.ttml"),
            AudioMetadata(title="Amazing Grace", isrc="HKA972401000"),
            AppleMusicMetadataResult(),
            QQMusicSearchResult(),
            NCMusicSearchResult(),
            result,
        )

        confirm_spotify_candidates(
            [pair],
            dry_run=True,
            input_func=lambda prompt: (_ for _ in ()).throw(AssertionError("dry-run should not prompt")),
            print_func=lambda *values, **kwargs: None,
        )

        self.assertEqual([candidate.track_id for candidate in result.selected], ["us-best", "kr-best"])

    def test_dry_run_spotify_confirmation_does_not_auto_select_artist_only_mismatch(self) -> None:
        result = SpotifySearchResult(
            candidates=[
                SpotifyTrackCandidate("wrong", "Fearless", ["Sān-Z"], "Fearless", "JP", 0),
            ],
            candidates_by_market={
                "JP": [
                    SpotifyTrackCandidate("wrong", "Fearless", ["Sān-Z"], "Fearless", "JP", 0),
                ],
            },
        )
        pair = PairMetadata(
            Path("a.m4a"),
            Path("a.ttml"),
            AudioMetadata(title="I Ask", artists=["Sān-Z"], album="I Ask - Single"),
            AppleMusicMetadataResult(),
            QQMusicSearchResult(),
            NCMusicSearchResult(),
            result,
        )

        confirm_spotify_candidates(
            [pair],
            dry_run=True,
            input_func=lambda prompt: (_ for _ in ()).throw(AssertionError("dry-run should not prompt")),
            print_func=lambda *values, **kwargs: None,
        )

        self.assertEqual(result.selected, [])
        self.assertEqual([candidate.track_id for candidate in result.candidates], ["wrong"])

    def test_dry_run_spotify_confirmation_auto_selects_localized_title_by_date_duration(self) -> None:
        result = SpotifySearchResult(
            candidates=[
                SpotifyTrackCandidate(
                    "localized",
                    "물음",
                    ["Sān-Z", "HOYO-MiX"],
                    "물음",
                    "KR",
                    0,
                    isrc="FR10S2564999",
                    duration_ms=192000,
                    release_date="2025-12-18",
                    release_date_precision="day",
                ),
                SpotifyTrackCandidate(
                    "instrumental",
                    "물음 - Instrumental",
                    ["Sān-Z", "HOYO-MiX"],
                    "물음",
                    "KR",
                    1,
                    isrc="FR10S2565000",
                    duration_ms=192000,
                    release_date="2025-12-18",
                    release_date_precision="day",
                ),
            ],
            candidates_by_market={
                "KR": [
                    SpotifyTrackCandidate(
                        "localized",
                        "물음",
                        ["Sān-Z", "HOYO-MiX"],
                        "물음",
                        "KR",
                        0,
                        isrc="FR10S2564999",
                        duration_ms=192000,
                        release_date="2025-12-18",
                        release_date_precision="day",
                    ),
                    SpotifyTrackCandidate(
                        "instrumental",
                        "물음 - Instrumental",
                        ["Sān-Z", "HOYO-MiX"],
                        "물음",
                        "KR",
                        1,
                        isrc="FR10S2565000",
                        duration_ms=192000,
                        release_date="2025-12-18",
                        release_date_precision="day",
                    ),
                ]
            },
        )
        pair = PairMetadata(
            Path("a.m4a"),
            Path("a.ttml"),
            AudioMetadata(
                title="I Ask",
                artists=["Sān-Z & HOYO-MiX"],
                album="I Ask - Single",
                duration_seconds=192.1,
                release_date="2025-12-18",
            ),
            AppleMusicMetadataResult(),
            QQMusicSearchResult(),
            NCMusicSearchResult(),
            result,
        )

        confirm_spotify_candidates(
            [pair],
            dry_run=True,
            input_func=lambda prompt: (_ for _ in ()).throw(AssertionError("dry-run should not prompt")),
            print_func=lambda *values, **kwargs: None,
        )

        self.assertEqual([(candidate.track_id, candidate.title) for candidate in result.selected], [("localized", "물음")])

    def test_dry_run_spotify_confirmation_rejects_instrumental_when_source_is_not_instrumental(self) -> None:
        result = SpotifySearchResult(
            candidates=[
                SpotifyTrackCandidate(
                    "instrumental",
                    "I Ask - Instrumental",
                    ["Sān-Z", "HOYO-MiX"],
                    "I Ask",
                    "US",
                    0,
                    duration_ms=192000,
                    release_date="2025-12-18",
                    release_date_precision="day",
                ),
            ],
            candidates_by_market={
                "US": [
                    SpotifyTrackCandidate(
                        "instrumental",
                        "I Ask - Instrumental",
                        ["Sān-Z", "HOYO-MiX"],
                        "I Ask",
                        "US",
                        0,
                        duration_ms=192000,
                        release_date="2025-12-18",
                        release_date_precision="day",
                    )
                ]
            },
        )
        pair = PairMetadata(
            Path("a.m4a"),
            Path("a.ttml"),
            AudioMetadata(
                title="I Ask",
                artists=["Sān-Z & HOYO-MiX"],
                album="I Ask - Single",
                duration_seconds=192,
                release_date="2025-12-18",
            ),
            AppleMusicMetadataResult(),
            QQMusicSearchResult(),
            NCMusicSearchResult(),
            result,
        )

        confirm_spotify_candidates(
            [pair],
            dry_run=True,
            input_func=lambda prompt: (_ for _ in ()).throw(AssertionError("dry-run should not prompt")),
            print_func=lambda *values, **kwargs: None,
        )

        self.assertEqual(result.selected, [])

    def test_rejecting_spotify_candidates_prompts_for_one_of_five_choices_per_market(self) -> None:
        result = SpotifySearchResult(
            candidates=[
                SpotifyTrackCandidate(f"us-{index}", f"US Song {index}", ["A"], "Album", "US", index)
                for index in range(1, 7)
            ],
            candidates_by_market={
                "US": [
                    SpotifyTrackCandidate(f"us-{index}", f"US Song {index}", ["A"], "Album", "US", index)
                    for index in range(1, 7)
                ],
                "KR": [
                    SpotifyTrackCandidate(f"kr-{index}", f"KR Song {index}", ["A"], "Album", "KR", index)
                    for index in range(1, 4)
                ],
            },
        )
        pair = PairMetadata(
            Path("song.flac"),
            Path("song.ttml"),
            AudioMetadata(title="Song"),
            AppleMusicMetadataResult(),
            QQMusicSearchResult(),
            NCMusicSearchResult(),
            result,
        )
        answers = iter(["N", "4", ""])
        printed: list[str] = []

        confirm_spotify_candidates(
            [pair],
            dry_run=False,
            input_func=lambda prompt: next(answers),
            print_func=lambda *values, **kwargs: printed.append(" ".join(str(value) for value in values)),
        )

        self.assertEqual([candidate.track_id for candidate in result.selected], ["us-4"])
        self.assertTrue(any("US Spotify 候选" in line for line in printed))
        self.assertTrue(any("KR Spotify 候选" in line for line in printed))
        self.assertTrue(any("[us-1]" in line for line in printed))
        self.assertTrue(any("[us-5]" in line for line in printed))
        self.assertFalse(any("[us-6]" in line for line in printed))

    def test_values_from_metadata_dedupes_spotify_id_but_keeps_market_metadata_variants(self) -> None:
        values = values_from_metadata(
            AudioMetadata(title="Amazing Grace", artists=["邓紫棋"], album="Amazing Grace - Single"),
            spotify_candidates=[
                SpotifyTrackCandidate("same-id", "Amazing Grace", ["G.E.M."], "Amazing Grace", "US", 0),
                SpotifyTrackCandidate("same-id", "어메이징 그레이스", ["G.E.M. 덩쯔치"], "어메이징 그레이스", "KR", 1),
                SpotifyTrackCandidate(
                    "same-id",
                    "アメイジング・グレイス",
                    ["G.E.M."],
                    "アメイジング・グレイス",
                    "JP",
                    2,
                    isrc="HKA972401000",
                ),
            ],
        )

        self.assertEqual(values["spotifyId"], ["same-id"])
        self.assertEqual(values["musicName"], ["Amazing Grace", "어메이징 그레이스", "アメイジング・グレイス"])
        self.assertEqual(values["artists"], ["邓紫棋", "G.E.M.", "G.E.M. 덩쯔치"])
        self.assertEqual(values["album"], ["Amazing Grace - Single", "Amazing Grace", "어메이징 그레이스", "アメイジング・グレイス"])
        self.assertEqual(values["isrc"], ["HKA972401000"])

    def test_process_prepared_pair_prints_market_best_and_deduped_spotify_ids(self) -> None:
        result = SpotifySearchResult(
            candidates=[
                SpotifyTrackCandidate("same-id", "Amazing Grace", ["G.E.M."], "Amazing Grace", "US", 0),
                SpotifyTrackCandidate("same-id", "어메이징 그레이스", ["G.E.M."], "어메이징 그레이스", "KR", 1),
            ],
            selected=[
                SpotifyTrackCandidate("same-id", "Amazing Grace", ["G.E.M."], "Amazing Grace", "US", 0),
                SpotifyTrackCandidate("same-id", "어메이징 그레이스", ["G.E.M."], "어메이징 그레이스", "KR", 1),
            ],
            candidates_by_market={
                "US": [SpotifyTrackCandidate("same-id", "Amazing Grace", ["G.E.M."], "Amazing Grace", "US", 0)],
                "KR": [SpotifyTrackCandidate("same-id", "어메이징 그레이스", ["G.E.M."], "어메이징 그레이스", "KR", 1)],
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.ttml"
            path.write_text(REFERENCE_STYLE_TTML, encoding="utf-8")
            pair = PairMetadata(
                Path("song.flac"),
                path,
                AudioMetadata(title="Amazing Grace"),
                AppleMusicMetadataResult(),
                QQMusicSearchResult(),
                NCMusicSearchResult(),
                result,
            )
            stdout = StringIO()

            with redirect_stdout(stdout):
                _process_prepared_pair(pair, dry_run=True)

        output = stdout.getvalue()
        self.assertIn("spotifyBest:", output)
        self.assertIn("    - US: Amazing Grace - G.E.M. - Amazing Grace [same-id]", output)
        self.assertIn("    - KR: 어메이징 그레이스 - G.E.M. - 어메이징 그레이스 [same-id]", output)
        self.assertIn("spotifyId: same-id", output)
        self.assertNotIn("spotifyId: same-id, same-id", output)

    def test_process_prepared_pair_writes_successful_source_when_another_source_warns(self) -> None:
        qq_result = QQMusicSearchResult(
            candidates=[QQMusicCandidate("224116257", "001hrIGe3flaPr", "玫瑰少年", "", ["蔡依林"], "UGLY BEAUTY", 0)],
            selected=QQMusicCandidate("224116257", "001hrIGe3flaPr", "玫瑰少年", "", ["蔡依林"], "UGLY BEAUTY", 0),
            errors=["QQ 音乐搜索曾短暂失败后恢复"],
        )
        apple_result = AppleMusicMetadataResult(errors=["Apple Music 搜索失败: temporary outage"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.ttml"
            path.write_text(REFERENCE_STYLE_TTML, encoding="utf-8")
            pair = PairMetadata(
                Path("song.flac"),
                path,
                AudioMetadata(title="玫瑰少年"),
                apple_result,
                qq_result,
                NCMusicSearchResult(),
                SpotifySearchResult(),
            )
            stdout = StringIO()

            with redirect_stdout(stdout):
                _process_prepared_pair(pair, dry_run=False)

            after = path.read_text(encoding="utf-8")

        self.assertIn('<amll:meta key="qqMusicId" value="224116257"/>', after)
        self.assertIn("lookup warning: Apple Music 搜索失败: temporary outage", stdout.getvalue())
        self.assertIn("lookup warning: QQ 音乐搜索曾短暂失败后恢复", stdout.getvalue())

    def test_main_dry_run_skips_spotify_when_credentials_are_missing(self) -> None:
        class NoAppleLookupClient:
            def fetch_album_tracks(self, store, album_id):
                raise AssertionError("TTML-only work should not call Apple Music")

        class NoQQCandidatesClient:
            def search_songs(self, query):
                return []

        class NoNCMCandidatesClient:
            def search_songs(self, context):
                return []

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = directory / "song.ttml"
            path.write_text(
                (
                    '<tt xmlns="http://www.w3.org/ns/ttml" '
                    'xmlns:amll="http://www.example.com/ns/amll">'
                    '<head><metadata>'
                    '<amll:meta key="musicName" value="玫瑰少年"/>'
                    '<amll:meta key="artists" value="蔡依林"/>'
                    '<amll:meta key="album" value="UGLY BEAUTY"/>'
                    "</metadata></head><body/></tt>"
                ),
                encoding="utf-8",
            )
            stdout = StringIO()
            cwd = os.getcwd()

            try:
                os.chdir(directory)
                with (
                    patch.dict(os.environ, {}, clear=True),
                    patch("fill_ttml_metadata.AppleMusicClient", return_value=NoAppleLookupClient()),
                    patch("fill_ttml_metadata.QQMusicClient", return_value=NoQQCandidatesClient()),
                    patch("fill_ttml_metadata.NCMusicClient", return_value=NoNCMCandidatesClient()),
                    redirect_stdout(stdout),
                ):
                    exit_code = main(["--ttml", str(path), "--dry-run"])
            finally:
                os.chdir(cwd)

        self.assertEqual(exit_code, 0)
        self.assertIn("spotifyBest: -", stdout.getvalue())
        self.assertIn("spotifyId: -", stdout.getvalue())
        self.assertIn("缺少 SPOTIFY_CLIENT_ID 或 SPOTIFY_CLIENT_SECRET，跳过 Spotify 搜索", stdout.getvalue())

    def test_main_uses_three_search_workers_by_default_and_preserves_output_order(self) -> None:
        active = 0
        max_active = 0
        lock = threading.Lock()
        processed: list[str] = []

        def prepare(work_item, *args):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                time.sleep(0.03)
                return PairMetadata(
                    None,
                    work_item.ttml_path,
                    AudioMetadata(title=work_item.ttml_path.stem),
                    AppleMusicMetadataResult(),
                    QQMusicSearchResult(),
                    NCMusicSearchResult(),
                    SpotifySearchResult(),
                )
            finally:
                with lock:
                    active -= 1

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for index in range(4):
                (directory / f"{index + 1:02d}.ttml").write_text(REFERENCE_STYLE_TTML, encoding="utf-8")

            with (
                patch.dict(os.environ, {}, clear=True),
                patch("fill_ttml_metadata._prepare_work_item", side_effect=prepare),
                patch("fill_ttml_metadata._collect_ncm_music_metadata_for_pairs"),
                patch("fill_ttml_metadata.confirm_apple_music_candidates"),
                patch("fill_ttml_metadata.confirm_qq_music_candidates"),
                patch("fill_ttml_metadata.confirm_ncm_music_candidates"),
                patch("fill_ttml_metadata.confirm_spotify_candidates"),
                patch("fill_ttml_metadata._process_prepared_pair", side_effect=lambda pair, **kwargs: processed.append(pair.ttml_path.name)),
            ):
                exit_code = main([str(directory), "--dry-run"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(max_active, 3)
        self.assertEqual(processed, ["01.ttml", "02.ttml", "03.ttml", "04.ttml"])

    def test_main_search_workers_can_be_reduced_to_one(self) -> None:
        active = 0
        max_active = 0
        lock = threading.Lock()

        def prepare(work_item, *args):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                time.sleep(0.02)
                return PairMetadata(
                    None,
                    work_item.ttml_path,
                    AudioMetadata(title=work_item.ttml_path.stem),
                    AppleMusicMetadataResult(),
                    QQMusicSearchResult(),
                    NCMusicSearchResult(),
                    SpotifySearchResult(),
                )
            finally:
                with lock:
                    active -= 1

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for index in range(3):
                (directory / f"{index + 1:02d}.ttml").write_text(REFERENCE_STYLE_TTML, encoding="utf-8")

            with (
                patch.dict(os.environ, {}, clear=True),
                patch("fill_ttml_metadata._prepare_work_item", side_effect=prepare),
                patch("fill_ttml_metadata._collect_ncm_music_metadata_for_pairs"),
                patch("fill_ttml_metadata.confirm_apple_music_candidates"),
                patch("fill_ttml_metadata.confirm_qq_music_candidates"),
                patch("fill_ttml_metadata.confirm_ncm_music_candidates"),
                patch("fill_ttml_metadata.confirm_spotify_candidates"),
                patch("fill_ttml_metadata._process_prepared_pair"),
            ):
                exit_code = main([str(directory), "--dry-run", "--search-workers", "1"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(max_active, 1)

    def test_main_rejects_non_positive_search_workers(self) -> None:
        stderr = StringIO()

        with self.assertRaises(SystemExit) as raised:
            with redirect_stderr(stderr):
                main([".", "--dry-run", "--search-workers", "0"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--search-workers must be at least 1", stderr.getvalue())

    def test_main_parallel_prepare_failure_does_not_drop_successful_items(self) -> None:
        processed: list[str] = []

        def prepare(work_item, *args):
            if work_item.ttml_path.name == "02.ttml":
                raise RuntimeError("boom")
            return PairMetadata(
                None,
                work_item.ttml_path,
                AudioMetadata(title=work_item.ttml_path.stem),
                AppleMusicMetadataResult(),
                QQMusicSearchResult(),
                NCMusicSearchResult(),
                SpotifySearchResult(),
            )

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for index in range(3):
                (directory / f"{index + 1:02d}.ttml").write_text(REFERENCE_STYLE_TTML, encoding="utf-8")
            stderr = StringIO()

            with (
                patch.dict(os.environ, {}, clear=True),
                patch("fill_ttml_metadata._prepare_work_item", side_effect=prepare),
                patch("fill_ttml_metadata._collect_ncm_music_metadata_for_pairs"),
                patch("fill_ttml_metadata.confirm_apple_music_candidates"),
                patch("fill_ttml_metadata.confirm_qq_music_candidates"),
                patch("fill_ttml_metadata.confirm_ncm_music_candidates"),
                patch("fill_ttml_metadata.confirm_spotify_candidates"),
                patch("fill_ttml_metadata._process_prepared_pair", side_effect=lambda pair, **kwargs: processed.append(pair.ttml_path.name)),
                redirect_stderr(stderr),
            ):
                exit_code = main([str(directory), "--dry-run", "--search-workers", "3"])

        self.assertEqual(exit_code, 1)
        self.assertEqual(processed, ["01.ttml", "03.ttml"])
        self.assertIn("[error] 02.ttml: boom", stderr.getvalue())


class TtmlOnlyMetadataTests(unittest.TestCase):
    def write_ttml(self, directory: Path, name: str = "song.ttml", body: str | None = None) -> Path:
        path = directory / name
        path.write_text(body or REFERENCE_STYLE_TTML, encoding="utf-8")
        return path

    def metadata_ttml(self, metadata_inner: str) -> str:
        return (
            '<tt xmlns="http://www.w3.org/ns/ttml" '
            'xmlns:amll="http://www.example.com/ns/amll">'
            f"<head><metadata>{metadata_inner}</metadata></head><body/></tt>"
        )

    def test_reads_title_artists_and_album_from_ttml_metadata(self) -> None:
        text = self.metadata_ttml(
            '<amll:meta key="musicName" value="玫瑰少年"/>'
            '<amll:meta key="musicName" value="*"/>'
            '<amll:meta key="artists" value="蔡依林"/>'
            '<amll:meta key="artists" value=""/>'
            '<amll:meta key="album" value="UGLY BEAUTY"/>'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(Path(tmp), body=text)

            metadata = read_ttml_metadata(path)

        self.assertEqual(metadata.title, "玫瑰少年")
        self.assertEqual(metadata.artists, ["蔡依林"])
        self.assertEqual(metadata.album, "UGLY BEAUTY")
        self.assertIsNone(metadata.isrc)
        self.assertIsNone(metadata.catalog_id)
        self.assertIsNone(metadata.playlist_id)

    def test_reads_apple_music_id_and_isrc_from_ttml_metadata(self) -> None:
        text = self.metadata_ttml(
            '<amll:meta key="musicName" value="玫瑰少年"/>'
            '<amll:meta key="artists" value="蔡依林"/>'
            '<amll:meta key="album" value="UGLY BEAUTY"/>'
            '<amll:meta key="appleMusicId" value="1458862568"/>'
            '<amll:meta key="isrc" value="TWA471900001"/>'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(Path(tmp), body=text)

            metadata = read_ttml_metadata(path)

        self.assertEqual(metadata.title, "玫瑰少年")
        self.assertEqual(metadata.artists, ["蔡依林"])
        self.assertEqual(metadata.album, "UGLY BEAUTY")
        self.assertEqual(metadata.catalog_id, "1458862568")
        self.assertEqual(metadata.isrc, "TWA471900001")

    def test_reads_ttml_metadata_with_missing_amll_namespace(self) -> None:
        text = (
            '<tt xmlns="http://www.w3.org/ns/ttml">'
            '<head><metadata><amll:meta key="musicName" value="玫瑰少年"/></metadata></head>'
            "<body/></tt>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(Path(tmp), body=text)

            metadata = read_ttml_metadata(path)

        self.assertEqual(metadata.title, "玫瑰少年")

    def test_batch_discovery_includes_unmatched_ttml_as_ttml_only_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.write_ttml(directory, "matched.ttml")
            (directory / "matched.flac").write_text("", encoding="utf-8")
            self.write_ttml(directory, "lyrics-only.ttml")
            self.write_ttml(directory, "ambiguous.ttml")
            (directory / "ambiguous.mp3").write_text("", encoding="utf-8")
            (directory / "ambiguous.m4a").write_text("", encoding="utf-8")

            work_items, warnings = find_directory_work_items(directory)

        item_map = {item.ttml_path.name: item.audio_path.name if item.audio_path else None for item in work_items}
        self.assertEqual(item_map["matched.ttml"], "matched.flac")
        self.assertIsNone(item_map["lyrics-only.ttml"])
        self.assertNotIn("ambiguous.ttml", item_map)
        self.assertEqual(warnings, ["ambiguous.ttml: multiple same-stem audio files found: ambiguous.m4a, ambiguous.mp3"])

    def test_ttml_only_preparation_reuses_title_query_and_artist_album_ranking(self) -> None:
        class AppleSearchClient:
            def __init__(self):
                self.search_calls = []

            def fetch_album_tracks(self, store, album_id):
                raise AssertionError("TTML-only work without playlist should not call Apple Music album lookup")

            def search_songs(self, store, metadata):
                self.search_calls.append((store, metadata.title, metadata.catalog_id))
                return [
                    AppleMusicTrackCandidate(
                        f"{store}-apple",
                        "玫瑰少年" if store in {"cn", "tw"} else f"玫瑰少年 {store}",
                        ["蔡依林"],
                        "UGLY BEAUTY",
                        store,
                        0,
                        match_source="search",
                    )
                ]

            def search_artists(self, store, query):
                return []

            def fetch_artist_albums(self, store, artist_id):
                return [], []

        class SearchClient:
            def search_songs(self, query):
                self.query = query
                return [
                    QQMusicCandidate("235883438", "0035sVym0anwc4", "玫瑰少年", "", ["五月天"], "玫瑰少年", 0),
                    QQMusicCandidate("224116257", "001hrIGe3flaPr", "玫瑰少年", "", ["JOLIN蔡依林"], "UGLY BEAUTY", 1),
                    QQMusicCandidate(
                        "415233914",
                        "003YUKMv2dcOZq",
                        "玫瑰少年 - From THE FIRST TAKE",
                        "",
                        ["蔡依林"],
                        "玫瑰少年 - From THE FIRST TAKE",
                        2,
                    ),
                ]

        class NCMSearchClient:
            def search_songs(self, context):
                self.context = context
                return [
                    NCMusicCandidate("33894312", "玫瑰少年", [], ["五月天"], "玫瑰少年", 0),
                    NCMusicCandidate("1375248354", "玫瑰少年", [], ["蔡依林"], "UGLY BEAUTY", 1),
                ]

        text = self.metadata_ttml(
            '<amll:meta key="musicName" value="玫瑰少年"/>'
            '<amll:meta key="artists" value="蔡依林"/>'
            '<amll:meta key="album" value="UGLY BEAUTY"/>'
            '<amll:meta key="appleMusicId" value="1458862568"/>'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(Path(tmp), body=text)
            apple_client = AppleSearchClient()
            qq_client = SearchClient()
            ncm_client = NCMSearchClient()

            pair = _prepare_work_item(WorkItem(ttml_path=path), apple_client, qq_client, ncm_client)

        self.assertIsNone(pair.audio_path)
        self.assertEqual(pair.metadata.title, "玫瑰少年")
        self.assertEqual(pair.metadata.artists, ["蔡依林"])
        self.assertEqual(pair.metadata.album, "UGLY BEAUTY")
        self.assertEqual(pair.metadata.catalog_id, "1458862568")
        self.assertEqual(apple_client.search_calls, [(store, "玫瑰少年", "1458862568") for store in DEFAULT_STORES])
        self.assertEqual(pair.apple_music_metadata.values["appleMusicId"], ["1458862568", "cn-apple", "us-apple", "kr-apple", "jp-apple", "tw-apple"])
        self.assertEqual(qq_client.query, "玫瑰少年")
        self.assertEqual([candidate.song_id for candidate in pair.qq_music_metadata.candidates], ["224116257", "415233914", "235883438"])
        self.assertFalse(hasattr(ncm_client, "context"))

        pair.qq_music_metadata.selected = pair.qq_music_metadata.candidates[1]
        _collect_ncm_music_metadata_for_pairs([pair], ncm_client)

        self.assertEqual(ncm_client.context.titles, ["玫瑰少年", "玫瑰少年 - From THE FIRST TAKE"])
        self.assertEqual(ncm_client.context.artists, ["蔡依林"])
        self.assertEqual(ncm_client.context.albums, ["UGLY BEAUTY", "玫瑰少年 - From THE FIRST TAKE"])
        self.assertEqual([candidate.song_id for candidate in pair.ncm_music_metadata.candidates], ["1375248354", "33894312"])

    def test_ttml_only_cli_without_audio_is_accepted_and_reports_missing_title(self) -> None:
        text = self.metadata_ttml('<amll:meta key="artists" value="蔡依林"/>')
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(Path(tmp), body=text)
            stderr = StringIO()

            with redirect_stderr(stderr):
                exit_code = main(["--ttml", str(path), "--dry-run"])

        self.assertEqual(exit_code, 1)
        self.assertIn("TTML 中未读取到歌名，跳过 QQ 音乐搜索", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
