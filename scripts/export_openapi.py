from __future__ import annotations

import json
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

from server.main import create_app  # noqa: E402
from server.v2.storage import FileArtifactStore, LocalJsonSessionRepository  # noqa: E402
from server.v2.workflow import SessionWorkflow  # noqa: E402
from ttml_metadata.v2.application import MatchingApplication  # noqa: E402
from ttml_metadata.v2.engine import MatchingEngine  # noqa: E402


def main() -> None:
    target = ROOT / "openapi" / "v2.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        temporary = Path(tmp)
        workflow = SessionWorkflow(
            LocalJsonSessionRepository(temporary / "state"),
            FileArtifactStore(temporary / "artifacts"),
            MatchingApplication(MatchingEngine([])),
            work_root=temporary / "work",
        )
        schema = create_app(v2_workflow=workflow, cors_origins=()).openapi()
    target.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
