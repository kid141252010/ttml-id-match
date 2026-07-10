from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ttml_metadata.config import load_positive_int_config
from ttml_metadata.v2.application import MatchingApplication
from ttml_metadata.v2.runtime import build_default_engine
from ttml_metadata.v2.transport import HttpTransport, HttpxTransport

from .storage import FileArtifactStore, LocalJsonSessionRepository
from .vercel_storage import (
    RedisSessionRepository,
    SdkVercelBlobClient,
    UpstashRestRedisClient,
    VercelBlobArtifactStore,
)
from .workflow import SessionWorkflow


@dataclass(frozen=True)
class RuntimeSettings:
    storage_backend: str
    local_root: Path
    work_root: Path
    search_workers: int
    source_limits: dict[str, int]
    http_timeout_seconds: float
    http_attempts: int
    redis_url: str | None
    redis_token: str | None
    blob_token: str | None
    cors_origins: tuple[str, ...]

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "RuntimeSettings":
        values = environ if environ is not None else os.environ
        default_backend = "vercel" if values.get("VERCEL") else "local"
        backend = values.get("ID_MATCH_STORAGE_BACKEND", default_backend).strip().casefold() or default_backend
        root = Path(values.get("ID_MATCH_V2_ROOT", ".codex-tmp/id-match-v2"))
        work_root = Path(
            values.get(
                "ID_MATCH_WORK_ROOT",
                str(Path(tempfile.gettempdir()) / "id-match-v2-work"),
            )
        )
        cors = tuple(
            origin.strip()
            for origin in values.get(
                "ID_MATCH_CORS_ORIGINS",
                "http://127.0.0.1:5173,http://localhost:5173",
            ).split(",")
            if origin.strip()
        )
        http_timeout = float(values.get("TTML_HTTP_TIMEOUT_SECONDS", "20"))
        if http_timeout <= 0:
            raise ValueError("TTML_HTTP_TIMEOUT_SECONDS must be greater than zero")
        return cls(
            storage_backend=backend,
            local_root=root,
            work_root=work_root,
            search_workers=load_positive_int_config(
                "TTML_SEARCH_WORKERS",
                default=3,
                environ=dict(values),
            ),
            source_limits={
                source: load_positive_int_config(env_name, default=default, environ=dict(values))
                for source, env_name, default in (
                    ("apple_music", "TTML_SOURCE_APPLE_MUSIC_WORKERS", 1),
                    ("qq_music", "TTML_SOURCE_QQ_MUSIC_WORKERS", 2),
                    ("spotify", "TTML_SOURCE_SPOTIFY_WORKERS", 1),
                    ("ncm_music", "TTML_SOURCE_NCM_MUSIC_WORKERS", 1),
                )
            },
            http_timeout_seconds=http_timeout,
            http_attempts=load_positive_int_config(
                "TTML_HTTP_ATTEMPTS", default=3, environ=dict(values)
            ),
            redis_url=values.get("KV_REST_API_URL") or values.get("UPSTASH_REDIS_REST_URL"),
            redis_token=values.get("KV_REST_API_TOKEN") or values.get("UPSTASH_REDIS_REST_TOKEN"),
            blob_token=values.get("BLOB_READ_WRITE_TOKEN"),
            cors_origins=cors,
        )


def build_v2_workflow(
    settings: RuntimeSettings | None = None,
    *,
    transport: HttpTransport | None = None,
) -> SessionWorkflow:
    settings = settings or RuntimeSettings.from_env()
    if settings.storage_backend == "local":
        repository = LocalJsonSessionRepository(settings.local_root / "state")
        artifacts = FileArtifactStore(settings.local_root / "artifacts")
    elif settings.storage_backend == "vercel":
        missing = [
            name
            for name, value in (
                ("BLOB_READ_WRITE_TOKEN", settings.blob_token),
                ("KV_REST_API_URL", settings.redis_url),
                ("KV_REST_API_TOKEN", settings.redis_token),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"v2 Vercel storage requires {', '.join(missing)}")
        repository = RedisSessionRepository(
            UpstashRestRedisClient(settings.redis_url or "", settings.redis_token or "")
        )
        artifacts = VercelBlobArtifactStore(SdkVercelBlobClient(settings.blob_token or ""))
    else:
        raise RuntimeError(f"unsupported v2 storage backend: {settings.storage_backend}")

    shared_transport = transport or HttpxTransport(
        timeout_seconds=settings.http_timeout_seconds,
        attempts=settings.http_attempts,
    )
    engine = build_default_engine(
        max_workers=settings.search_workers,
        source_limits=settings.source_limits,
        transport=shared_transport,
    )
    return SessionWorkflow(
        repository,
        artifacts,
        MatchingApplication(engine),
        work_root=settings.work_root,
    )
