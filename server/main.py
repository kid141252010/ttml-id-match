from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.v2.api import build_v2_router, install_v2_exception_handlers
from server.v2.composition import RuntimeSettings, build_v2_workflow
from server.v2.workflow import SessionWorkflow


def create_app(
    v2_workflow: SessionWorkflow | None = None,
    *,
    cors_origins: tuple[str, ...] | None = None,
) -> FastAPI:
    settings = RuntimeSettings.from_env()
    workflow = v2_workflow or build_v2_workflow(settings)
    app = FastAPI(title="TTML ID Match API", version="2.0.0")
    install_v2_exception_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_origins or settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(build_v2_router(workflow))

    return app


app = create_app()
