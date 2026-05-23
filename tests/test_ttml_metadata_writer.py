import tempfile
import time
import unittest
from contextlib import redirect_stderr
from io import StringIO, TextIOWrapper
import json
from pathlib import Path

from fill_ttml_metadata import (
    AudioMetadata,
    AppleMusicMetadataResult,
    DEFAULT_STORES,
    NCMusicCandidate,
    NCMusicClient,
    NCMusicSearchResult,
    PairMetadata,
    QQMusicCandidate,
    QQMusicClient,
    QQMusicSearchResult,
    collect_apple_music_metadata,
    collect_ncm_music_metadata,
    collect_qq_music_metadata,
    confirm_ncm_music_candidates,
    confirm_qq_music_candidates,
    find_directory_work_items,
    main,
    read_ttml_metadata,
    update_ttml_metadata,
    values_from_metadata,
    WorkItem,
    _flatten_tags,
    _prepare_work_item,
    _parse_ncm_music_candidates,
    _parse_qq_music_candidates,
    _safe_print,
)


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
                    "appleMusicId": ["1691701944"],
                },
                dry_run=False,
            )

            after = path.read_text(encoding="utf-8")
            expected_insert = (
                '<amll:meta key="album" value="Album"/>'
                '<amll:meta key="qqMusicId" value="235883438"/>'
                '<amll:meta key="qqMusicId" value="0035sVym0anwc4"/>'
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

    def test_collects_metadata_from_all_default_storefronts_without_stopping_at_first_match(self) -> None:
        class RecordingClient:
            def __init__(self):
                self.calls = []

            def fetch_album_tracks(self, store, album_id):
                self.calls.append((store, album_id))
                names = {
                    "cn": ("Song", "Artist", "Album", "111"),
                    "tw": ("Song", "Artist", "Album", "111"),
                    "jp": ("曲", "Artist JP", "アルバム", "222"),
                    "kr": ("노래", "Artist KR", "앨범", "333"),
                    "us": ("Song", "Artist", "Album", "444"),
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
        self.assertEqual(result.values["musicName"], ["Song", "曲", "노래"])
        self.assertEqual(result.values["artists"], ["Artist", "Artist JP", "Artist KR"])
        self.assertEqual(result.values["album"], ["Album", "アルバム", "앨범"])
        self.assertEqual(result.values["appleMusicId"], ["111", "222", "333", "444"])
        self.assertEqual(result.values["isrc"], ["TST000000001"])
        self.assertEqual(
            result.sources,
            [
                "album:cn:track",
                "album:tw:track",
                "album:jp:track",
                "album:kr:track",
                "album:us:track",
            ],
        )

    def test_catalog_id_without_playlist_only_writes_existing_song_id(self) -> None:
        class NoLookupClient:
            def fetch_album_tracks(self, store, album_id):
                raise AssertionError("catalog-only metadata should not require album lookup")

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
            def search_songs(self, query):
                self.query = query
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

        self.assertEqual(client.query, "玫瑰少年")
        self.assertEqual([candidate.song_id for candidate in result.candidates], ["224116257", "415233914", "235883438"])

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
        class NoAppleLookupClient:
            def fetch_album_tracks(self, store, album_id):
                raise AssertionError("TTML-only work should not call Apple Music")

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
            def search_songs(self, query):
                self.query = query
                return [
                    NCMusicCandidate("33894312", "玫瑰少年", [], ["五月天"], "玫瑰少年", 0),
                    NCMusicCandidate("1375248354", "玫瑰少年", [], ["蔡依林"], "UGLY BEAUTY", 1),
                ]

        text = self.metadata_ttml(
            '<amll:meta key="musicName" value="玫瑰少年"/>'
            '<amll:meta key="artists" value="蔡依林"/>'
            '<amll:meta key="album" value="UGLY BEAUTY"/>'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(Path(tmp), body=text)
            qq_client = SearchClient()
            ncm_client = NCMSearchClient()

            pair = _prepare_work_item(WorkItem(ttml_path=path), NoAppleLookupClient(), qq_client, ncm_client)

        self.assertIsNone(pair.audio_path)
        self.assertEqual(pair.metadata.title, "玫瑰少年")
        self.assertEqual(pair.metadata.artists, ["蔡依林"])
        self.assertEqual(pair.metadata.album, "UGLY BEAUTY")
        self.assertEqual(qq_client.query, "玫瑰少年")
        self.assertEqual(ncm_client.query, "玫瑰少年")
        self.assertEqual([candidate.song_id for candidate in pair.qq_music_metadata.candidates], ["224116257", "415233914", "235883438"])
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
