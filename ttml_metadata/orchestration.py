from __future__ import annotations

from pathlib import Path

from .apple_music import (
    _apple_music_storefront_top_candidates,
    _format_apple_music_candidate,
    _merge_apple_music_metadata,
    collect_apple_music_metadata,
    confirm_apple_music_candidates,
)
from .audio import read_audio_metadata
from .console import _safe_print, _color_text
from .models import (
    AppleMusicClientProtocol,
    AppleMusicTrackCandidate,
    AudioMetadata,
    NCMusicCandidate,
    NCMusicClientProtocol,
    NCMusicSearchResult,
    PairMetadata,
    QQMusicCandidate,
    QQMusicClientProtocol,
    SpotifyClientProtocol,
    SpotifyTrackCandidate,
    TtmlLanguageNormalizationResult,
    WorkItem,
)
from .ncm_music import NCMusicClient, _format_ncm_music_candidate, _merge_ncm_music_metadata, collect_ncm_music_metadata, confirm_ncm_music_candidates
from .qq_music import QQMusicClient, _format_qq_music_candidate, _merge_qq_music_metadata, collect_qq_music_metadata, confirm_qq_music_candidates
from .spotify import _format_spotify_candidate, _merge_spotify_metadata, _unique_spotify_ids, collect_spotify_metadata
from .text_utils import _add_unique_value, split_artists
from .ttml import read_ttml_metadata, update_ttml_metadata
from .search_scheduler import BatchSearchCache, SearchClients, collect_ncm_for_pairs, prepare_one_work_item

def values_from_metadata(
    metadata: AudioMetadata,
    apple_music_values: dict[str, list[str]] | None = None,
    apple_music_candidates: Iterable[AppleMusicTrackCandidate] | None = None,
    qq_music_candidate: QQMusicCandidate | None = None,
    ncm_music_candidate: NCMusicCandidate | None = None,
    spotify_candidates: Iterable[SpotifyTrackCandidate] | None = None,
) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    if metadata.title:
        _add_unique_value(values, "musicName", metadata.title)
    if metadata.artists:
        for artist in split_artists(metadata.artists):
            _add_unique_value(values, "artists", artist)
    if metadata.album:
        _add_unique_value(values, "album", metadata.album)
    for key, proposed_values in (apple_music_values or {}).items():
        for value in proposed_values:
            _add_unique_value(values, key, value)
    for apple_music_candidate in apple_music_candidates or []:
        _merge_apple_music_metadata(values, metadata, apple_music_candidate)
    if qq_music_candidate:
        _merge_qq_music_metadata(values, metadata, qq_music_candidate)
    if ncm_music_candidate:
        _merge_ncm_music_metadata(values, metadata, ncm_music_candidate)
    for spotify_candidate in spotify_candidates or []:
        _merge_spotify_metadata(values, metadata, spotify_candidate)
    if metadata.isrc:
        _add_unique_value(values, "isrc", metadata.isrc)
    return values


def _prepare_pair(
    audio_path: Path,
    ttml_path: Path,
    apple_music_client: AppleMusicClientProtocol,
    qq_music_client: QQMusicClientProtocol,
    ncm_music_client: NCMusicClientProtocol | None = None,
    spotify_client: SpotifyClientProtocol | None = None,
) -> PairMetadata:
    metadata = read_audio_metadata(audio_path)
    apple_music_metadata = collect_apple_music_metadata(metadata, apple_music_client)
    qq_music_metadata = collect_qq_music_metadata(metadata, qq_music_client)
    spotify_metadata = collect_spotify_metadata(metadata, spotify_client)
    return PairMetadata(
        audio_path,
        ttml_path,
        metadata,
        apple_music_metadata,
        qq_music_metadata,
        NCMusicSearchResult(),
        spotify_metadata,
    )


def _prepare_work_item(
    work_item: WorkItem,
    apple_music_client: AppleMusicClientProtocol,
    qq_music_client: QQMusicClientProtocol,
    ncm_music_client: NCMusicClientProtocol | None = None,
    spotify_client: SpotifyClientProtocol | None = None,
) -> PairMetadata:
    return prepare_one_work_item(
        work_item,
        SearchClients(
            apple_music=apple_music_client,
            qq_music=qq_music_client,
            ncm_music=ncm_music_client or NCMusicClient(),
            spotify=spotify_client,
        ),
    )


def _process_pair(
    audio_path: Path,
    ttml_path: Path,
    client: AppleMusicClientProtocol,
    dry_run: bool,
    qq_music_client: QQMusicClientProtocol | None = None,
    ncm_music_client: NCMusicClientProtocol | None = None,
    spotify_client: SpotifyClientProtocol | None = None,
) -> None:
    pair = _prepare_pair(
        audio_path,
        ttml_path,
        client,
        qq_music_client or QQMusicClient(),
        ncm_music_client,
        spotify_client,
    )
    confirm_apple_music_candidates([pair], dry_run=dry_run)
    confirm_qq_music_candidates([pair], dry_run=dry_run)
    _collect_ncm_music_metadata_for_pairs([pair], ncm_music_client or NCMusicClient())
    confirm_ncm_music_candidates([pair], dry_run=dry_run)
    _process_prepared_pair(pair, dry_run=dry_run)


def _collect_ncm_music_metadata_for_pairs(
    pairs: list[PairMetadata],
    ncm_music_client: NCMusicClientProtocol,
    max_workers: int = 1,
) -> None:
    collect_ncm_for_pairs(
        pairs,
        ncm_music_client,
        max_workers=max_workers,
        cache=BatchSearchCache(),
    )


def _process_prepared_pair(
    pair: PairMetadata,
    dry_run: bool,
    backup_paths: dict[Path, Path] | None = None,
) -> None:
    values = values_from_metadata(
        pair.metadata,
        pair.apple_music_metadata.values,
        qq_music_candidate=pair.qq_music_metadata.selected,
        ncm_music_candidate=pair.ncm_music_metadata.selected,
        spotify_candidates=pair.spotify_metadata.selected,
    )
    audio_path = pair.audio_path
    ttml_path = pair.ttml_path
    apple_music_metadata = pair.apple_music_metadata
    qq_music_metadata = pair.qq_music_metadata
    ncm_music_metadata = pair.ncm_music_metadata
    spotify_metadata = pair.spotify_metadata
    result = update_ttml_metadata(ttml_path, values, dry_run=dry_run, backup_paths=backup_paths)

    status = "dry-run" if dry_run else "updated"
    if not result.changed:
        status = "unchanged"
    status_colored = _color_text(f"[{status}]", status)
    _safe_print(f"{status_colored} {ttml_path.name}")
    _safe_print(f"  audio: {audio_path.name if audio_path else '-'}")

    # Apple Music Best
    am_best = _apple_music_storefront_top_candidates(apple_music_metadata)
    if not am_best:
        _safe_print("  appleMusicBest: -")
    else:
        _safe_print("  appleMusicBest:")
        for candidate in am_best:
            _safe_print(f"    - {_format_apple_music_candidate(candidate)}")

    # Apple Music ID
    am_ids = apple_music_metadata.values.get('appleMusicId', [])
    if not am_ids:
        _safe_print("  appleMusicId: -")
    elif len(am_ids) == 1:
        _safe_print(f"  appleMusicId: {am_ids[0]}")
    else:
        _safe_print("  appleMusicId:")
        for item_id in am_ids:
            _safe_print(f"    - {item_id}")

    # Apple Music Sources
    am_sources = apple_music_metadata.sources
    if not am_sources:
        _safe_print("  appleMusicSources: -")
    elif len(am_sources) == 1:
        _safe_print(f"  appleMusicSources: {am_sources[0]}")
    else:
        _safe_print("  appleMusicSources:")
        for src in am_sources:
            _safe_print(f"    - {src}")

    if apple_music_metadata.errors:
        for error in apple_music_metadata.errors:
            _safe_print(f"  {_color_text('lookup warning:', 'warning')} {error}")

    # QQ Music
    best_qq = qq_music_metadata.candidates[0] if qq_music_metadata.candidates else None
    _safe_print(f"  qqMusicBest: {_format_qq_music_candidate(best_qq) if best_qq else '-'}")
    selected_qq = qq_music_metadata.selected
    if not selected_qq:
        _safe_print("  qqMusicId: -")
    else:
        _safe_print("  qqMusicId:")
        _safe_print(f"    - {selected_qq.song_id}")
        _safe_print(f"    - {selected_qq.mid}")

    if qq_music_metadata.errors:
        for error in qq_music_metadata.errors:
            _safe_print(f"  {_color_text('lookup warning:', 'warning')} {error}")

    # NetEase Cloud Music
    best_ncm = ncm_music_metadata.candidates[0] if ncm_music_metadata.candidates else None
    _safe_print(f"  ncmMusicBest: {_format_ncm_music_candidate(best_ncm) if best_ncm else '-'}")
    selected_ncm = ncm_music_metadata.selected
    _safe_print(f"  ncmMusicId: {selected_ncm.song_id if selected_ncm else '-'}")

    if ncm_music_metadata.errors:
        for error in ncm_music_metadata.errors:
            _safe_print(f"  {_color_text('lookup warning:', 'warning')} {error}")

    # Spotify Best
    if not spotify_metadata.selected:
        _safe_print("  spotifyBest: -")
    else:
        _safe_print("  spotifyBest:")
        for candidate in spotify_metadata.selected:
            _safe_print(f"    - {_format_spotify_candidate(candidate)}")

    # Spotify ID
    sp_ids = _unique_spotify_ids(spotify_metadata.selected)
    if not sp_ids:
        _safe_print("  spotifyId: -")
    elif len(sp_ids) == 1:
        _safe_print(f"  spotifyId: {sp_ids[0]}")
    else:
        _safe_print("  spotifyId:")
        for s_id in sp_ids:
            _safe_print(f"    - {s_id}")

    if spotify_metadata.errors:
        for error in spotify_metadata.errors:
            _safe_print(f"  {_color_text('lookup warning:', 'warning')} {error}")

    _print_change_group("added", result.added)
    _print_change_group("replaced", result.replaced)
    _print_change_group("skipped", result.skipped)
    if result.backup_path:
        _safe_print(f"  backup: {result.backup_path}")


def _print_language_normalization_result(
    ttml_path: Path,
    result: TtmlLanguageNormalizationResult,
    dry_run: bool,
) -> None:
    if not result.changed:
        return

    status = "dry-run" if dry_run else "normalized"
    status_colored = _color_text(f"[{status}]", status)
    _safe_print(f"{status_colored} {ttml_path.name}")
    if result.language_changed:
        _safe_print("  language: zh-Hant -> zh-Hans")
    if result.body_text_changed:
        _safe_print("  lyrics: traditional -> simplified")
    if result.removed_translations:
        _safe_print(f"  removed: zh-Hans replacement translations = {result.removed_translations}")
    if result.removed_transliterations:
        _safe_print(f"  removed: zh-Latn-pinyin transliterations = {result.removed_transliterations}")
    if result.backup_path:
        _safe_print(f"  backup: {result.backup_path}")


def _print_change_group(label: str, changes: dict[str, list[str]]) -> None:
    colors = {
        "added": "updated",
        "replaced": "skip",
        "skipped": "unchanged",
    }
    color_key = colors.get(label, "reset")
    for key, values in changes.items():
        joined = ", ".join(values)
        _safe_print(f"  {_color_text(label, color_key)}: {key} = {joined}")
