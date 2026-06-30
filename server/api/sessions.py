from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from server.models.schemas import ApplyRequest, ApplySummary, PreviewJobResponse, PreviewResponse, SessionResponse, UploadResponse
from server.services.file_service import list_session_files, pair_session_files, save_uploads
from server.services.metadata_service import MetadataService
from server.services.session_manager import SessionManager, SessionState


def build_router(session_manager: SessionManager, metadata_service: MetadataService) -> APIRouter:
    router = APIRouter(prefix="/api")

    def session(session_id: str) -> SessionState:
        try:
            return session_manager.get(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/sessions", response_model=SessionResponse)
    def create_session() -> SessionResponse:
        state = session_manager.create_session()
        return SessionResponse(session_id=state.session_id)

    @router.post("/sessions/{session_id}/upload", response_model=UploadResponse)
    async def upload_files(state: SessionState = Depends(session), files: list[UploadFile] = File(...)) -> UploadResponse:
        await save_uploads(state.upload_dir, files)
        pairs = pair_session_files(state.upload_dir)
        state.pairs = [pair.model_dump() for pair in pairs]
        state.prepared_pairs = {}
        state.previews = {}
        state.preview_fingerprint = None
        state.preview_jobs = {}
        session_manager.sync(state)
        return UploadResponse(files=list_session_files(state.upload_dir), pairs=pairs)

    @router.get("/sessions/{session_id}/pairs")
    def get_pairs(state: SessionState = Depends(session)) -> dict[str, object]:
        pairs = pair_session_files(state.upload_dir)
        state.pairs = [pair.model_dump() for pair in pairs]
        return {"pairs": pairs}

    @router.post("/sessions/{session_id}/preview", response_model=PreviewResponse)
    def preview(state: SessionState = Depends(session)) -> PreviewResponse:
        try:
            return metadata_service.preview(state)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/sessions/{session_id}/preview-jobs", response_model=PreviewJobResponse)
    def create_preview_job(state: SessionState = Depends(session)) -> PreviewJobResponse:
        try:
            response = metadata_service.create_preview_job(state)
            session_manager.sync(state)
            return response
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/sessions/{session_id}/preview-jobs/{job_id}/step", response_model=PreviewJobResponse)
    def step_preview_job(job_id: str, state: SessionState = Depends(session)) -> PreviewJobResponse:
        try:
            response = metadata_service.step_preview_job(state, job_id)
            session_manager.sync(state)
            return response
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/sessions/{session_id}/apply", response_model=ApplySummary)
    def apply(request: ApplyRequest, state: SessionState = Depends(session)) -> ApplySummary:
        try:
            summary = metadata_service.apply(state, request.selections)
            session_manager.sync(state)
            return summary
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/sessions/{session_id}/download")
    def download_all(state: SessionState = Depends(session)) -> FileResponse:
        zip_path = metadata_service.zip_outputs(state)
        return FileResponse(zip_path, media_type="application/zip", filename="ttml-results.zip")

    @router.get("/sessions/{session_id}/download/{filename}")
    def download_one(filename: str, state: SessionState = Depends(session)) -> FileResponse:
        safe_name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        path = state.output_dir / safe_name
        if not path.exists() or path.suffix.lower() != ".ttml":
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(path, media_type="application/xml", filename=path.name)

    return router
