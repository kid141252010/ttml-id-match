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



if __name__ == "__main__":
    unittest.main()
