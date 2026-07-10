import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.v2.api import build_v2_router, install_v2_exception_handlers
from server.v2.storage import (
    FileArtifactStore,
    LocalJsonSessionRepository,
    VersionConflictError,
)
from server.v2.workflow import SessionWorkflow
from ttml_metadata.models import QQMusicCandidate
from ttml_metadata.v2.application import MatchingApplication
from ttml_metadata.v2.engine import MatchingEngine
from ttml_metadata.v2.sources import QQMusicSourceAdapter


TTML = (
    '<tt xmlns="http://www.w3.org/ns/ttml" '
    'xmlns:amll="http://www.example.com/ns/amll" xml:lang="zh-Hans">'
    '<head><metadata><amll:meta key="musicName" value="Song"/></metadata></head>'
    '<body><div><p>Song</p></div></body></tt>'
)


class QQClient:
    def search_songs(self, _query):
        return [
            QQMusicCandidate(
                song_id="qq-v2",
                mid="qq-mid-v2",
                title="Song",
                artists=["Artist"],
                album="Album",
            )
        ]


class V2ApiTests(unittest.TestCase):
    def test_openapi_documents_the_uniform_v2_validation_error_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = SessionWorkflow(
                LocalJsonSessionRepository(root / "state"),
                FileArtifactStore(root / "artifacts"),
                MatchingApplication(MatchingEngine([])),
                work_root=root / "work",
            )
            app = FastAPI()
            install_v2_exception_handlers(app)
            app.include_router(build_v2_router(workflow))

            schema = app.openapi()
            validation_responses = [
                operation["responses"]["422"]
                for path in schema["paths"].values()
                for operation in path.values()
                if "422" in operation.get("responses", {})
            ]

            self.assertTrue(validation_responses)
            self.assertTrue(all(
                response["content"]["application/json"]["schema"]["$ref"]
                == "#/components/schemas/ErrorResponse"
                for response in validation_responses
            ))

    def test_session_cas_conflicts_are_retryable_v2_conflicts(self):
        class ConflictingRepository(LocalJsonSessionRepository):
            def save(self, session_id, data, *, expected_version):
                raise VersionConflictError("concurrent session update")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = SessionWorkflow(
                ConflictingRepository(root / "state"),
                FileArtifactStore(root / "artifacts"),
                MatchingApplication(MatchingEngine([])),
                work_root=root / "work",
            )
            app = FastAPI()
            install_v2_exception_handlers(app)
            app.include_router(build_v2_router(workflow))
            client = TestClient(app)
            session_id = client.post("/api/v2/sessions").json()["session_id"]

            response = client.post(
                f"/api/v2/sessions/{session_id}/files",
                files={
                    "files": (
                        "Song.ttml",
                        TTML.encode("utf-8"),
                        "application/xml",
                    )
                },
            )

            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["code"], "session_conflict")
            self.assertTrue(response.json()["retryable"])

    def test_upload_preview_change_plan_apply_and_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = SessionWorkflow(
                LocalJsonSessionRepository(root / "state"),
                FileArtifactStore(root / "artifacts"),
                MatchingApplication(MatchingEngine([QQMusicSourceAdapter(QQClient())])),
                work_root=root / "work",
            )
            app = FastAPI()
            install_v2_exception_handlers(app)
            app.include_router(build_v2_router(workflow))
            client = TestClient(app)

            session_id = client.post("/api/v2/sessions").json()["session_id"]
            upload = client.post(
                f"/api/v2/sessions/{session_id}/files",
                files=[("files", ("Song.ttml", TTML.encode("utf-8"), "application/xml"))],
            )
            self.assertEqual(upload.status_code, 200)
            self.assertEqual(upload.json()["pairs"][0]["status"], "ttml_only")

            job = client.post(f"/api/v2/sessions/{session_id}/preview-jobs").json()
            completed = client.post(
                f"/api/v2/sessions/{session_id}/preview-jobs/{job['job_id']}/steps"
            )
            self.assertEqual(completed.status_code, 200)
            body = completed.json()
            self.assertEqual(body["status"], "completed")
            preview = body["results"][0]
            self.assertEqual(list(preview["sources"]), ["qq_music"])
            self.assertNotIn("score", preview["sources"]["qq_music"]["candidates"][0])

            selection = preview["default_selection"]
            change_plan = client.post(
                f"/api/v2/sessions/{session_id}/change-plans",
                json={"snapshot_id": body["snapshot_id"], "selection": selection},
            )
            self.assertEqual(change_plan.status_code, 200)
            self.assertIn("qqMusicId", change_plan.json()["metadata"]["added"])
            self.assertIn(
                'key="qqMusicId" value="qq-v2"',
                change_plan.json()["final_text"],
            )

            applied = client.post(
                f"/api/v2/sessions/{session_id}/apply",
                json={"snapshot_id": body["snapshot_id"], "selections": [selection]},
            )
            self.assertEqual(applied.status_code, 200)
            self.assertEqual(applied.json()["succeeded"], 1)

            downloaded = client.get(f"/api/v2/sessions/{session_id}/outputs/Song.ttml")
            self.assertEqual(downloaded.status_code, 200)
            self.assertIn(b'key="qqMusicId" value="qq-v2"', downloaded.content)
            self.assertIn("attachment", downloaded.headers["content-disposition"])

            archive = client.get(f"/api/v2/sessions/{session_id}/outputs.zip")
            self.assertEqual(archive.status_code, 200)
            with zipfile.ZipFile(BytesIO(archive.content)) as output_zip:
                self.assertEqual(output_zip.namelist(), ["Song.ttml"])
                self.assertIn(b'key="qqMusicId" value="qq-v2"', output_zip.read("Song.ttml"))

            deleted = client.delete(f"/api/v2/sessions/{session_id}")
            self.assertEqual(deleted.status_code, 204)

    def test_errors_use_the_v2_error_shape_without_fastapi_detail_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = SessionWorkflow(
                LocalJsonSessionRepository(root / "state"),
                FileArtifactStore(root / "artifacts"),
                MatchingApplication(MatchingEngine([])),
                work_root=root / "work",
            )
            app = FastAPI()
            install_v2_exception_handlers(app)
            app.include_router(build_v2_router(workflow))
            client = TestClient(app)
            session_id = client.post("/api/v2/sessions").json()["session_id"]

            response = client.post(
                f"/api/v2/sessions/{session_id}/change-plans",
                json={
                    "snapshot_id": "missing",
                    "selection": {"pair_id": "pair-1", "sources": {}},
                },
            )

            self.assertEqual(response.status_code, 409)
            self.assertEqual(set(response.json()), {"code", "message", "retryable", "details"})
            self.assertEqual(response.json()["code"], "snapshot_conflict")


if __name__ == "__main__":
    unittest.main()
