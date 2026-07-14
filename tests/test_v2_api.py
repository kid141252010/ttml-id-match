import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.v2.api import ApiPolicy, build_v2_router, install_v2_exception_handlers
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
    def test_session_token_is_required_and_is_never_stored_in_plaintext(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = LocalJsonSessionRepository(root / "state")
            workflow = SessionWorkflow(
                repository,
                FileArtifactStore(root / "artifacts"),
                MatchingApplication(MatchingEngine([])),
                work_root=root / "work",
            )
            app = FastAPI()
            install_v2_exception_handlers(app)
            app.include_router(build_v2_router(workflow))
            client = TestClient(app)
            first = client.post("/api/v2/sessions").json()
            second = client.post("/api/v2/sessions").json()

            self.assertNotEqual(
                repository.load(first["session_id"]).data["token_hash"],
                first["session_token"],
            )
            route = f"/api/v2/sessions/{first['session_id']}/preview-jobs"
            self.assertEqual(client.post(route).status_code, 401)
            self.assertEqual(
                client.post(route, headers={"Authorization": "Bearer wrong"}).status_code,
                401,
            )
            self.assertEqual(
                client.post(
                    route,
                    headers={"Authorization": f"Bearer {second['session_token']}"},
                ).status_code,
                401,
            )

    def test_authenticated_delete_can_retry_after_artifact_cleanup_failure(self):
        class FailingOnceArtifactStore(FileArtifactStore):
            def __init__(self, root):
                super().__init__(root)
                self.fail_once = True

            def delete_prefix(self, prefix):
                if self.fail_once:
                    self.fail_once = False
                    raise OSError("temporary cleanup failure")
                return super().delete_prefix(prefix)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = SessionWorkflow(
                LocalJsonSessionRepository(root / "state"),
                FailingOnceArtifactStore(root / "artifacts"),
                MatchingApplication(MatchingEngine([])),
                work_root=root / "work",
            )
            app = FastAPI()
            install_v2_exception_handlers(app)
            app.include_router(build_v2_router(workflow))
            client = TestClient(app)
            session_id, headers = _create_session(client)

            first = client.delete(f"/api/v2/sessions/{session_id}", headers=headers)
            second = client.delete(f"/api/v2/sessions/{session_id}", headers=headers)

            self.assertEqual(first.status_code, 500)
            self.assertEqual(second.status_code, 204)

    def test_session_creation_rate_limit_returns_retry_after(self):
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
            app.include_router(build_v2_router(
                workflow,
                policy=ApiPolicy(
                    request_limit=10,
                    session_create_limit=1,
                ),
            ))
            client = TestClient(app)

            self.assertEqual(client.post("/api/v2/sessions").status_code, 200)
            limited = client.post("/api/v2/sessions")

            self.assertEqual(limited.status_code, 429)
            self.assertEqual(limited.json()["code"], "rate_limited")
            self.assertGreaterEqual(int(limited.headers["retry-after"]), 1)

    def test_oversized_upload_does_not_change_session_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = LocalJsonSessionRepository(root / "state")
            workflow = SessionWorkflow(
                repository,
                FileArtifactStore(root / "artifacts"),
                MatchingApplication(MatchingEngine([])),
                work_root=root / "work",
                max_file_bytes=4,
                max_session_bytes=8,
            )
            app = FastAPI()
            install_v2_exception_handlers(app)
            app.include_router(build_v2_router(workflow))
            client = TestClient(app)
            session_id, headers = _create_session(client)

            response = client.post(
                f"/api/v2/sessions/{session_id}/files",
                files={"files": ("large.ttml", b"12345", "application/xml")},
                headers=headers,
            )

            self.assertEqual(response.status_code, 413)
            self.assertEqual(response.json()["code"], "payload_too_large")
            self.assertEqual(repository.load(session_id).data["uploads"], [])

    def test_pair_preview_failure_is_returned_and_apply_is_rejected(self):
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
            session_id, headers = _create_session(client)
            client.post(
                f"/api/v2/sessions/{session_id}/files",
                files={"files": ("Broken.ttml", b"<tt><body></tt>", "application/xml")},
                headers=headers,
            )
            job = client.post(
                f"/api/v2/sessions/{session_id}/preview-jobs",
                headers=headers,
            ).json()
            completed = client.post(
                f"/api/v2/sessions/{session_id}/preview-jobs/{job['job_id']}/steps",
                headers=headers,
            ).json()

            self.assertEqual(completed["results"], [])
            self.assertEqual(
                completed["pair_failures"][0]["error"]["code"],
                "pair_preview_failed",
            )
            applied = client.post(
                f"/api/v2/sessions/{session_id}/apply",
                json={"snapshot_id": completed["snapshot_id"], "selections": []},
                headers=headers,
            )
            self.assertEqual(applied.status_code, 409)
            self.assertEqual(applied.json()["code"], "preview_incomplete")

    def test_cleanup_endpoint_deletes_expired_sessions_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = [100.0]
            repository = LocalJsonSessionRepository(root / "state")
            artifacts = FileArtifactStore(root / "artifacts")
            workflow = SessionWorkflow(
                repository,
                artifacts,
                MatchingApplication(MatchingEngine([])),
                work_root=root / "work",
                session_ttl_seconds=10,
                gc_grace_seconds=10,
                clock=lambda: now[0],
            )
            app = FastAPI()
            install_v2_exception_handlers(app)
            app.include_router(build_v2_router(
                workflow,
                policy=ApiPolicy(cleanup_token="cron-token"),
            ))
            client = TestClient(app)
            session_id, headers = _create_session(client)
            uploaded = client.post(
                f"/api/v2/sessions/{session_id}/files",
                files={"files": ("Song.ttml", TTML.encode(), "application/xml")},
                headers=headers,
            )
            self.assertEqual(uploaded.status_code, 200)
            now[0] = 111.0

            cleanup = client.get(
                "/api/v2/maintenance/cleanup",
                headers={"Authorization": "Bearer cron-token"},
            )
            repeated = client.get(
                "/api/v2/maintenance/cleanup",
                headers={"Authorization": "Bearer cron-token"},
            )

            self.assertEqual(cleanup.json(), {"examined": 1, "deleted": 1, "failed": 0})
            self.assertEqual(repeated.json(), {"examined": 0, "deleted": 0, "failed": 0})
            self.assertIsNone(repository.load(session_id))
            self.assertFalse((root / "artifacts" / "sessions" / session_id).exists())

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
            session_id, headers = _create_session(client)

            response = client.post(
                f"/api/v2/sessions/{session_id}/files",
                files={
                    "files": (
                        "Song.ttml",
                        TTML.encode("utf-8"),
                        "application/xml",
                    )
                },
                headers=headers,
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

            session_id, headers = _create_session(client)
            upload = client.post(
                f"/api/v2/sessions/{session_id}/files",
                files=[("files", ("Song.ttml", TTML.encode("utf-8"), "application/xml"))],
                headers=headers,
            )
            self.assertEqual(upload.status_code, 200)
            self.assertEqual(upload.json()["pairs"][0]["status"], "ttml_only")

            job = client.post(
                f"/api/v2/sessions/{session_id}/preview-jobs",
                headers=headers,
            ).json()
            completed = client.post(
                f"/api/v2/sessions/{session_id}/preview-jobs/{job['job_id']}/steps",
                headers=headers,
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
                headers=headers,
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
                headers=headers,
            )
            self.assertEqual(applied.status_code, 200)
            self.assertEqual(applied.json()["succeeded"], 1)

            downloaded = client.get(
                f"/api/v2/sessions/{session_id}/outputs/Song.ttml",
                headers=headers,
            )
            self.assertEqual(downloaded.status_code, 200)
            self.assertIn(b'key="qqMusicId" value="qq-v2"', downloaded.content)
            self.assertIn("attachment", downloaded.headers["content-disposition"])

            archive = client.get(
                f"/api/v2/sessions/{session_id}/outputs.zip",
                headers=headers,
            )
            self.assertEqual(archive.status_code, 200)
            with zipfile.ZipFile(BytesIO(archive.content)) as output_zip:
                self.assertEqual(output_zip.namelist(), ["Song.ttml"])
                self.assertIn(b'key="qqMusicId" value="qq-v2"', output_zip.read("Song.ttml"))

            deleted = client.delete(f"/api/v2/sessions/{session_id}", headers=headers)
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
            session_id, headers = _create_session(client)

            response = client.post(
                f"/api/v2/sessions/{session_id}/change-plans",
                json={
                    "snapshot_id": "missing",
                    "selection": {"pair_id": "pair-1", "sources": {}},
                },
                headers=headers,
            )

            self.assertEqual(response.status_code, 409)
            self.assertEqual(set(response.json()), {"code", "message", "retryable", "details"})
            self.assertEqual(response.json()["code"], "snapshot_conflict")


def _create_session(client: TestClient) -> tuple[str, dict[str, str]]:
    response = client.post("/api/v2/sessions")
    payload = response.json()
    return payload["session_id"], {
        "Authorization": f"Bearer {payload['session_token']}"
    }


if __name__ == "__main__":
    unittest.main()
