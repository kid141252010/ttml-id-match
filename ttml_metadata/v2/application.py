from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ttml_metadata.audio import read_audio_metadata
from ttml_metadata.models import AudioMetadata
from ttml_metadata.ttml import read_ttml_metadata

from .domain import (
    Candidate,
    MatchEvidence,
    MatchResult,
    Selection,
    SourceResult,
)
from .engine import MatchingEngine, UnknownCandidateError
from .pairing import PairingPair
from .ttml_plan import (
    ChangePlan,
    LanguageNormalizationSummary,
    MetadataChangeSummary,
    TtmlInputChangedError,
    TtmlPlanner,
)


@dataclass(frozen=True)
class ChangePlanSummary:
    input_sha256: str
    output_sha256: str
    final_text: str
    changed: bool
    metadata: MetadataChangeSummary
    normalization: LanguageNormalizationSummary

    @classmethod
    def from_plan(cls, plan: ChangePlan) -> ChangePlanSummary:
        return cls(
            input_sha256=plan.input_sha256,
            output_sha256=plan.output_sha256,
            final_text=plan.final_text,
            changed=plan.changed,
            metadata=plan.metadata,
            normalization=plan.language,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "input_sha256": self.input_sha256,
            "output_sha256": self.output_sha256,
            "final_text": self.final_text,
            "changed": self.changed,
            "metadata": {
                "added": _string_lists(self.metadata.added),
                "replaced": _string_lists(self.metadata.replaced),
                "skipped": _string_lists(self.metadata.skipped),
                "changed": self.metadata.changed,
            },
            "normalization": {
                "language_changed": self.normalization.language_changed,
                "body_text_changed": self.normalization.body_text_changed,
                "removed_translations": self.normalization.removed_translations,
                "removed_transliterations": self.normalization.removed_transliterations,
                "changed": self.normalization.changed,
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ChangePlanSummary:
        metadata = data["metadata"]
        normalization = data["normalization"]
        return cls(
            input_sha256=str(data["input_sha256"]),
            output_sha256=str(data["output_sha256"]),
            final_text=str(data["final_text"]),
            changed=bool(data["changed"]),
            metadata=MetadataChangeSummary(
                added=_string_lists(metadata.get("added", {})),
                replaced=_string_lists(metadata.get("replaced", {})),
                skipped=_string_lists(metadata.get("skipped", {})),
            ),
            normalization=LanguageNormalizationSummary(
                language_changed=bool(normalization.get("language_changed", False)),
                body_text_changed=bool(normalization.get("body_text_changed", False)),
                removed_translations=int(normalization.get("removed_translations", 0)),
                removed_transliterations=int(normalization.get("removed_transliterations", 0)),
            ),
        )


@dataclass(frozen=True)
class PairSnapshot:
    pair_id: str
    ttml_filename: str
    ttml_sha256: str
    audio_filename: str | None
    audio_sha256: str | None
    metadata: AudioMetadata
    match_result: MatchResult
    default_selection: Selection
    baseline_change_plan: ChangePlanSummary
    base_metadata_values: Mapping[str, tuple[str, ...]]
    candidate_metadata_values: Mapping[
        str,
        Mapping[str, Mapping[str, tuple[str, ...]]],
    ]

    def to_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "pair_id": self.pair_id,
            "ttml_filename": self.ttml_filename,
            "ttml_sha256": self.ttml_sha256,
            "audio_filename": self.audio_filename,
            "audio_sha256": self.audio_sha256,
            "metadata": _audio_metadata_to_dict(self.metadata),
            "match_result": _match_result_to_dict(self.match_result),
            "default_selection": _selection_to_dict(self.default_selection),
            "baseline_change_plan": self.baseline_change_plan.to_dict(),
            "base_metadata_values": {
                key: list(values) for key, values in self.base_metadata_values.items()
            },
            "candidate_metadata_values": {
                source: {
                    candidate_id: {
                        key: list(values) for key, values in contribution.items()
                    }
                    for candidate_id, contribution in candidates.items()
                }
                for source, candidates in self.candidate_metadata_values.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PairSnapshot:
        version = int(data.get("version", 1))
        if version != 1:
            raise ValueError(f"unsupported pair snapshot version: {version}")
        return cls(
            pair_id=str(data["pair_id"]),
            ttml_filename=str(data["ttml_filename"]),
            ttml_sha256=str(data["ttml_sha256"]),
            audio_filename=(
                str(data["audio_filename"])
                if data.get("audio_filename") is not None
                else None
            ),
            audio_sha256=(
                str(data["audio_sha256"])
                if data.get("audio_sha256") is not None
                else None
            ),
            metadata=_audio_metadata_from_dict(data["metadata"]),
            match_result=_match_result_from_dict(data["match_result"]),
            default_selection=_selection_from_dict(data["default_selection"]),
            baseline_change_plan=ChangePlanSummary.from_dict(data["baseline_change_plan"]),
            base_metadata_values=_tuple_string_lists(data["base_metadata_values"]),
            candidate_metadata_values={
                str(source): {
                    str(candidate_id): _tuple_string_lists(contribution)
                    for candidate_id, contribution in candidates.items()
                }
                for source, candidates in data["candidate_metadata_values"].items()
            },
        )


@dataclass(frozen=True)
class PairPreviewFailure:
    pair_id: str
    ttml_filename: str
    message: str


@dataclass(frozen=True)
class PreviewBatch:
    snapshots: tuple[PairSnapshot, ...]
    failures: tuple[PairPreviewFailure, ...]


class MatchingApplication:
    def __init__(
        self,
        engine: MatchingEngine,
        planner: TtmlPlanner | None = None,
    ) -> None:
        self._engine = engine
        self._planner = planner or TtmlPlanner()

    def preview_pair(self, pair: PairingPair) -> PairSnapshot:
        ttml_bytes, audio_bytes, metadata = self._read_pair(pair)
        match_result = self._engine.match(metadata)
        return self._build_snapshot(
            pair,
            ttml_bytes,
            audio_bytes,
            metadata,
            match_result,
        )

    def preview_pairs(self, pairs: Sequence[PairingPair]) -> PreviewBatch:
        prepared: list[tuple[PairingPair, bytes, bytes | None, AudioMetadata]] = []
        failures: list[PairPreviewFailure] = []
        for pair in pairs:
            try:
                ttml_bytes, audio_bytes, metadata = self._read_pair(pair)
            except Exception as exc:
                failures.append(
                    PairPreviewFailure(pair.pair_id, pair.ttml_path.name, str(exc))
                )
                continue
            prepared.append((pair, ttml_bytes, audio_bytes, metadata))

        results = self._engine.match_many(item[3] for item in prepared)
        snapshots: list[PairSnapshot] = []
        for item, result in zip(prepared, results):
            pair, ttml_bytes, audio_bytes, metadata = item
            try:
                snapshots.append(
                    self._build_snapshot(
                        pair,
                        ttml_bytes,
                        audio_bytes,
                        metadata,
                        result,
                    )
                )
            except Exception as exc:
                failures.append(
                    PairPreviewFailure(pair.pair_id, pair.ttml_path.name, str(exc))
                )
        return PreviewBatch(tuple(snapshots), tuple(failures))

    def _read_pair(
        self,
        pair: PairingPair,
    ) -> tuple[bytes, bytes | None, AudioMetadata]:
        if pair.status == "ambiguous":
            raise ValueError(f"cannot preview ambiguous pair: {pair.pair_id}")
        if pair.status == "paired" and pair.audio_path is None:
            raise ValueError(f"paired item has no audio file: {pair.pair_id}")

        ttml_bytes = pair.ttml_path.read_bytes()
        audio_bytes = pair.audio_path.read_bytes() if pair.audio_path is not None else None
        metadata = (
            read_audio_metadata(pair.audio_path)
            if pair.audio_path is not None
            else read_ttml_metadata(pair.ttml_path)
        )
        return ttml_bytes, audio_bytes, metadata

    def _build_snapshot(
        self,
        pair: PairingPair,
        ttml_bytes: bytes,
        audio_bytes: bytes | None,
        metadata: AudioMetadata,
        match_result: MatchResult,
    ) -> PairSnapshot:
        default_selection = self._engine.default_selection(pair.pair_id, match_result)
        base_values = self._engine.metadata_values(
            match_result,
            Selection(pair_id=pair.pair_id, sources={}),
        )
        candidate_values = self._candidate_contributions(pair.pair_id, match_result, base_values)
        baseline_values = _selected_metadata_values(
            pair.pair_id,
            match_result,
            default_selection,
            base_values,
            candidate_values,
        )
        baseline_plan = self._planner.plan(pair.ttml_path, baseline_values)

        return PairSnapshot(
            pair_id=pair.pair_id,
            ttml_filename=pair.ttml_path.name,
            ttml_sha256=_sha256(ttml_bytes),
            audio_filename=pair.audio_path.name if pair.audio_path is not None else None,
            audio_sha256=_sha256(audio_bytes) if audio_bytes is not None else None,
            metadata=metadata,
            match_result=match_result,
            default_selection=default_selection,
            baseline_change_plan=ChangePlanSummary.from_plan(baseline_plan),
            base_metadata_values={key: tuple(values) for key, values in base_values.items()},
            candidate_metadata_values=candidate_values,
        )

    def plan_selection(
        self,
        snapshot: PairSnapshot,
        selection: Selection,
        ttml_path: Path,
    ) -> ChangePlan:
        current_sha256 = _sha256(ttml_path.read_bytes())
        if current_sha256 != snapshot.ttml_sha256:
            raise TtmlInputChangedError(
                "TTML input changed after preview: "
                f"expected {snapshot.ttml_sha256}, found {current_sha256}"
            )
        values = _selected_metadata_values(
            snapshot.pair_id,
            snapshot.match_result,
            selection,
            snapshot.base_metadata_values,
            snapshot.candidate_metadata_values,
        )
        return self._planner.plan(ttml_path, values)

    def _candidate_contributions(
        self,
        pair_id: str,
        result: MatchResult,
        base_values: Mapping[str, Sequence[str]],
    ) -> dict[str, dict[str, dict[str, tuple[str, ...]]]]:
        contributions: dict[str, dict[str, dict[str, tuple[str, ...]]]] = {}
        for source_key, source_result in result.sources.items():
            source_contributions: dict[str, dict[str, tuple[str, ...]]] = {}
            for candidate in source_result.candidates:
                values = self._engine.metadata_values(
                    result,
                    Selection(pair_id=pair_id, sources={source_key: (candidate.id,)}),
                )
                source_contributions[candidate.id] = _subtract_base_values(values, base_values)
            contributions[source_key] = source_contributions
        return contributions


def _selected_metadata_values(
    pair_id: str,
    result: MatchResult,
    selection: Selection,
    base_values: Mapping[str, Sequence[str]],
    candidate_values: Mapping[str, Mapping[str, Mapping[str, Sequence[str]]]],
) -> dict[str, list[str]]:
    if selection.pair_id != pair_id:
        raise ValueError(
            f"selection pair {selection.pair_id} does not match snapshot pair {pair_id}"
        )

    unknown_sources = set(selection.sources) - set(result.sources)
    if unknown_sources:
        raise UnknownCandidateError(f"unknown source: {sorted(unknown_sources)[0]}")

    selected_by_source = {
        source: set(selected_ids) for source, selected_ids in selection.sources.items()
    }
    values = {key: list(items) for key, items in base_values.items()}
    for source_key, source_result in result.sources.items():
        selected_ids = selected_by_source.get(source_key, set())
        known_ids = {candidate.id for candidate in source_result.candidates}
        unknown_ids = selected_ids - known_ids
        if unknown_ids:
            raise UnknownCandidateError(
                f"unknown candidates for {source_key}: {', '.join(sorted(unknown_ids))}"
            )
        contributions = candidate_values.get(source_key, {})
        for candidate in source_result.candidates:
            if candidate.id not in selected_ids:
                continue
            contribution = contributions.get(candidate.id)
            if contribution is None:
                raise ValueError(
                    f"snapshot is missing metadata contribution for {source_key}:{candidate.id}"
                )
            _merge_values(values, contribution)
    return values


def _subtract_base_values(
    values: Mapping[str, Sequence[str]],
    base_values: Mapping[str, Sequence[str]],
) -> dict[str, tuple[str, ...]]:
    contribution: dict[str, tuple[str, ...]] = {}
    for key, proposed in values.items():
        base = set(base_values.get(key, ()))
        additional = tuple(value for value in proposed if value not in base)
        if additional:
            contribution[key] = additional
    return contribution


def _merge_values(
    destination: dict[str, list[str]],
    contribution: Mapping[str, Sequence[str]],
) -> None:
    for key, proposed in contribution.items():
        existing = destination.setdefault(key, [])
        for value in proposed:
            if value and value not in existing:
                existing.append(value)


def _audio_metadata_to_dict(metadata: AudioMetadata) -> dict[str, object]:
    return {
        "title": metadata.title,
        "artists": list(metadata.artists),
        "album": metadata.album,
        "isrc": metadata.isrc,
        "catalog_id": metadata.catalog_id,
        "playlist_id": metadata.playlist_id,
        "track_number": metadata.track_number,
        "disc_number": metadata.disc_number,
        "duration_seconds": metadata.duration_seconds,
        "release_date": metadata.release_date,
    }


def _audio_metadata_from_dict(data: Mapping[str, Any]) -> AudioMetadata:
    return AudioMetadata(
        title=data.get("title"),
        artists=[str(artist) for artist in data.get("artists", [])],
        album=data.get("album"),
        isrc=data.get("isrc"),
        catalog_id=data.get("catalog_id"),
        playlist_id=data.get("playlist_id"),
        track_number=(int(data["track_number"]) if data.get("track_number") is not None else None),
        disc_number=(int(data["disc_number"]) if data.get("disc_number") is not None else None),
        duration_seconds=(
            float(data["duration_seconds"])
            if data.get("duration_seconds") is not None
            else None
        ),
        release_date=data.get("release_date"),
    )


def _match_result_to_dict(result: MatchResult) -> dict[str, object]:
    return {
        "metadata": _audio_metadata_to_dict(result.metadata),
        "sources": {
            key: _source_result_to_dict(source_result)
            for key, source_result in result.sources.items()
        },
    }


def _match_result_from_dict(data: Mapping[str, Any]) -> MatchResult:
    return MatchResult(
        metadata=_audio_metadata_from_dict(data["metadata"]),
        sources={
            str(key): _source_result_from_dict(source_result)
            for key, source_result in data["sources"].items()
        },
    )


def _source_result_to_dict(result: SourceResult) -> dict[str, object]:
    return {
        "source": result.source,
        "candidates": [_candidate_to_dict(candidate) for candidate in result.candidates],
        "groups": {key: list(candidate_ids) for key, candidate_ids in result.groups.items()},
        "recommended_ids": list(result.recommended_ids),
        "warnings": list(result.warnings),
    }


def _source_result_from_dict(data: Mapping[str, Any]) -> SourceResult:
    return SourceResult(
        source=str(data["source"]),
        candidates=tuple(
            _candidate_from_dict(candidate) for candidate in data.get("candidates", [])
        ),
        groups={
            str(key): tuple(str(candidate_id) for candidate_id in candidate_ids)
            for key, candidate_ids in data.get("groups", {}).items()
        },
        recommended_ids=tuple(
            str(candidate_id) for candidate_id in data.get("recommended_ids", [])
        ),
        warnings=tuple(str(warning) for warning in data.get("warnings", [])),
    )


def _candidate_to_dict(candidate: Candidate) -> dict[str, object]:
    return {
        "id": candidate.id,
        "source": candidate.source,
        "title": candidate.title,
        "artists": list(candidate.artists),
        "album": candidate.album,
        "aliases": list(candidate.aliases),
        "identifiers": dict(candidate.identifiers),
        "group": candidate.group,
        "rank": candidate.rank,
        "recommended": candidate.recommended,
        "evidence": [
            {
                "field": evidence.field,
                "relation": evidence.relation,
                "expected": evidence.expected,
                "actual": evidence.actual,
            }
            for evidence in candidate.evidence
        ],
        "duration_ms": candidate.duration_ms,
        "release_date": candidate.release_date,
    }


def _candidate_from_dict(data: Mapping[str, Any]) -> Candidate:
    return Candidate(
        id=str(data["id"]),
        source=str(data["source"]),
        title=data.get("title"),
        artists=tuple(str(artist) for artist in data.get("artists", [])),
        album=data.get("album"),
        aliases=tuple(str(alias) for alias in data.get("aliases", [])),
        identifiers={str(key): str(value) for key, value in data.get("identifiers", {}).items()},
        group=data.get("group"),
        rank=int(data.get("rank", 1)),
        recommended=bool(data.get("recommended", False)),
        evidence=tuple(
            MatchEvidence(
                field=str(evidence["field"]),
                relation=str(evidence["relation"]),
                expected=evidence.get("expected"),
                actual=evidence.get("actual"),
            )
            for evidence in data.get("evidence", [])
        ),
        duration_ms=(int(data["duration_ms"]) if data.get("duration_ms") is not None else None),
        release_date=data.get("release_date"),
    )


def _selection_to_dict(selection: Selection) -> dict[str, object]:
    return {
        "pair_id": selection.pair_id,
        "sources": {
            source: list(candidate_ids) for source, candidate_ids in selection.sources.items()
        },
    }


def _selection_from_dict(data: Mapping[str, Any]) -> Selection:
    return Selection(
        pair_id=str(data["pair_id"]),
        sources={
            str(source): tuple(str(candidate_id) for candidate_id in candidate_ids)
            for source, candidate_ids in data.get("sources", {}).items()
        },
    )


def _tuple_string_lists(data: Mapping[str, Sequence[Any]]) -> dict[str, tuple[str, ...]]:
    return {
        str(key): tuple(str(value) for value in values)
        for key, values in data.items()
    }


def _string_lists(data: Mapping[str, Sequence[Any]]) -> dict[str, list[str]]:
    return {
        str(key): [str(value) for value in values]
        for key, values in data.items()
    }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
