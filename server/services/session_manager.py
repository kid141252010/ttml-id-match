from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from server.models.schemas import PreviewResult
from ttml_metadata.models import PairMetadata


@dataclass
class SessionState:
    session_id: str
    root: Path
    upload_dir: Path
    output_dir: Path
    pairs: list[dict[str, str | None]] = field(default_factory=list)
    prepared_pairs: dict[str, PairMetadata] = field(default_factory=dict)
    previews: dict[str, PreviewResult] = field(default_factory=dict)


class SessionManager:
    def __init__(self, root: Path | None = None):
        self.root = root or Path(".codex-tmp") / "id-match-sessions"
        self.root.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, SessionState] = {}

    def create_session(self) -> SessionState:
        session_id = uuid.uuid4().hex
        session_root = self.root / session_id
        upload_dir = session_root / "uploads"
        output_dir = session_root / "outputs"
        upload_dir.mkdir(parents=True, exist_ok=False)
        output_dir.mkdir(parents=True, exist_ok=False)
        state = SessionState(session_id=session_id, root=session_root, upload_dir=upload_dir, output_dir=output_dir)
        self._sessions[session_id] = state
        return state

    def get(self, session_id: str) -> SessionState:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"session not found: {session_id}") from exc

    def cleanup(self, session_id: str) -> None:
        state = self._sessions.pop(session_id, None)
        if state:
            shutil.rmtree(state.root, ignore_errors=True)
