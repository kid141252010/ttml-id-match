from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import fastapi  # noqa: F401
except ModuleNotFoundError:
    candidates = (
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
    )
    interpreter = next((path for path in candidates if path.is_file()), None)
    if interpreter is None:
        raise
    os.execv(str(interpreter), [str(interpreter), str(Path(__file__).resolve())])

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from server.v2.api import build_v2_router, install_v2_exception_handlers  # noqa: E402
from server.v2.storage import FileArtifactStore, LocalJsonSessionRepository  # noqa: E402
from server.v2.workflow import SessionWorkflow  # noqa: E402
from ttml_metadata.models import QQMusicCandidate  # noqa: E402
from ttml_metadata.v2.application import MatchingApplication  # noqa: E402
from ttml_metadata.v2.engine import MatchingEngine  # noqa: E402
from ttml_metadata.v2.sources import QQMusicSourceAdapter  # noqa: E402


class FixtureQQClient:
    def search_songs(self, _query: str) -> list[QQMusicCandidate]:
        return [
            QQMusicCandidate(
                song_id="qq-best",
                mid="qq-mid-best",
                title="Song",
                artists=["Artist"],
                album="Album",
                source_index=0,
            ),
            QQMusicCandidate(
                song_id="qq-alt",
                mid="qq-mid-alt",
                title="Song (Alt)",
                artists=["Artist"],
                album="Album",
                source_index=1,
            ),
        ]


temporary = tempfile.TemporaryDirectory(prefix="id-match-e2e-")
root = Path(temporary.name)
workflow = SessionWorkflow(
    LocalJsonSessionRepository(root / "state"),
    FileArtifactStore(root / "artifacts"),
    MatchingApplication(
        MatchingEngine([QQMusicSourceAdapter(FixtureQQClient())], max_workers=2)
    ),
    work_root=root / "work",
)
app = FastAPI(title="TTML ID Match E2E")
install_v2_exception_handlers(app)
app.include_router(build_v2_router(workflow))


@app.get("/api/v2/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
