from __future__ import annotations

from .apple_music import (
    AppleMusicClient,
    collect_apple_music_metadata,
    confirm_apple_music_candidates,
    is_valid_apple_music_song_id,
    _AppleMusicAlbumCandidate,
    _AppleMusicArtistCandidate,
    _format_apple_music_candidate,
    _format_apple_music_candidate_list,
    _parse_apple_music_artist_album_candidates,
    _sync_apple_music_result_values,
)
from .audio import read_audio_metadata, _flatten_tags
from .cli import find_directory_pairs, find_directory_work_items
from .console import _safe_print, _color_text
from .models import (
    DEFAULT_STORES,
    DEFAULT_SPOTIFY_MARKETS,
    SPOTIFY_SEARCH_LIMIT,
    AudioMetadata,
    AppleMusicMetadataResult,
    AppleMusicTrackCandidate,
    NCMusicCandidate,
    NCMusicSearchContext,
    NCMusicSearchResult,
    PairMetadata,
    QQMusicCandidate,
    QQMusicSearchResult,
    SpotifyCredentials,
    SpotifySearchResult,
    SpotifyTrackCandidate,
    WorkItem,
)
from .ncm_music import NCMusicClient, collect_ncm_music_metadata, confirm_ncm_music_candidates, _parse_ncm_music_candidates
from .orchestration import _collect_ncm_music_metadata_for_pairs, _prepare_work_item, _process_prepared_pair, values_from_metadata
from .qq_music import QQMusicClient, collect_qq_music_metadata, confirm_qq_music_candidates, _parse_qq_music_candidates
from .spotify import (
    SpotifyClient,
    collect_spotify_metadata,
    confirm_spotify_candidates,
    load_spotify_credentials,
    _parse_spotify_candidates,
    _spotify_candidate_score,
    _spotify_search_queries,
)
from .text_utils import split_artists, _text_match_score
from .ttml import normalize_ttml_language, read_ttml_metadata, update_ttml_metadata


def main(argv: list[str] | None = None) -> int:
    from . import cli as _cli

    _cli.AppleMusicClient = AppleMusicClient
    _cli.QQMusicClient = QQMusicClient
    _cli.NCMusicClient = NCMusicClient
    _cli.SpotifyClient = SpotifyClient
    _cli.confirm_apple_music_candidates = confirm_apple_music_candidates
    _cli.confirm_qq_music_candidates = confirm_qq_music_candidates
    _cli.confirm_ncm_music_candidates = confirm_ncm_music_candidates
    _cli.confirm_spotify_candidates = confirm_spotify_candidates
    _cli.find_directory_work_items = find_directory_work_items
    _cli.load_spotify_credentials = load_spotify_credentials
    _cli.normalize_ttml_language = normalize_ttml_language
    _cli._collect_ncm_music_metadata_for_pairs = _collect_ncm_music_metadata_for_pairs
    _cli._prepare_work_item = _prepare_work_item
    _cli._process_prepared_pair = _process_prepared_pair
    return _cli.main(argv)


__all__ = [
    "AudioMetadata",
    "AppleMusicMetadataResult",
    "AppleMusicTrackCandidate",
    "DEFAULT_STORES",
    "DEFAULT_SPOTIFY_MARKETS",
    "SPOTIFY_SEARCH_LIMIT",
    "NCMusicCandidate",
    "NCMusicClient",
    "NCMusicSearchContext",
    "NCMusicSearchResult",
    "PairMetadata",
    "QQMusicCandidate",
    "QQMusicClient",
    "QQMusicSearchResult",
    "SpotifyClient",
    "SpotifyCredentials",
    "SpotifySearchResult",
    "SpotifyTrackCandidate",
    "collect_apple_music_metadata",
    "collect_ncm_music_metadata",
    "collect_qq_music_metadata",
    "collect_spotify_metadata",
    "confirm_apple_music_candidates",
    "confirm_ncm_music_candidates",
    "confirm_qq_music_candidates",
    "confirm_spotify_candidates",
    "find_directory_pairs",
    "find_directory_work_items",
    "is_valid_apple_music_song_id",
    "load_spotify_credentials",
    "main",
    "normalize_ttml_language",
    "read_audio_metadata",
    "read_ttml_metadata",
    "split_artists",
    "update_ttml_metadata",
    "values_from_metadata",
    "WorkItem",
    "_collect_ncm_music_metadata_for_pairs",
    "_flatten_tags",
    "_AppleMusicAlbumCandidate",
    "_AppleMusicArtistCandidate",
    "_format_apple_music_candidate",
    "_format_apple_music_candidate_list",
    "_parse_apple_music_artist_album_candidates",
    "_prepare_work_item",
    "_parse_ncm_music_candidates",
    "_parse_qq_music_candidates",
    "_parse_spotify_candidates",
    "_process_prepared_pair",
    "_safe_print",
    "_color_text",
    "_spotify_candidate_score",
    "_spotify_search_queries",
    "_sync_apple_music_result_values",
    "_text_match_score",
]
