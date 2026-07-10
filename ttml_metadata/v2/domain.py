from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence

from ttml_metadata.models import AudioMetadata

SourceKey = str


@dataclass(frozen=True)
class MatchEvidence:
    field: str
    relation: str
    expected: str | None = None
    actual: str | None = None


@dataclass(frozen=True)
class Candidate:
    id: str
    source: SourceKey
    title: str | None = None
    artists: tuple[str, ...] = ()
    album: str | None = None
    aliases: tuple[str, ...] = ()
    identifiers: Mapping[str, str] = field(default_factory=dict)
    group: str | None = None
    rank: int = 1
    recommended: bool = False
    evidence: tuple[MatchEvidence, ...] = ()
    duration_ms: int | None = None
    release_date: str | None = None


@dataclass(frozen=True)
class SourceResult:
    source: SourceKey
    candidates: tuple[Candidate, ...] = ()
    groups: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    recommended_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class MatchContext:
    metadata: AudioMetadata
    results: Mapping[SourceKey, SourceResult]


@dataclass(frozen=True)
class MatchResult:
    metadata: AudioMetadata
    sources: Mapping[SourceKey, SourceResult]


@dataclass(frozen=True)
class Selection:
    pair_id: str
    sources: Mapping[SourceKey, tuple[str, ...]]


class SourceAdapter(Protocol):
    key: SourceKey
    dependencies: frozenset[SourceKey]

    def search(self, context: MatchContext) -> SourceResult:
        ...

    def sort_candidates(
        self,
        candidates: Sequence[Candidate],
    ) -> tuple[Candidate, ...]:
        ...

    def recommended_candidate_ids(
        self,
        candidates: Sequence[Candidate],
    ) -> tuple[str, ...]:
        ...

    def match_evidence(self, candidate: Candidate) -> tuple[MatchEvidence, ...]:
        ...

    def metadata_values(
        self,
        metadata: AudioMetadata,
        result: SourceResult,
        selected_ids: Sequence[str],
    ) -> Mapping[str, Sequence[str]]:
        ...
