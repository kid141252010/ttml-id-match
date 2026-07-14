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

    @unittest.skip("v1 CLI removed; deterministic v2 CLI is covered by test_v2_cli")
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

if __name__ == "__main__":
    unittest.main()

