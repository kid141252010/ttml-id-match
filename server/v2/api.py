from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, FastAPI, File, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ttml_metadata.v2.application import PairSnapshot
from ttml_metadata.v2.domain import Selection as DomainSelection
from ttml_metadata.v2.engine import UnknownCandidateError
from ttml_metadata.v2.ttml_plan import ChangePlan, TtmlInputChangedError

from .storage import VersionConflictError

from .schemas import (
    ApplyRequest,
    ApplyResponse,
    ChangePlanRequest,
    ChangePlanResponse,
    ChangePlanSummary,
    ErrorResponse,
    PairFile,
    PairFiles,
    PairPreview,
    PairingPlanResponse,
    PreviewJob,
    Selection,
    SessionResponse,
    SourceResult,
)
from .workflow import (
    InvalidSelectionError,
    JobBusyError,
    PairingConflictError,
    PreviewJobNotFoundError,
    SessionNotFoundError,
    SessionWorkflow,
    SnapshotConflictError,
    UploadData,
)


class V2ApiException(Exception):
    def __init__(self, status_code: int, error: ErrorResponse):
        super().__init__(error.message)
        self.status_code = status_code
        self.error = error


def install_v2_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(V2ApiException)
    async def handle_v2_error(_request: Request, exc: V2ApiException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.error.model_dump(mode="json"),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        error = ErrorResponse(
            code="invalid_request",
            message="Request validation failed",
            retryable=False,
            details={"errors": jsonable_encoder(exc.errors())},
        )
        return JSONResponse(status_code=422, content=error.model_dump(mode="json"))


def build_v2_router(workflow: SessionWorkflow) -> APIRouter:
    router = APIRouter(
        prefix="/api/v2",
        responses={
            422: {
                "model": ErrorResponse,
                "description": "Invalid request",
            }
        },
    )

    @router.post("/sessions", response_model=SessionResponse)
    def create_session() -> SessionResponse:
        return SessionResponse(session_id=workflow.create_session())

    @router.delete("/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str) -> Response:
        try:
            workflow.delete_session(session_id)
        except Exception as exc:
            _raise_http(exc)
        return Response(status_code=204)

    @router.post("/sessions/{session_id}/files", response_model=PairingPlanResponse)
    async def upload_files(
        session_id: str,
        files: list[UploadFile] = File(...),
    ) -> PairingPlanResponse:
        try:
            uploads = [UploadData(file.filename or "uploaded.bin", await file.read()) for file in files]
            return PairingPlanResponse.model_validate(workflow.upload_files(session_id, uploads))
        except Exception as exc:
            _raise_http(exc)

    @router.post("/sessions/{session_id}/preview-jobs", response_model=PreviewJob)
    def create_preview_job(session_id: str) -> PreviewJob:
        try:
            return _preview_job(workflow.create_preview_job(session_id))
        except Exception as exc:
            _raise_http(exc)

    @router.get("/sessions/{session_id}/preview-jobs/{job_id}", response_model=PreviewJob)
    def get_preview_job(session_id: str, job_id: str) -> PreviewJob:
        try:
            return _preview_job(workflow.get_preview_job(session_id, job_id))
        except Exception as exc:
            _raise_http(exc)

    @router.post("/sessions/{session_id}/preview-jobs/{job_id}/steps", response_model=PreviewJob)
    def step_preview_job(session_id: str, job_id: str) -> PreviewJob:
        try:
            return _preview_job(workflow.step_preview_job(session_id, job_id))
        except Exception as exc:
            _raise_http(exc)

    @router.post("/sessions/{session_id}/change-plans", response_model=ChangePlanResponse)
    def change_plan(session_id: str, request: ChangePlanRequest) -> ChangePlanResponse:
        try:
            plan = workflow.change_plan(
                session_id,
                request.snapshot_id,
                _domain_selection(request.selection),
            )
            return _change_plan_response(request.snapshot_id, request.selection.pair_id, plan)
        except Exception as exc:
            _raise_http(exc)

    @router.post("/sessions/{session_id}/apply", response_model=ApplyResponse)
    def apply(session_id: str, request: ApplyRequest) -> ApplyResponse:
        try:
            response = workflow.apply(
                session_id,
                request.snapshot_id,
                [_domain_selection(selection) for selection in request.selections],
            )
            return ApplyResponse.model_validate(response)
        except Exception as exc:
            _raise_http(exc)

    @router.get("/sessions/{session_id}/outputs.zip")
    def download_outputs_zip(session_id: str) -> Response:
        try:
            content = workflow.get_outputs_zip(session_id)
        except Exception as exc:
            _raise_http(exc)
        return Response(
            content=content,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="ttml-results.zip"'},
        )

    @router.get("/sessions/{session_id}/outputs/{filename}")
    def download_output(session_id: str, filename: str) -> Response:
        try:
            content = workflow.get_output(session_id, filename)
        except Exception as exc:
            _raise_http(exc)
        return Response(
            content=content,
            media_type="application/xml",
            headers={
                "Content-Disposition": (
                    f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
                )
            },
        )

    return router


def _preview_job(value: dict[str, object]) -> PreviewJob:
    return PreviewJob(
        job_id=str(value["job_id"]),
        status=str(value["status"]),
        total=int(value["total"]),
        completed=int(value["completed"]),
        results=[_pair_preview(PairSnapshot.from_dict(item)) for item in value.get("results", [])],
        errors=[ErrorResponse.model_validate(item) for item in value.get("errors", [])],
        snapshot_id=str(value["snapshot_id"]) if value.get("snapshot_id") else None,
    )


def _pair_preview(snapshot: PairSnapshot) -> PairPreview:
    return PairPreview(
        pair_id=snapshot.pair_id,
        files=PairFiles(
            ttml=PairFile(filename=snapshot.ttml_filename, sha256=snapshot.ttml_sha256),
            audio=(
                PairFile(filename=snapshot.audio_filename, sha256=snapshot.audio_sha256 or "")
                if snapshot.audio_filename
                else None
            ),
        ),
        sources={
            key: SourceResult.model_validate(
                {
                    "source": result.source,
                    "candidates": [
                        {
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
                        for candidate in result.candidates
                    ],
                    "groups": {group: list(ids) for group, ids in result.groups.items()},
                    "recommended_ids": list(result.recommended_ids),
                    "warnings": list(result.warnings),
                }
            )
            for key, result in snapshot.match_result.sources.items()
        },
        default_selection=Selection(
            pair_id=snapshot.default_selection.pair_id,
            sources={key: list(ids) for key, ids in snapshot.default_selection.sources.items()},
        ),
        baseline_change_plan=ChangePlanSummary.model_validate(snapshot.baseline_change_plan.to_dict()),
    )


def _domain_selection(selection: Selection) -> DomainSelection:
    return DomainSelection(
        pair_id=selection.pair_id,
        sources={key: tuple(ids) for key, ids in selection.sources.items()},
    )


def _change_plan_response(snapshot_id: str, pair_id: str, plan: ChangePlan) -> ChangePlanResponse:
    summary = {
        "input_sha256": plan.input_sha256,
        "output_sha256": plan.output_sha256,
        "final_text": plan.final_text,
        "changed": plan.changed,
        "metadata": {
            "added": plan.metadata.added,
            "replaced": plan.metadata.replaced,
            "skipped": plan.metadata.skipped,
            "changed": plan.metadata.changed,
        },
        "normalization": {
            "language_changed": plan.language.language_changed,
            "body_text_changed": plan.language.body_text_changed,
            "removed_translations": plan.language.removed_translations,
            "removed_transliterations": plan.language.removed_transliterations,
            "changed": plan.language.changed,
        },
    }
    return ChangePlanResponse(snapshot_id=snapshot_id, pair_id=pair_id, **summary)


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, (SessionNotFoundError, PreviewJobNotFoundError, FileNotFoundError, KeyError)):
        status, code, retryable = 404, "not_found", False
    elif isinstance(exc, JobBusyError):
        status, code, retryable = 409, "job_busy", True
    elif isinstance(exc, VersionConflictError):
        status, code, retryable = 409, "session_conflict", True
    elif isinstance(exc, SnapshotConflictError):
        status, code, retryable = 409, "snapshot_conflict", False
    elif isinstance(exc, TtmlInputChangedError):
        status, code, retryable = 409, "input_changed", False
    elif isinstance(exc, PairingConflictError):
        status, code, retryable = 409, "pairing_conflict", False
    elif isinstance(exc, (InvalidSelectionError, UnknownCandidateError, ValueError)):
        status, code, retryable = 422, "invalid_request", False
    else:
        status, code, retryable = 500, "internal_error", False
    error = ErrorResponse(code=code, message=str(exc), retryable=retryable)
    raise V2ApiException(status, error) from exc
