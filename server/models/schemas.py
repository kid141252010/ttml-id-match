from __future__ import annotations

from pydantic import BaseModel, Field


class SessionResponse(BaseModel):
    session_id: str


class SessionFile(BaseModel):
    name: str
    size: int
    kind: str


class FilePair(BaseModel):
    id: str
    ttml: str | None
    audio: str | None
    status: str


class UploadResponse(BaseModel):
    files: list[SessionFile]
    pairs: list[FilePair]


class Candidate(BaseModel):
    id: str
    title: str | None = None
    artists: list[str] = Field(default_factory=list)
    album: str | None = None
    region: str | None = None
    isrc: str | None = None
    duration_ms: int | None = None
    release_date: str | None = None
    score: int = 0
    source: str


class SourcePreview(BaseModel):
    best: list[Candidate] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    candidates_by_storefront: dict[str, list[Candidate]] = Field(default_factory=dict)
    candidates_by_market: dict[str, list[Candidate]] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class ChangeSet(BaseModel):
    added: dict[str, list[str]] = Field(default_factory=dict)
    replaced: dict[str, list[str]] = Field(default_factory=dict)
    skipped: dict[str, list[str]] = Field(default_factory=dict)


class PreviewResult(BaseModel):
    pair_id: str
    ttml: str
    audio: str | None
    apple_music: SourcePreview
    qq_music: SourcePreview
    ncm_music: SourcePreview
    spotify: SourcePreview
    changes: ChangeSet


class PreviewResponse(BaseModel):
    results: list[PreviewResult]


class PreviewJobResponse(BaseModel):
    job_id: str
    status: str
    total: int
    completed: int
    results: list[PreviewResult] = Field(default_factory=list)
    error: str | None = None


class SelectionPayload(BaseModel):
    pair_id: str
    apple_music: list[str] = Field(default_factory=list)
    qq_music: list[str] = Field(default_factory=list)
    ncm_music: list[str] = Field(default_factory=list)
    spotify: list[str] = Field(default_factory=list)


class ApplyRequest(BaseModel):
    selections: list[SelectionPayload] = Field(default_factory=list)


class AppliedFile(BaseModel):
    pair_id: str
    ttml: str
    status: str
    metadata_written: list[str] = Field(default_factory=list)
    error: str | None = None


class ApplySummary(BaseModel):
    succeeded: int
    failed: int
    skipped: int
    files: list[AppliedFile]
