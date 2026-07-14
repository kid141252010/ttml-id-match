from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SourceKey = str
PairingStatus = Literal["paired", "ttml_only", "ambiguous"]
PreviewJobStatus = Literal[
    "pending",
    "running",
    "completed",
    "completed_with_errors",
    "failed",
]
ApplyFileStatus = Literal["applied", "unchanged", "failed"]


class WireModel(BaseModel):
    """Base class for strict, JSON-safe v2 wire models."""

    model_config = ConfigDict(extra="forbid")


class Evidence(WireModel):
    field: str
    relation: str
    expected: str | None = None
    actual: str | None = None


class Candidate(WireModel):
    id: str
    source: SourceKey
    title: str | None = None
    artists: list[str] = Field(default_factory=list)
    album: str | None = None
    aliases: list[str] = Field(default_factory=list)
    identifiers: dict[str, str] = Field(default_factory=dict)
    group: str | None = None
    rank: int = Field(default=1, ge=1)
    recommended: bool = False
    evidence: list[Evidence] = Field(default_factory=list)
    duration_ms: int | None = Field(default=None, ge=0)
    release_date: str | None = None


class SourceResult(WireModel):
    source: SourceKey
    candidates: list[Candidate] = Field(default_factory=list)
    groups: dict[str, list[str]] = Field(default_factory=dict)
    recommended_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Selection(WireModel):
    pair_id: str
    sources: dict[SourceKey, list[str]] = Field(default_factory=dict)


class MetadataChangeSummary(WireModel):
    added: dict[str, list[str]] = Field(default_factory=dict)
    replaced: dict[str, list[str]] = Field(default_factory=dict)
    skipped: dict[str, list[str]] = Field(default_factory=dict)
    changed: bool = False


class NormalizationSummary(WireModel):
    language_changed: bool = False
    body_text_changed: bool = False
    removed_translations: int = Field(default=0, ge=0)
    removed_transliterations: int = Field(default=0, ge=0)
    changed: bool = False


class ChangePlanSummary(WireModel):
    input_sha256: str
    output_sha256: str
    final_text: str
    changed: bool
    metadata: MetadataChangeSummary
    normalization: NormalizationSummary


class PairFile(WireModel):
    filename: str
    sha256: str


class PairFiles(WireModel):
    ttml: PairFile
    audio: PairFile | None = None


class PairPreview(WireModel):
    pair_id: str
    files: PairFiles
    sources: dict[SourceKey, SourceResult] = Field(default_factory=dict)
    default_selection: Selection
    baseline_change_plan: ChangePlanSummary


class PairingPair(WireModel):
    pair_id: str
    status: PairingStatus
    ttml_path: str
    audio_path: str | None = None
    audio_candidates: list[str] = Field(default_factory=list)


class PairingIssue(WireModel):
    code: str
    pair_id: str
    ttml_path: str
    audio_candidates: list[str] = Field(default_factory=list)


class PairingPlanResponse(WireModel):
    pairs: list[PairingPair] = Field(default_factory=list)
    issues: list[PairingIssue] = Field(default_factory=list)


class ErrorResponse(WireModel):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class PairPreviewFailure(WireModel):
    pair_id: str
    ttml_path: str
    audio_path: str | None = None
    error: ErrorResponse


class SessionResponse(WireModel):
    session_id: str
    session_token: str
    expires_at: datetime


class CleanupResponse(WireModel):
    examined: int = Field(ge=0)
    deleted: int = Field(ge=0)
    failed: int = Field(ge=0)


class PreviewJob(WireModel):
    job_id: str
    status: PreviewJobStatus
    total: int = Field(ge=0)
    completed: int = Field(ge=0)
    results: list[PairPreview] = Field(default_factory=list)
    pair_failures: list[PairPreviewFailure] = Field(default_factory=list)
    errors: list[ErrorResponse] = Field(default_factory=list)
    snapshot_id: str | None = None


class ChangePlanRequest(WireModel):
    snapshot_id: str
    selection: Selection


class ChangePlanResponse(ChangePlanSummary):
    snapshot_id: str
    pair_id: str


class ApplyRequest(WireModel):
    snapshot_id: str
    selections: list[Selection] = Field(default_factory=list)


class ApplyFileResult(WireModel):
    pair_id: str
    ttml: str
    status: ApplyFileStatus
    output_sha256: str | None = None
    backup: str | None = None
    error: ErrorResponse | None = None


class ApplyResponse(WireModel):
    snapshot_id: str
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    files: list[ApplyFileResult] = Field(default_factory=list)
