from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.api.sessions import build_router
from server.services.metadata_service import MetadataService
from server.services.session_manager import SessionManager


def create_app(
    session_manager: SessionManager | None = None,
    metadata_service: MetadataService | None = None,
) -> FastAPI:
    manager = session_manager or SessionManager()
    service = metadata_service or MetadataService(manager)
    app = FastAPI(title="TTML ID Match API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(build_router(manager, service))

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
