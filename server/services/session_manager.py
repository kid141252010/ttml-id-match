from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from server.services.storage import ArtifactStore, StoredSession, build_session_store


SessionState = StoredSession


class SessionManager:
    def __init__(self, root: Path | None = None, store: ArtifactStore | None = None):
        self.store = store or build_session_store(root)

    def create_session(self) -> SessionState:
        return self.store.create_session()

    def get(self, session_id: str) -> SessionState:
        return self.store.get_session(session_id)

    def cleanup(self, session_id: str) -> None:
        self.store.cleanup_session(session_id)

    def sync(self, state: SessionState) -> None:
        self.store.sync_session(state)
