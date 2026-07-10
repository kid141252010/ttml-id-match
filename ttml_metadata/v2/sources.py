from __future__ import annotations

from collections.abc import Sequence

from ttml_metadata.models import (
    AppleMusicClientProtocol,
    AppleMusicTrackCandidate,
    AudioMetadata,
    NCMusicCandidate,
    NCMusicClientProtocol,
    QQMusicCandidate,
    QQMusicClientProtocol,
    SpotifyClientProtocol,
    SpotifyTrackCandidate,
)
from ttml_metadata.apple_music import _merge_apple_music_metadata, collect_apple_music_metadata
from ttml_metadata.ncm_music import _merge_ncm_music_metadata, collect_ncm_music_metadata
from ttml_metadata.qq_music import _merge_qq_music_metadata, collect_qq_music_metadata
from ttml_metadata.spotify import _merge_spotify_metadata, collect_spotify_metadata
from ttml_metadata.text_utils import (
    _duration_close,
    _normalize_release_date,
    _release_date_matches,
    _same_identifier,
    _same_raw_text,
    _text_match_score,
)

from .domain import Candidate, MatchContext, MatchEvidence, SourceResult


class RankedSourceAdapter:
    def sort_candidates(
        self,
        candidates: Sequence[Candidate],
    ) -> tuple[Candidate, ...]:
        return tuple(sorted(candidates, key=lambda candidate: (candidate.rank, candidate.id)))

    def recommended_candidate_ids(
        self,
        candidates: Sequence[Candidate],
    ) -> tuple[str, ...]:
        return tuple(candidate.id for candidate in candidates if candidate.recommended)

    def match_evidence(self, candidate: Candidate) -> tuple[MatchEvidence, ...]:
        return candidate.evidence


class QQMusicSourceAdapter(RankedSourceAdapter):
    key = "qq_music"
    dependencies = frozenset()

    def __init__(self, client: QQMusicClientProtocol):
        self._client = client

    def search(self, context: MatchContext) -> SourceResult:
        result = collect_qq_music_metadata(context.metadata, self._client)
        candidates = self.sort_candidates(tuple(
            _qq_candidate(context.metadata, candidate, rank=index + 1, recommended=index == 0)
            for index, candidate in enumerate(result.candidates)
        ))
        recommended_ids = self.recommended_candidate_ids(candidates)
        return SourceResult(
            source=self.key,
            candidates=candidates,
            groups={"default": tuple(candidate.id for candidate in candidates)},
            recommended_ids=recommended_ids,
            warnings=tuple(result.errors),
        )

    def metadata_values(
        self,
        metadata: AudioMetadata,
        result: SourceResult,
        selected_ids: Sequence[str],
    ) -> dict[str, list[str]]:
        selected = set(selected_ids)
        values: dict[str, list[str]] = {}
        for candidate in result.candidates:
            if candidate.id not in selected:
                continue
            _merge_qq_music_metadata(values, metadata, _qq_domain_candidate(candidate))
        return values


class NCMusicSourceAdapter(RankedSourceAdapter):
    key = "ncm_music"
    dependencies = frozenset({"qq_music"})

    def __init__(self, client: NCMusicClientProtocol):
        self._client = client

    def search(self, context: MatchContext) -> SourceResult:
        qq_candidate = _recommended_qq_candidate(context.results.get("qq_music"))
        result = collect_ncm_music_metadata(
            context.metadata,
            self._client,
            qq_music_candidate=qq_candidate,
        )
        candidates = self.sort_candidates(tuple(
            _ncm_candidate(context.metadata, candidate, rank=index + 1, recommended=index == 0)
            for index, candidate in enumerate(result.candidates)
        ))
        recommended_ids = self.recommended_candidate_ids(candidates)
        return SourceResult(
            source=self.key,
            candidates=candidates,
            groups={"default": tuple(candidate.id for candidate in candidates)},
            recommended_ids=recommended_ids,
            warnings=tuple(result.errors),
        )

    def metadata_values(
        self,
        metadata: AudioMetadata,
        result: SourceResult,
        selected_ids: Sequence[str],
    ) -> dict[str, list[str]]:
        selected = set(selected_ids)
        values: dict[str, list[str]] = {}
        for candidate in result.candidates:
            if candidate.id not in selected:
                continue
            _merge_ncm_music_metadata(values, metadata, _ncm_domain_candidate(candidate))
        return values


class AppleMusicSourceAdapter(RankedSourceAdapter):
    key = "apple_music"
    dependencies = frozenset()

    def __init__(self, client: AppleMusicClientProtocol, *, storefront_workers: int = 1):
        self._client = client
        self._storefront_workers = storefront_workers

    def search(self, context: MatchContext) -> SourceResult:
        result = collect_apple_music_metadata(
            context.metadata,
            self._client,
            max_workers=self._storefront_workers,
        )
        selected = {(candidate.storefront, candidate.track_id) for candidate in result.selected}
        domain_candidates: list[Candidate] = []
        groups: dict[str, tuple[str, ...]] = {}
        for storefront, candidates in result.candidates_by_storefront.items():
            group_ids: list[str] = []
            for provider_candidate in candidates:
                candidate_id = _grouped_candidate_id(storefront, provider_candidate.track_id)
                group_ids.append(candidate_id)
                domain_candidates.append(
                    _apple_candidate(
                        context.metadata,
                        provider_candidate,
                        candidate_id=candidate_id,
                        rank=len(domain_candidates) + 1,
                        recommended=(storefront, provider_candidate.track_id) in selected,
                    )
                )
            groups[storefront] = tuple(group_ids)
        candidates = self.sort_candidates(domain_candidates)
        recommended_ids = self.recommended_candidate_ids(candidates)
        return SourceResult(
            source=self.key,
            candidates=candidates,
            groups=groups,
            recommended_ids=recommended_ids,
            warnings=tuple(result.errors),
        )

    def metadata_values(
        self,
        metadata: AudioMetadata,
        result: SourceResult,
        selected_ids: Sequence[str],
    ) -> dict[str, list[str]]:
        selected = set(selected_ids)
        values: dict[str, list[str]] = {}
        for candidate in result.candidates:
            if candidate.id in selected:
                _merge_apple_music_metadata(values, metadata, _apple_domain_candidate(candidate))
        return values


class SpotifySourceAdapter(RankedSourceAdapter):
    key = "spotify"
    dependencies = frozenset()

    def __init__(self, client: SpotifyClientProtocol | None):
        self._client = client

    def search(self, context: MatchContext) -> SourceResult:
        result = collect_spotify_metadata(context.metadata, self._client)
        selected = {(candidate.market, candidate.track_id) for candidate in result.selected}
        domain_candidates: list[Candidate] = []
        groups: dict[str, tuple[str, ...]] = {}
        for market, candidates in result.candidates_by_market.items():
            group_ids: list[str] = []
            for provider_candidate in candidates:
                candidate_id = _grouped_candidate_id(market, provider_candidate.track_id)
                group_ids.append(candidate_id)
                domain_candidates.append(
                    _spotify_candidate(
                        context.metadata,
                        provider_candidate,
                        candidate_id=candidate_id,
                        rank=len(domain_candidates) + 1,
                        recommended=(market, provider_candidate.track_id) in selected,
                    )
                )
            groups[market] = tuple(group_ids)
        candidates = self.sort_candidates(domain_candidates)
        recommended_ids = self.recommended_candidate_ids(candidates)
        return SourceResult(
            source=self.key,
            candidates=candidates,
            groups=groups,
            recommended_ids=recommended_ids,
            warnings=tuple(result.errors),
        )

    def metadata_values(
        self,
        metadata: AudioMetadata,
        result: SourceResult,
        selected_ids: Sequence[str],
    ) -> dict[str, list[str]]:
        selected = set(selected_ids)
        values: dict[str, list[str]] = {}
        for candidate in result.candidates:
            if candidate.id in selected:
                _merge_spotify_metadata(values, metadata, _spotify_domain_candidate(candidate))
        return values


def _qq_candidate(
    metadata: AudioMetadata,
    candidate: QQMusicCandidate,
    *,
    rank: int,
    recommended: bool,
) -> Candidate:
    return Candidate(
        id=candidate.song_id,
        source="qq_music",
        title=candidate.title,
        artists=tuple(candidate.artists),
        album=candidate.album,
        aliases=(candidate.subtitle,) if candidate.subtitle else (),
        identifiers={"song_id": candidate.song_id, "mid": candidate.mid},
        group="default",
        rank=rank,
        recommended=recommended,
        evidence=_candidate_evidence(metadata, candidate.title, candidate.artists, candidate.album),
    )


def _qq_domain_candidate(candidate: Candidate) -> QQMusicCandidate:
    return QQMusicCandidate(
        song_id=candidate.identifiers.get("song_id", candidate.id),
        mid=candidate.identifiers.get("mid", ""),
        title=candidate.title,
        subtitle=candidate.aliases[0] if candidate.aliases else None,
        artists=list(candidate.artists),
        album=candidate.album,
        source_index=max(0, candidate.rank - 1),
    )


def _recommended_qq_candidate(result: SourceResult | None) -> QQMusicCandidate | None:
    if result is None or not result.recommended_ids:
        return None
    wanted = set(result.recommended_ids)
    for candidate in result.candidates:
        if candidate.id in wanted:
            return _qq_domain_candidate(candidate)
    return None


def _ncm_candidate(
    metadata: AudioMetadata,
    candidate: NCMusicCandidate,
    *,
    rank: int,
    recommended: bool,
) -> Candidate:
    return Candidate(
        id=candidate.song_id,
        source="ncm_music",
        title=candidate.title,
        artists=tuple(candidate.artists),
        album=candidate.album,
        aliases=tuple(candidate.aliases),
        identifiers={"song_id": candidate.song_id},
        group="default",
        rank=rank,
        recommended=recommended,
        evidence=_candidate_evidence(metadata, candidate.title, candidate.artists, candidate.album),
    )


def _ncm_domain_candidate(candidate: Candidate) -> NCMusicCandidate:
    return NCMusicCandidate(
        song_id=candidate.identifiers.get("song_id", candidate.id),
        title=candidate.title,
        aliases=list(candidate.aliases),
        artists=list(candidate.artists),
        album=candidate.album,
        source_index=max(0, candidate.rank - 1),
    )


def _grouped_candidate_id(group: str, native_id: str) -> str:
    return f"{group}:{native_id}"


def _apple_candidate(
    metadata: AudioMetadata,
    candidate: AppleMusicTrackCandidate,
    *,
    candidate_id: str,
    rank: int,
    recommended: bool,
) -> Candidate:
    return Candidate(
        id=candidate_id,
        source="apple_music",
        title=candidate.title,
        artists=tuple(candidate.artists),
        album=candidate.album,
        identifiers={"track_id": candidate.track_id, "storefront": candidate.storefront, **({"isrc": candidate.isrc} if candidate.isrc else {})},
        group=candidate.storefront,
        rank=rank,
        recommended=recommended,
        evidence=_candidate_evidence(
            metadata,
            candidate.title,
            candidate.artists,
            candidate.album,
            isrc=candidate.isrc,
            duration_ms=candidate.duration_ms,
            release_date=candidate.release_date,
        ),
        duration_ms=candidate.duration_ms,
        release_date=candidate.release_date,
    )


def _apple_domain_candidate(candidate: Candidate) -> AppleMusicTrackCandidate:
    return AppleMusicTrackCandidate(
        track_id=candidate.identifiers.get("track_id", candidate.id),
        title=candidate.title,
        artists=list(candidate.artists),
        album=candidate.album,
        storefront=candidate.identifiers.get("storefront", candidate.group or ""),
        source_index=max(0, candidate.rank - 1),
        isrc=candidate.identifiers.get("isrc"),
        release_date=candidate.release_date,
        duration_ms=candidate.duration_ms,
    )


def _spotify_candidate(
    metadata: AudioMetadata,
    candidate: SpotifyTrackCandidate,
    *,
    candidate_id: str,
    rank: int,
    recommended: bool,
) -> Candidate:
    return Candidate(
        id=candidate_id,
        source="spotify",
        title=candidate.title,
        artists=tuple(candidate.artists),
        album=candidate.album,
        identifiers={"track_id": candidate.track_id, "market": candidate.market, **({"isrc": candidate.isrc} if candidate.isrc else {})},
        group=candidate.market,
        rank=rank,
        recommended=recommended,
        evidence=_candidate_evidence(
            metadata,
            candidate.title,
            candidate.artists,
            candidate.album,
            isrc=candidate.isrc,
            duration_ms=candidate.duration_ms,
            release_date=candidate.release_date,
        ),
        duration_ms=candidate.duration_ms,
        release_date=candidate.release_date,
    )


def _spotify_domain_candidate(candidate: Candidate) -> SpotifyTrackCandidate:
    return SpotifyTrackCandidate(
        track_id=candidate.identifiers.get("track_id", candidate.id),
        title=candidate.title,
        artists=list(candidate.artists),
        album=candidate.album,
        market=candidate.identifiers.get("market", candidate.group or ""),
        source_index=max(0, candidate.rank - 1),
        isrc=candidate.identifiers.get("isrc"),
        duration_ms=candidate.duration_ms,
        release_date=candidate.release_date,
    )


def _candidate_evidence(
    metadata: AudioMetadata,
    title: str | None,
    artists: Sequence[str],
    album: str | None,
    *,
    isrc: str | None = None,
    duration_ms: int | None = None,
    release_date: str | None = None,
) -> tuple[MatchEvidence, ...]:
    evidence = [_text_evidence("title", metadata.title, title)]
    if metadata.artists:
        best_artist = max(
            ((expected, actual) for expected in metadata.artists for actual in artists),
            key=lambda pair: _text_match_score(pair[0], pair[1]),
            default=(metadata.artists[0], None),
        )
        evidence.append(_text_evidence("artist", best_artist[0], best_artist[1]))
    if metadata.album:
        evidence.append(_text_evidence("album", metadata.album, album))
    if metadata.isrc or isrc:
        evidence.append(_identifier_evidence("isrc", metadata.isrc, isrc))
    if metadata.duration_seconds is not None or duration_ms is not None:
        evidence.append(_duration_evidence(metadata.duration_seconds, duration_ms))
    if metadata.release_date or release_date:
        evidence.append(_date_evidence(metadata.release_date, release_date))
    return tuple(evidence)


def _text_evidence(field: str, expected: str | None, actual: str | None) -> MatchEvidence:
    if not expected:
        return MatchEvidence(field=field, relation="not_provided", actual=actual)
    if not actual:
        return MatchEvidence(field=field, relation="missing", expected=expected)
    if _same_raw_text(expected, actual):
        relation = "exact"
    else:
        score = _text_match_score(expected, actual)
        relation = "normalized" if score == 2 else "partial" if score == 1 else "conflict"
    return MatchEvidence(field=field, relation=relation, expected=expected, actual=actual)


def _identifier_evidence(
    field: str,
    expected: str | None,
    actual: str | None,
) -> MatchEvidence:
    if not expected:
        return MatchEvidence(field=field, relation="not_provided", actual=actual)
    if not actual:
        return MatchEvidence(field=field, relation="missing", expected=expected)
    return MatchEvidence(
        field=field,
        relation="exact" if _same_identifier(expected, actual) else "conflict",
        expected=expected,
        actual=actual,
    )


def _duration_evidence(
    expected_seconds: float | None,
    actual_ms: int | None,
) -> MatchEvidence:
    expected = str(round(expected_seconds * 1000)) if expected_seconds is not None else None
    actual = str(actual_ms) if actual_ms is not None else None
    if expected_seconds is None:
        return MatchEvidence(field="duration", relation="not_provided", actual=actual)
    if actual_ms is None:
        return MatchEvidence(field="duration", relation="missing", expected=expected)
    return MatchEvidence(
        field="duration",
        relation="close" if _duration_close(expected_seconds, actual_ms) else "conflict",
        expected=expected,
        actual=actual,
    )


def _date_evidence(
    expected_value: str | None,
    actual_value: str | None,
) -> MatchEvidence:
    expected = _normalize_release_date(expected_value)
    actual = _normalize_release_date(actual_value)
    if not expected:
        return MatchEvidence(field="release_date", relation="not_provided", actual=actual)
    if not actual:
        return MatchEvidence(field="release_date", relation="missing", expected=expected)
    relation = (
        "exact"
        if expected == actual
        else "compatible"
        if _release_date_matches(expected, actual)
        else "conflict"
    )
    return MatchEvidence(
        field="release_date",
        relation=relation,
        expected=expected,
        actual=actual,
    )
