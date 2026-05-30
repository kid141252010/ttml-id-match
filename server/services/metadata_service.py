from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Iterable

from server.models.schemas import (
    AppliedFile,
    ApplySummary,
    Candidate,
    ChangeSet,
    FilePair,
    PreviewResponse,
    PreviewResult,
    SelectionPayload,
    SourcePreview,
)
from server.services.file_service import copy_uploads_to_outputs, pair_session_files
from server.services.session_manager import SessionManager, SessionState
from ttml_metadata.apple_music import AppleMusicClient, _apple_music_storefront_top_candidates, _sync_apple_music_result_values
from ttml_metadata.ncm_music import NCMusicClient, collect_ncm_music_metadata
from ttml_metadata.orchestration import _prepare_work_item, values_from_metadata
from ttml_metadata.qq_music import QQMusicClient
from ttml_metadata.spotify import SpotifyClient, load_spotify_credentials
from ttml_metadata.ttml import normalize_ttml_language, update_ttml_metadata
from ttml_metadata.models import (
    AppleMusicClientProtocol,
    AppleMusicTrackCandidate,
    NCMusicCandidate,
    NCMusicClientProtocol,
    PairMetadata,
    QQMusicCandidate,
    QQMusicClientProtocol,
    SpotifyClientProtocol,
    SpotifyTrackCandidate,
    WorkItem,
)


class MetadataService:
    def __init__(
        self,
        session_manager: SessionManager,
        apple_music_client: AppleMusicClientProtocol | None = None,
        qq_music_client: QQMusicClientProtocol | None = None,
        ncm_music_client: NCMusicClientProtocol | None = None,
        spotify_client: SpotifyClientProtocol | None = None,
        search_workers: int = 3,
    ):
        self.session_manager = session_manager
        self.apple_music_client = apple_music_client or AppleMusicClient()
        self.qq_music_client = qq_music_client or QQMusicClient()
        self.ncm_music_client = ncm_music_client or NCMusicClient()
        if spotify_client is not None:
            self.spotify_client = spotify_client
        else:
            credentials = load_spotify_credentials()
            self.spotify_client = SpotifyClient(credentials) if credentials.enabled else None
        self.search_workers = max(1, search_workers)

    def preview(self, state: SessionState) -> PreviewResponse:
        state.pairs = [pair.model_dump() for pair in pair_session_files(state.upload_dir)]
        state.prepared_pairs = {}
        state.previews = {}
        results: list[PreviewResult] = []
        for pair_model in [FilePair(**pair) for pair in state.pairs]:
            if not pair_model.ttml:
                continue
            work_item = WorkItem(
                ttml_path=state.upload_dir / pair_model.ttml,
                audio_path=(state.upload_dir / pair_model.audio) if pair_model.audio else None,
            )
            prepared = _prepare_work_item(
                work_item,
                self.apple_music_client,
                self.qq_music_client,
                self.ncm_music_client,
                self.spotify_client,
            )
            _select_preview_defaults(prepared)
            prepared.ncm_music_metadata = collect_ncm_music_metadata(
                prepared.metadata,
                self.ncm_music_client,
                qq_music_candidate=prepared.qq_music_metadata.selected,
            )
            if prepared.ncm_music_metadata.candidates:
                prepared.ncm_music_metadata.selected = prepared.ncm_music_metadata.candidates[0]
            preview = pair_to_preview(pair_model, prepared)
            state.prepared_pairs[pair_model.id] = prepared
            state.previews[pair_model.id] = preview
            results.append(preview)
        return PreviewResponse(results=results)

    def apply(self, state: SessionState, selections: list[SelectionPayload]) -> ApplySummary:
        if not state.prepared_pairs:
            self.preview(state)
        selection_by_pair = {selection.pair_id: selection for selection in selections}
        copy_uploads_to_outputs(state.upload_dir, state.output_dir)
        files: list[AppliedFile] = []
        backup_paths: dict[Path, Path] = {}
        for pair_id, prepared in state.prepared_pairs.items():
            output_ttml = state.output_dir / prepared.ttml_path.name
            output_audio = state.output_dir / prepared.audio_path.name if prepared.audio_path else None
            output_pair = PairMetadata(
                output_audio,
                output_ttml,
                prepared.metadata,
                prepared.apple_music_metadata,
                prepared.qq_music_metadata,
                prepared.ncm_music_metadata,
                prepared.spotify_metadata,
            )
            selection = selection_by_pair.get(pair_id)
            if selection is not None:
                apply_selection(output_pair, selection)
            try:
                normalize_ttml_language(output_ttml, dry_run=False, backup_paths=backup_paths)
                values = values_from_metadata(
                    output_pair.metadata,
                    output_pair.apple_music_metadata.values,
                    apple_music_candidates=output_pair.apple_music_metadata.selected,
                    qq_music_candidate=output_pair.qq_music_metadata.selected,
                    ncm_music_candidate=output_pair.ncm_music_metadata.selected,
                    spotify_candidates=output_pair.spotify_metadata.selected,
                )
                result = update_ttml_metadata(output_ttml, values, dry_run=False, backup_paths=backup_paths)
                files.append(
                    AppliedFile(
                        pair_id=pair_id,
                        ttml=output_ttml.name,
                        status="success",
                        metadata_written=sorted(set(result.added) | set(result.replaced)),
                    )
                )
            except Exception as exc:
                files.append(
                    AppliedFile(
                        pair_id=pair_id,
                        ttml=output_ttml.name,
                        status="failed",
                        metadata_written=[],
                        error=str(exc),
                    )
                )
        succeeded = sum(1 for item in files if item.status == "success")
        failed = sum(1 for item in files if item.status == "failed")
        skipped = sum(1 for item in files if item.status == "skipped")
        return ApplySummary(succeeded=succeeded, failed=failed, skipped=skipped, files=files)

    def zip_outputs(self, state: SessionState) -> Path:
        zip_path = state.root / "ttml-results.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(state.output_dir.glob("*.ttml"), key=lambda item: item.name.lower()):
                archive.write(path, arcname=path.name)
        return zip_path


def pair_to_preview(pair: FilePair, prepared: PairMetadata) -> PreviewResult:
    values = values_from_metadata(
        prepared.metadata,
        prepared.apple_music_metadata.values,
        apple_music_candidates=prepared.apple_music_metadata.selected,
        qq_music_candidate=prepared.qq_music_metadata.selected,
        ncm_music_candidate=prepared.ncm_music_metadata.selected,
        spotify_candidates=prepared.spotify_metadata.selected,
    )
    result = update_ttml_metadata(prepared.ttml_path, values, dry_run=True)
    return PreviewResult(
        pair_id=pair.id,
        ttml=pair.ttml or prepared.ttml_path.name,
        audio=pair.audio,
        apple_music=apple_music_preview(prepared),
        qq_music=qq_music_preview(prepared),
        ncm_music=ncm_music_preview(prepared),
        spotify=spotify_preview(prepared),
        changes=ChangeSet(added=result.added, replaced=result.replaced, skipped=result.skipped),
    )


def _select_preview_defaults(pair: PairMetadata) -> None:
    if pair.apple_music_metadata.candidates:
        pair.apple_music_metadata.selected = _apple_music_storefront_top_candidates(pair.apple_music_metadata)
        _sync_apple_music_result_values(pair.apple_music_metadata, pair.metadata)
    if pair.qq_music_metadata.candidates:
        pair.qq_music_metadata.selected = pair.qq_music_metadata.candidates[0]
    if pair.spotify_metadata.candidates:
        pair.spotify_metadata.selected = _first_by_region(pair.spotify_metadata.candidates, "market")


def apply_selection(pair: PairMetadata, selection: SelectionPayload) -> None:
    pair.apple_music_metadata.selected = [
        candidate for candidate in pair.apple_music_metadata.candidates if candidate.track_id in set(selection.apple_music)
    ]
    _sync_apple_music_result_values(pair.apple_music_metadata, pair.metadata)
    pair.qq_music_metadata.selected = _first_candidate(pair.qq_music_metadata.candidates, selection.qq_music, lambda item: item.song_id)
    pair.ncm_music_metadata.selected = _first_candidate(pair.ncm_music_metadata.candidates, selection.ncm_music, lambda item: item.song_id)
    pair.spotify_metadata.selected = [
        candidate for candidate in pair.spotify_metadata.candidates if candidate.track_id in set(selection.spotify)
    ]


def apple_music_preview(pair: PairMetadata) -> SourcePreview:
    return SourcePreview(
        best=[apple_candidate(candidate) for candidate in _apple_music_storefront_top_candidates(pair.apple_music_metadata)],
        candidates=[apple_candidate(candidate) for candidate in pair.apple_music_metadata.candidates],
        candidates_by_storefront={
            store: [apple_candidate(candidate) for candidate in candidates]
            for store, candidates in pair.apple_music_metadata.candidates_by_storefront.items()
        },
        errors=list(pair.apple_music_metadata.errors),
    )


def qq_music_preview(pair: PairMetadata) -> SourcePreview:
    candidates = [qq_candidate(candidate) for candidate in pair.qq_music_metadata.candidates]
    return SourcePreview(best=candidates[:1], candidates=candidates, errors=list(pair.qq_music_metadata.errors))


def ncm_music_preview(pair: PairMetadata) -> SourcePreview:
    candidates = [ncm_candidate(candidate) for candidate in pair.ncm_music_metadata.candidates]
    return SourcePreview(best=candidates[:1], candidates=candidates, errors=list(pair.ncm_music_metadata.errors))


def spotify_preview(pair: PairMetadata) -> SourcePreview:
    grouped: dict[str, list[Candidate]] = {}
    for candidate in pair.spotify_metadata.candidates:
        if candidate.market:
            grouped.setdefault(candidate.market, []).append(spotify_candidate(candidate))
    return SourcePreview(
        best=[spotify_candidate(candidate) for candidate in _first_by_region(pair.spotify_metadata.candidates, "market")],
        candidates=[spotify_candidate(candidate) for candidate in pair.spotify_metadata.candidates],
        candidates_by_market=grouped,
        errors=list(pair.spotify_metadata.errors),
    )


def apple_candidate(candidate: AppleMusicTrackCandidate) -> Candidate:
    return Candidate(
        id=candidate.track_id,
        title=candidate.title,
        artists=list(candidate.artists),
        album=candidate.album,
        region=candidate.storefront,
        isrc=candidate.isrc,
        duration_ms=candidate.duration_ms,
        release_date=candidate.release_date,
        score=max(0, 100 - candidate.source_index * 5),
        source="apple_music",
    )


def qq_candidate(candidate: QQMusicCandidate) -> Candidate:
    return Candidate(
        id=candidate.song_id,
        title=candidate.title,
        artists=list(candidate.artists),
        album=candidate.album,
        score=max(0, 96 - candidate.source_index * 5),
        source="qq_music",
    )


def ncm_candidate(candidate: NCMusicCandidate) -> Candidate:
    return Candidate(
        id=candidate.song_id,
        title=candidate.title,
        artists=list(candidate.artists),
        album=candidate.album,
        score=max(0, 92 - candidate.source_index * 5),
        source="ncm_music",
    )


def spotify_candidate(candidate: SpotifyTrackCandidate) -> Candidate:
    return Candidate(
        id=candidate.track_id,
        title=candidate.title,
        artists=list(candidate.artists),
        album=candidate.album,
        region=candidate.market,
        isrc=candidate.isrc,
        duration_ms=candidate.duration_ms,
        release_date=candidate.release_date,
        score=max(0, 95 - candidate.source_index * 5),
        source="spotify",
    )


def _first_candidate(candidates: Iterable, ids: list[str], get_id):
    wanted = set(ids)
    for candidate in candidates:
        if get_id(candidate) in wanted:
            return candidate
    return None


def _first_by_region(candidates: Iterable, region_attr: str):
    selected = []
    seen = set()
    for candidate in candidates:
        region = getattr(candidate, region_attr, "")
        if region in seen:
            continue
        seen.add(region)
        selected.append(candidate)
    return selected
