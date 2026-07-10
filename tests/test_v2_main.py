import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from server.main import create_app
from server.v2.storage import FileArtifactStore, LocalJsonSessionRepository
from server.v2.workflow import SessionWorkflow
from ttml_metadata.v2.application import MatchingApplication
from ttml_metadata.v2.engine import MatchingEngine


class V2MainTests(unittest.TestCase):
    def test_main_app_mounts_v2_router_with_injected_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = SessionWorkflow(
                LocalJsonSessionRepository(root / "state"),
                FileArtifactStore(root / "artifacts"),
                MatchingApplication(MatchingEngine([])),
                work_root=root / "work",
            )
            app = create_app(v2_workflow=workflow)
            client = TestClient(app)

            response = client.post("/api/v2/sessions")

            self.assertEqual(response.status_code, 200)
            self.assertRegex(response.json()["session_id"], r"^[0-9a-f]{32}$")
            self.assertEqual(client.post("/api/sessions").status_code, 404)
            paths = client.get("/openapi.json").json()["paths"]
            self.assertTrue(all(path.startswith("/api/v2") for path in paths))
            self.assertNotIn("/api/v2/health", paths)


if __name__ == "__main__":
    unittest.main()
