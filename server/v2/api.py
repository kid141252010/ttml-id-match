from __future__ import annotations

import hashlib
import hmac
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, FastAPI, File, Request, Response, Security, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBearer
from starlette.background import BackgroundTask

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
    CleanupResponse,
    ErrorResponse,
    PairFile,
    PairFiles,
    PairPreview,
    PairPreviewFailure,
    PairingPlanResponse,
    PreviewJob,
    Selection,
    SessionResponse,
    SourceResult,
)
from .workflow import (
    InvalidSelectionError,
    JobBusyError,
    PayloadTooLargeError,
    PairingConflictError,
    PreviewIncompleteError,
    PreviewJobNotFoundError,
    SessionNotFoundError,
    SessionQuotaExceededError,
    SessionUnauthorizedError,
    SessionWorkflow,
    SnapshotConflictError,
    UploadData,
)


class V2ApiException(Exception):
    def __init__(
        self,
        status_code: int,
        error: ErrorResponse,
        *,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(error.message)
        self.status_code = status_code
        self.error = error
        self.headers = headers or {}


@dataclass(frozen=True)
class ApiPolicy:
    request_limit: int = 240
    request_window_seconds: int = 60
    session_create_limit: int = 10
    session_create_window_seconds: int = 3600
    trust_proxy_headers: bool = False
    cleanup_token: str | None = None


def install_v2_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(V2ApiException)
    async def handle_v2_error(_request: Request, exc: V2ApiException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.error.model_dump(mode="json"),
            headers=exc.headers,
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


def build_v2_router(
    workflow: SessionWorkflow,
    *,
    policy: ApiPolicy | None = None,
) -> APIRouter:
    policy = policy or ApiPolicy()
    session_bearer = HTTPBearer(auto_error=False, scheme_name="SessionBearer")
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
    def create_session(request: Request) -> SessionResponse:
        _enforce_rate_limits(workflow, request, policy, creating_session=True)
        credentials = workflow.create_session_credentials()
        return SessionResponse(
            session_id=credentials.session_id,
            session_token=credentials.session_token,
            expires_at=datetime.fromtimestamp(credentials.expires_at, timezone.utc),
        )

    @router.delete(
        "/sessions/{session_id}",
        status_code=204,
        dependencies=[Security(session_bearer)],
    )
    def delete_session(session_id: str, request: Request) -> Response:
        try:
            _authorize_request(workflow, request, session_id, policy)
            workflow.delete_session(session_id)
        except Exception as exc:
            _raise_http(exc)
        return Response(status_code=204)

    @router.post(
        "/sessions/{session_id}/files",
        response_model=PairingPlanResponse,
        dependencies=[Security(session_bearer)],
    )
    async def upload_files(
        session_id: str,
        request: Request,
        files: list[UploadFile] = File(...),
    ) -> PairingPlanResponse:
        try:
            _authorize_request(workflow, request, session_id, policy)
            with tempfile.TemporaryDirectory(prefix="id-match-upload-") as tmp:
                uploads = await _stage_uploads(files, Path(tmp), workflow)
                return PairingPlanResponse.model_validate(
                    workflow.upload_files(session_id, uploads)
                )
        except Exception as exc:
            _raise_http(exc)
        finally:
            for file in files:
                await file.close()

    @router.post(
        "/sessions/{session_id}/preview-jobs",
        response_model=PreviewJob,
        dependencies=[Security(session_bearer)],
    )
    def create_preview_job(session_id: str, request: Request) -> PreviewJob:
        try:
            _authorize_request(workflow, request, session_id, policy)
            return _preview_job(workflow.create_preview_job(session_id))
        except Exception as exc:
            _raise_http(exc)

    @router.get(
        "/sessions/{session_id}/preview-jobs/{job_id}",
        response_model=PreviewJob,
        dependencies=[Security(session_bearer)],
    )
    def get_preview_job(session_id: str, job_id: str, request: Request) -> PreviewJob:
        try:
            _authorize_request(workflow, request, session_id, policy)
            return _preview_job(workflow.get_preview_job(session_id, job_id))
        except Exception as exc:
            _raise_http(exc)

    @router.post(
        "/sessions/{session_id}/preview-jobs/{job_id}/steps",
        response_model=PreviewJob,
        dependencies=[Security(session_bearer)],
    )
    def step_preview_job(session_id: str, job_id: str, request: Request) -> PreviewJob:
        try:
            _authorize_request(workflow, request, session_id, policy)
            return _preview_job(workflow.step_preview_job(session_id, job_id))
        except Exception as exc:
            _raise_http(exc)

    @router.post(
        "/sessions/{session_id}/change-plans",
        response_model=ChangePlanResponse,
        dependencies=[Security(session_bearer)],
    )
    def change_plan(
        session_id: str,
        request_body: ChangePlanRequest,
        request: Request,
    ) -> ChangePlanResponse:
        try:
            _authorize_request(workflow, request, session_id, policy)
            plan = workflow.change_plan(
                session_id,
                request_body.snapshot_id,
                _domain_selection(request_body.selection),
            )
            return _change_plan_response(
                request_body.snapshot_id,
                request_body.selection.pair_id,
                plan,
            )
        except Exception as exc:
            _raise_http(exc)

    @router.post(
        "/sessions/{session_id}/apply",
        response_model=ApplyResponse,
        dependencies=[Security(session_bearer)],
    )
    def apply(
        session_id: str,
        request_body: ApplyRequest,
        request: Request,
    ) -> ApplyResponse:
        try:
            _authorize_request(workflow, request, session_id, policy)
            response = workflow.apply(
                session_id,
                request_body.snapshot_id,
                [_domain_selection(selection) for selection in request_body.selections],
            )
            return ApplyResponse.model_validate(response)
        except Exception as exc:
            _raise_http(exc)

    @router.get(
        "/sessions/{session_id}/outputs.zip",
        dependencies=[Security(session_bearer)],
    )
    def download_outputs_zip(session_id: str, request: Request) -> FileResponse:
        temporary_root = Path(tempfile.mkdtemp(prefix="id-match-download-"))
        try:
            _authorize_request(workflow, request, session_id, policy)
            path = workflow.build_outputs_zip(
                session_id,
                temporary_root / "ttml-results.zip",
            )
        except Exception as exc:
            shutil.rmtree(temporary_root, ignore_errors=True)
            _raise_http(exc)
        return FileResponse(
            path,
            media_type="application/zip",
            filename="ttml-results.zip",
            background=BackgroundTask(shutil.rmtree, temporary_root, ignore_errors=True),
        )

    @router.get(
        "/sessions/{session_id}/outputs/{filename}",
        dependencies=[Security(session_bearer)],
    )
    def download_output(session_id: str, filename: str, request: Request) -> FileResponse:
        temporary_root = Path(tempfile.mkdtemp(prefix="id-match-download-"))
        try:
            _authorize_request(workflow, request, session_id, policy)
            path = workflow.materialize_output(
                session_id,
                filename,
                temporary_root / Path(filename).name,
            )
        except Exception as exc:
            shutil.rmtree(temporary_root, ignore_errors=True)
            _raise_http(exc)
        return FileResponse(
            path,
            media_type="application/xml",
            filename=Path(filename).name,
            background=BackgroundTask(shutil.rmtree, temporary_root, ignore_errors=True),
        )

    @router.get("/maintenance/cleanup", response_model=CleanupResponse)
    def cleanup_expired_sessions(request: Request) -> CleanupResponse:
        expected = policy.cleanup_token
        provided = _bearer_token(request)
        if not expected or not provided or not hmac.compare_digest(expected, provided):
            raise V2ApiException(
                401,
                ErrorResponse(
                    code="unauthorized",
                    message="valid cleanup bearer token required",
                    retryable=False,
                ),
            )
        return CleanupResponse.model_validate(workflow.cleanup_expired_sessions(limit=100))

    return router


async def _stage_uploads(
    files: list[UploadFile],
    root: Path,
    workflow: SessionWorkflow,
) -> list[UploadData]:
    if len(files) > workflow.max_files:
        raise SessionQuotaExceededError(
            f"file count exceeds session limit of {workflow.max_files}"
        )
    staged: list[UploadData] = []
    total_size = 0
    for index, file in enumerate(files):
        destination = root / f"upload-{index}"
        digest = hashlib.sha256()
        size = 0
        with destination.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                total_size += len(chunk)
                if size > workflow.max_file_bytes:
                    raise PayloadTooLargeError(
                        f"file {file.filename!r} exceeds limit of {workflow.max_file_bytes} bytes"
                    )
                if total_size > workflow.max_session_bytes:
                    raise PayloadTooLargeError(
                        f"upload size exceeds session limit of {workflow.max_session_bytes} bytes"
                    )
                digest.update(chunk)
                output.write(chunk)
        staged.append(
            UploadData(
                filename=file.filename or "uploaded.bin",
                path=destination,
                sha256=digest.hexdigest(),
                size=size,
            )
        )
    return staged


def _authorize_request(
    workflow: SessionWorkflow,
    request: Request,
    session_id: str,
    policy: ApiPolicy,
) -> None:
    _enforce_rate_limits(workflow, request, policy, creating_session=False)
    workflow.authorize_session(session_id, _bearer_token(request) or "")


def _enforce_rate_limits(
    workflow: SessionWorkflow,
    request: Request,
    policy: ApiPolicy,
    *,
    creating_session: bool,
) -> None:
    identity = _client_identity(request, trust_proxy_headers=policy.trust_proxy_headers)
    fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    limits = [
        ("requests", policy.request_limit, policy.request_window_seconds),
    ]
    if creating_session:
        limits.append(
            (
                "session-create",
                policy.session_create_limit,
                policy.session_create_window_seconds,
            )
        )
    for bucket, limit, window in limits:
        result = workflow.consume_rate_limit(
            f"{bucket}:{fingerprint}",
            limit=limit,
            window_seconds=window,
        )
        if not result.allowed:
            raise V2ApiException(
                429,
                ErrorResponse(
                    code="rate_limited",
                    message="Request rate limit exceeded",
                    retryable=True,
                    details={"limit": limit, "window_seconds": window},
                ),
                headers={"Retry-After": str(result.retry_after_seconds)},
            )


def _client_identity(request: Request, *, trust_proxy_headers: bool) -> str:
    if trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _bearer_token(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.casefold() != "bearer" or not token:
        return None
    return token.strip()


def _preview_job(value: dict[str, object]) -> PreviewJob:
    return PreviewJob(
        job_id=str(value["job_id"]),
        status=str(value["status"]),
        total=int(value["total"]),
        completed=int(value["completed"]),
        results=[_pair_preview(PairSnapshot.from_dict(item)) for item in value.get("results", [])],
        pair_failures=[
            PairPreviewFailure.model_validate(item)
            for item in value.get("pair_failures", [])
        ],
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
    elif isinstance(exc, PreviewIncompleteError):
        status, code, retryable = 409, "preview_incomplete", False
    elif isinstance(exc, SessionUnauthorizedError):
        status, code, retryable = 401, "unauthorized", False
    elif isinstance(exc, PayloadTooLargeError):
        status, code, retryable = 413, "payload_too_large", False
    elif isinstance(exc, SessionQuotaExceededError):
        status, code, retryable = 429, "quota_exceeded", False
    elif isinstance(exc, (InvalidSelectionError, UnknownCandidateError, ValueError)):
        status, code, retryable = 422, "invalid_request", False
    else:
        status, code, retryable = 500, "internal_error", False
    error = ErrorResponse(code=code, message=str(exc), retryable=retryable)
    raise V2ApiException(status, error) from exc
