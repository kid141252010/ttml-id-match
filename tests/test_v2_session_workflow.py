import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from server.v2.storage import (
    FileArtifactStore,
    LocalJsonSessionRepository,
    VersionConflictError,
)
from server.v2.vercel_storage import RedisSessionRepository, VercelBlobArtifactStore
from server.v2.workflow import (
    InvalidSelectionError,
    JobBusyError,
    PairingConflictError,
    SessionNotFoundError,
    SessionWorkflow,
    SnapshotConflictError,
    UploadData,
)
from tests.test_v2_vercel_storage import FakeBlobClient, FakeRedisJsonClient
from ttml_metadata.models import QQMusicCandidate
from ttml_metadata.v2.application import MatchingApplication
from ttml_metadata.v2.domain import Selection
from ttml_metadata.v2.engine import MatchingEngine
from ttml_metadata.v2.sources import QQMusicSourceAdapter


TTML = (
    '<tt xmlns="http://www.w3.org/ns/ttml" '
    'xmlns:amll="http://www.example.com/ns/amll" xml:lang="zh-Hans">'
    '<head><metadata><amll:meta key="musicName" value="Song"/></metadata></head>'
    '<body><div><p>Song</p></div></body></tt>'
)


class PreviewQQClient:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = 0

    def search_songs(self, _query):
        self.calls += 1
        if self.fail:
            raise AssertionError("apply must not query upstream sources")
        return [
            QQMusicCandidate(
                song_id="qq-preview",
                mid="qq-mid-preview",
                title="Song",
                artists=["Artist"],
                album="Album",
            )
        ]


class SessionWorkflowTests(unittest.TestCase):
    def test_upload_cas_conflict_cannot_overwrite_the_committed_artifact(self):
        class CoordinatedRepository(LocalJsonSessionRepository):
            def __init__(self, root):
                super().__init__(root)
                self.winner_saved = threading.Event()

            def save(self, session_id, data, *, expected_version):
                if threading.current_thread().name == "winner":
                    saved = super().save(
                        session_id,
                        data,
                        expected_version=expected_version,
                    )
                    self.winner_saved.set()
                    return saved
                if threading.current_thread().name == "loser":
                    if not self.winner_saved.wait(timeout=2):
                        raise TimeoutError("winning upload did not commit")
                return super().save(
                    session_id,
                    data,
                    expected_version=expected_version,
                )

        class CoordinatedArtifactStore(FileArtifactStore):
            def __init__(self, root, repository):
                super().__init__(root)
                self.repository = repository
                self.loser_waiting = threading.Event()

            def put_bytes(self, key, content):
                if "/uploads/" in key and threading.current_thread().name == "loser":
                    self.loser_waiting.set()
                    if not self.repository.winner_saved.wait(timeout=2):
                        raise TimeoutError("winning upload did not commit")
                return super().put_bytes(key, content)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = CoordinatedRepository(root / "state")
            artifacts = CoordinatedArtifactStore(root / "artifacts", repository)
            first = SessionWorkflow(
                repository,
                artifacts,
                MatchingApplication(MatchingEngine([])),
                work_root=root / "work-1",
            )
            second = SessionWorkflow(
                repository,
                artifacts,
                MatchingApplication(MatchingEngine([])),
                work_root=root / "work-2",
            )
            session_id = first.create_session()

            with ThreadPoolExecutor(max_workers=2) as executor:
                loser = executor.submit(
                    lambda: threading.current_thread().__setattr__("name", "loser")
                    or first.upload_files(
                        session_id,
                        [UploadData("Song.ttml", b"losing content")],
                    )
                )
                self.assertTrue(artifacts.loser_waiting.wait(timeout=1))
                winner = executor.submit(
                    lambda: threading.current_thread().__setattr__("name", "winner")
                    or second.upload_files(
                        session_id,
                        [UploadData("Song.ttml", b"winning content")],
                    )
                )
                winner.result(timeout=2)
                with self.assertRaises(VersionConflictError):
                    loser.result(timeout=2)

            current = repository.load(session_id)
            upload = current.data["uploads"][0]
            self.assertEqual(artifacts.get_bytes(upload["artifact_key"]), b"winning content")

    def test_upload_preserves_normalized_ttml_collisions_as_pairing_issues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = SessionWorkflow(
                LocalJsonSessionRepository(root / "state"),
                FileArtifactStore(root / "artifacts"),
                MatchingApplication(MatchingEngine([])),
                work_root=root / "work",
            )
            session_id = workflow.create_session()

            pairing = workflow.upload_files(
                session_id,
                [
                    UploadData("Song.ttml", TTML.encode("utf-8")),
                    UploadData("song.ttml", TTML.encode("utf-8")),
                ],
            )

            self.assertEqual(pairing["issues"][0]["code"], "duplicate_ttml_key")
            with self.assertRaises(PairingConflictError):
                workflow.create_preview_job(session_id)

    def test_session_delete_can_retry_after_artifact_cleanup_failure(self):
        class FailingOnceArtifactStore(FileArtifactStore):
            def __init__(self, root):
                super().__init__(root)
                self.fail_next_cleanup = True

            def delete_prefix(self, prefix):
                if self.fail_next_cleanup and prefix.startswith("sessions/"):
                    self.fail_next_cleanup = False
                    raise OSError("temporary blob cleanup failure")
                return super().delete_prefix(prefix)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = LocalJsonSessionRepository(root / "state")
            workflow = SessionWorkflow(
                repository,
                FailingOnceArtifactStore(root / "artifacts"),
                MatchingApplication(MatchingEngine([])),
                work_root=root / "work",
            )
            session_id = workflow.create_session()
            workflow.upload_files(
                session_id,
                [UploadData("Song.ttml", TTML.encode("utf-8"))],
            )

            with self.assertRaisesRegex(OSError, "temporary blob cleanup failure"):
                workflow.delete_session(session_id)
            with self.assertRaises(SessionNotFoundError):
                workflow.upload_files(
                    session_id,
                    [UploadData("Other.ttml", TTML.encode("utf-8"))],
                )

            self.assertTrue(workflow.delete_session(session_id))
            self.assertFalse(workflow.delete_session(session_id))

    def test_deleting_a_session_cleans_artifacts_recreated_by_an_inflight_step(self):
        class BlockingDraftStore(FileArtifactStore):
            def __init__(self, root):
                super().__init__(root)
                self.block_draft = False
                self.draft_started = threading.Event()
                self.release_draft = threading.Event()
                self.fail_next_cleanup = False

            def put_json(self, key, payload):
                if self.block_draft and key.endswith("/draft.json"):
                    self.draft_started.set()
                    if not self.release_draft.wait(timeout=2):
                        raise TimeoutError("test did not release draft write")
                return super().put_json(key, payload)

            def delete_prefix(self, prefix):
                if self.fail_next_cleanup and prefix.startswith("sessions/"):
                    self.fail_next_cleanup = False
                    raise OSError("temporary abandoned cleanup failure")
                return super().delete_prefix(prefix)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts = BlockingDraftStore(root / "artifacts")
            repository = LocalJsonSessionRepository(root / "state")
            workflow = SessionWorkflow(
                repository,
                artifacts,
                MatchingApplication(MatchingEngine([])),
                work_root=root / "work",
            )
            session_id = workflow.create_session()
            workflow.upload_files(
                session_id,
                [UploadData("Song.ttml", TTML.encode("utf-8"))],
            )
            job = workflow.create_preview_job(session_id)
            artifacts.block_draft = True

            with ThreadPoolExecutor(max_workers=1) as executor:
                step = executor.submit(
                    workflow.step_preview_job,
                    session_id,
                    job["job_id"],
                    owner="inflight",
                )
                self.assertTrue(artifacts.draft_started.wait(timeout=1))
                self.assertTrue(workflow.delete_session(session_id))
                artifacts.fail_next_cleanup = True
                artifacts.release_draft.set()
                with self.assertRaises((JobBusyError, SessionNotFoundError)):
                    step.result(timeout=2)

            tombstone = repository.load(session_id)
            self.assertIsNotNone(tombstone)
            self.assertTrue(tombstone.data["deleting"])
            self.assertTrue(workflow.delete_session(session_id))
            self.assertIsNone(repository.load(session_id))
            self.assertFalse((root / "artifacts" / "sessions" / session_id).exists())

    def test_preview_step_reuses_persisted_result_after_session_cas_conflict(self):
        class ConflictOnceRepository(LocalJsonSessionRepository):
            def __init__(self, root):
                super().__init__(root)
                self.injected = False

            def save_with_job_lease(
                self,
                session_id,
                data,
                *,
                expected_version,
                job_id,
                owner,
            ):
                jobs = data.get("jobs", {})
                if not self.injected and any(
                    int(job.get("next_index", 0)) == 1
                    for job in jobs.values()
                ):
                    current = self.load(session_id)
                    competing = dict(current.data)
                    competing["concurrent_marker"] = True
                    super().save(
                        session_id,
                        competing,
                        expected_version=current.version,
                    )
                    self.injected = True
                return super().save_with_job_lease(
                    session_id,
                    data,
                    expected_version=expected_version,
                    job_id=job_id,
                    owner=owner,
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = ConflictOnceRepository(root / "state")
            client = PreviewQQClient()
            workflow = SessionWorkflow(
                repository,
                FileArtifactStore(root / "artifacts"),
                MatchingApplication(MatchingEngine([QQMusicSourceAdapter(client)])),
                work_root=root / "work",
            )
            session_id = workflow.create_session()
            workflow.upload_files(
                session_id,
                [UploadData("Song.ttml", TTML.encode("utf-8"))],
            )
            job = workflow.create_preview_job(session_id)

            with self.assertRaises(VersionConflictError):
                workflow.step_preview_job(
                    session_id,
                    job["job_id"],
                    owner="first-attempt",
                )
            completed = workflow.step_preview_job(
                session_id,
                job["job_id"],
                owner="retry",
            )

            self.assertEqual(completed["status"], "completed")
            self.assertEqual(len(completed["results"]), 1)
            self.assertEqual(client.calls, 1)

    def test_preview_step_cannot_commit_after_its_lease_is_stolen(self):
        class LeaseStealingRepository(LocalJsonSessionRepository):
            def __init__(self, root):
                super().__init__(root)
                self.steal_on_save = False

            def save_with_job_lease(
                self,
                session_id,
                data,
                *,
                expected_version,
                job_id,
                owner,
            ):
                if self.steal_on_save:
                    self.steal_on_save = False
                    self.release_job_lease(session_id, job_id, owner)
                    self.acquire_job_lease(
                        session_id,
                        job_id,
                        "thief",
                        ttl_seconds=60,
                    )
                return super().save_with_job_lease(
                    session_id,
                    data,
                    expected_version=expected_version,
                    job_id=job_id,
                    owner=owner,
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = LeaseStealingRepository(root / "state")
            client = PreviewQQClient()
            workflow = SessionWorkflow(
                repository,
                FileArtifactStore(root / "artifacts"),
                MatchingApplication(MatchingEngine([QQMusicSourceAdapter(client)])),
                work_root=root / "work",
            )
            session_id = workflow.create_session()
            workflow.upload_files(
                session_id,
                [UploadData("Song.ttml", TTML.encode("utf-8"))],
            )
            job = workflow.create_preview_job(session_id)
            repository.steal_on_save = True

            with self.assertRaises(JobBusyError):
                workflow.step_preview_job(
                    session_id,
                    job["job_id"],
                    owner="stale-worker",
                )

            self.assertEqual(
                workflow.get_preview_job(session_id, job["job_id"])["completed"],
                0,
            )
            repository.release_job_lease(session_id, job["job_id"], "thief")
            completed = workflow.step_preview_job(
                session_id,
                job["job_id"],
                owner="retry",
            )
            self.assertEqual(completed["completed"], 1)
            self.assertEqual(client.calls, 1)

    def test_apply_cas_conflict_cannot_replace_the_committed_output(self):
        class CoordinatedRepository(LocalJsonSessionRepository):
            def __init__(self, root):
                super().__init__(root)
                self.coordinate = False
                self.winner_saved = threading.Event()

            def save(self, session_id, data, *, expected_version):
                if self.coordinate and threading.current_thread().name == "winner":
                    saved = super().save(
                        session_id,
                        data,
                        expected_version=expected_version,
                    )
                    self.winner_saved.set()
                    return saved
                if self.coordinate and threading.current_thread().name == "loser":
                    if not self.winner_saved.wait(timeout=2):
                        raise TimeoutError("winning apply did not commit")
                return super().save(
                    session_id,
                    data,
                    expected_version=expected_version,
                )

        class CoordinatedArtifactStore(FileArtifactStore):
            def __init__(self, root, repository):
                super().__init__(root)
                self.repository = repository
                self.loser_waiting = threading.Event()

            def put_bytes(self, key, content):
                if (
                    self.repository.coordinate
                    and "/outputs/" in key
                    and threading.current_thread().name == "loser"
                ):
                    self.loser_waiting.set()
                    if not self.repository.winner_saved.wait(timeout=2):
                        raise TimeoutError("winning apply did not commit")
                return super().put_bytes(key, content)

        class TwoCandidateQQClient(PreviewQQClient):
            def search_songs(self, _query):
                self.calls += 1
                return [
                    QQMusicCandidate(
                        song_id="qq-winner",
                        mid="qq-mid-winner",
                        title="Song",
                        artists=["Artist"],
                        album="Album",
                    ),
                    QQMusicCandidate(
                        song_id="qq-loser",
                        mid="qq-mid-loser",
                        title="Song (Alternate)",
                        artists=["Artist"],
                        album="Album",
                    ),
                ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = CoordinatedRepository(root / "state")
            artifacts = CoordinatedArtifactStore(root / "artifacts", repository)
            application = MatchingApplication(
                MatchingEngine([QQMusicSourceAdapter(TwoCandidateQQClient())])
            )
            workflow = SessionWorkflow(
                repository,
                artifacts,
                application,
                work_root=root / "work-1",
            )
            second = SessionWorkflow(
                repository,
                artifacts,
                application,
                work_root=root / "work-2",
            )
            session_id = workflow.create_session()
            workflow.upload_files(
                session_id,
                [UploadData("Song.ttml", TTML.encode("utf-8"))],
            )
            job = workflow.create_preview_job(session_id)
            completed = workflow.step_preview_job(
                session_id,
                job["job_id"],
                owner="preview",
            )
            preview = completed["results"][0]
            winner_selection = _selection_from_preview(preview)
            source_key = next(iter(winner_selection.sources))
            loser_candidate = preview["match_result"]["sources"][source_key][
                "candidates"
            ][1]["id"]
            loser_selection = Selection(
                pair_id=winner_selection.pair_id,
                sources={source_key: (loser_candidate,)},
            )
            repository.coordinate = True

            with ThreadPoolExecutor(max_workers=2) as executor:
                loser = executor.submit(
                    lambda: threading.current_thread().__setattr__("name", "loser")
                    or workflow.apply(
                        session_id,
                        completed["snapshot_id"],
                        [loser_selection],
                    )
                )
                self.assertTrue(artifacts.loser_waiting.wait(timeout=1))
                winner = executor.submit(
                    lambda: threading.current_thread().__setattr__("name", "winner")
                    or second.apply(
                        session_id,
                        completed["snapshot_id"],
                        [winner_selection],
                    )
                )
                winner.result(timeout=2)
                with self.assertRaises(VersionConflictError):
                    loser.result(timeout=2)

            output = workflow.get_output(session_id, "Song.ttml")
            self.assertIn(b'key="qqMusicId" value="qq-winner"', output)
            self.assertNotIn(b'key="qqMusicId" value="qq-loser"', output)

    def test_cold_start_apply_uses_immutable_snapshot_without_querying_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository_root = root / "state"
            artifact_root = root / "artifacts"
            preview_client = PreviewQQClient()
            first = SessionWorkflow(
                LocalJsonSessionRepository(repository_root),
                FileArtifactStore(artifact_root),
                MatchingApplication(MatchingEngine([QQMusicSourceAdapter(preview_client)])),
                work_root=root / "work-1",
            )
            session_id = first.create_session()
            first.upload_files(session_id, [UploadData("Song.ttml", TTML.encode("utf-8"))])
            job = first.create_preview_job(session_id)

            completed = first.step_preview_job(session_id, job["job_id"], owner="worker-1")

            self.assertEqual(completed["status"], "completed")
            self.assertIsNotNone(completed["snapshot_id"])
            selection_data = completed["results"][0]["default_selection"]
            selection = Selection(
                pair_id=selection_data["pair_id"],
                sources={key: tuple(value) for key, value in selection_data["sources"].items()},
            )

            cold_client = PreviewQQClient(fail=True)
            second = SessionWorkflow(
                LocalJsonSessionRepository(repository_root),
                FileArtifactStore(artifact_root),
                MatchingApplication(MatchingEngine([QQMusicSourceAdapter(cold_client)])),
                work_root=root / "work-2",
            )

            applied = second.apply(session_id, completed["snapshot_id"], [selection])

            self.assertEqual(applied["succeeded"], 1)
            self.assertIsNone(applied["files"][0]["backup"])
            self.assertEqual(cold_client.calls, 0)
            output = second.get_output(session_id, "Song.ttml").decode("utf-8")
            self.assertIn('key="qqMusicId" value="qq-preview"', output)
            self.assertIn('key="qqMusicId" value="qq-mid-preview"', output)

    def test_vercel_cold_start_apply_uses_shared_kv_blob_and_zero_upstream_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            redis = FakeRedisJsonClient()
            blob = FakeBlobClient()
            preview_client = PreviewQQClient()
            first = SessionWorkflow(
                RedisSessionRepository(redis),
                VercelBlobArtifactStore(blob),
                MatchingApplication(MatchingEngine([QQMusicSourceAdapter(preview_client)])),
                work_root=root / "work-1",
            )
            session_id = first.create_session()
            first.upload_files(session_id, [UploadData("Song.ttml", TTML.encode())])
            job = first.create_preview_job(session_id)
            completed = first.step_preview_job(session_id, job["job_id"], owner="one")
            selection = _selection_from_preview(completed["results"][0])

            cold_client = PreviewQQClient(fail=True)
            second = SessionWorkflow(
                RedisSessionRepository(redis),
                VercelBlobArtifactStore(blob),
                MatchingApplication(MatchingEngine([QQMusicSourceAdapter(cold_client)])),
                work_root=root / "work-2",
            )
            result = second.apply(session_id, completed["snapshot_id"], [selection])

            self.assertEqual(result["succeeded"], 1)
            self.assertEqual(cold_client.calls, 0)
            self.assertIn(b'key="qqMusicId" value="qq-preview"', second.get_output(session_id, "Song.ttml"))

    def test_busy_lease_does_not_advance_the_same_pair_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = LocalJsonSessionRepository(root / "state")
            workflow = SessionWorkflow(
                repository,
                FileArtifactStore(root / "artifacts"),
                MatchingApplication(MatchingEngine([])),
                work_root=root / "work",
            )
            session_id = workflow.create_session()
            workflow.upload_files(session_id, [UploadData("Song.ttml", TTML.encode())])
            job = workflow.create_preview_job(session_id)
            self.assertTrue(repository.acquire_job_lease(session_id, job["job_id"], "holder", ttl_seconds=60))

            with self.assertRaises(JobBusyError):
                workflow.step_preview_job(session_id, job["job_id"], owner="contender")
            self.assertEqual(workflow.get_preview_job(session_id, job["job_id"])["completed"], 0)

            repository.release_job_lease(session_id, job["job_id"], "holder")
            self.assertEqual(
                workflow.step_preview_job(session_id, job["job_id"], owner="contender")["completed"],
                1,
            )

    def test_concurrent_step_calls_have_exactly_one_lease_holder(self):
        class BlockingApplication:
            def __init__(self, delegate):
                self.delegate = delegate
                self.started = threading.Event()
                self.release = threading.Event()

            def preview_pair(self, pair):
                self.started.set()
                if not self.release.wait(timeout=2):
                    raise TimeoutError("test did not release preview")
                return self.delegate.preview_pair(pair)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blocking = BlockingApplication(MatchingApplication(MatchingEngine([])))
            first = SessionWorkflow(
                LocalJsonSessionRepository(root / "state"),
                FileArtifactStore(root / "artifacts"),
                blocking,
                work_root=root / "work-1",
                lease_seconds=0.12,
            )
            second = SessionWorkflow(
                LocalJsonSessionRepository(root / "state"),
                FileArtifactStore(root / "artifacts"),
                blocking,
                work_root=root / "work-2",
                lease_seconds=0.12,
            )
            session_id = first.create_session()
            first.upload_files(session_id, [UploadData("Song.ttml", TTML.encode())])
            job = first.create_preview_job(session_id)

            with ThreadPoolExecutor(max_workers=1) as executor:
                winner = executor.submit(
                    first.step_preview_job,
                    session_id,
                    job["job_id"],
                    owner="winner",
                )
                self.assertTrue(blocking.started.wait(timeout=1))
                time.sleep(0.2)
                with self.assertRaises(JobBusyError):
                    second.step_preview_job(session_id, job["job_id"], owner="loser")
                blocking.release.set()
                completed = winner.result(timeout=2)

            self.assertEqual(completed["completed"], 1)
            self.assertEqual(len(completed["results"]), 1)

    def test_stale_snapshot_and_incomplete_selections_are_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = LocalJsonSessionRepository(root / "state")
            artifacts = FileArtifactStore(root / "artifacts")
            workflow = SessionWorkflow(
                repository,
                artifacts,
                MatchingApplication(MatchingEngine([])),
                work_root=root / "work",
            )
            session_id = workflow.create_session()
            workflow.upload_files(
                session_id,
                [UploadData("One.ttml", TTML.encode()), UploadData("Two.ttml", TTML.encode())],
            )
            job = workflow.create_preview_job(session_id)
            running = workflow.step_preview_job(session_id, job["job_id"], owner="one")
            completed = workflow.step_preview_job(session_id, job["job_id"], owner="two")
            self.assertEqual(running["status"], "running")
            self.assertEqual(completed["status"], "completed")

            first_selection = _selection_from_preview(completed["results"][0])
            with self.assertRaises(InvalidSelectionError):
                workflow.apply(session_id, completed["snapshot_id"], [first_selection])
            self.assertEqual(list((root / "artifacts").rglob("outputs")), [])

            current = repository.load(session_id)
            data = current.data
            data["uploads"][0]["sha256"] = "changed"
            repository.save(session_id, data, expected_version=current.version)
            with self.assertRaises(SnapshotConflictError):
                workflow.change_plan(session_id, completed["snapshot_id"], first_selection)

    def test_any_new_upload_invalidates_the_published_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = SessionWorkflow(
                LocalJsonSessionRepository(root / "state"),
                FileArtifactStore(root / "artifacts"),
                MatchingApplication(MatchingEngine([])),
                work_root=root / "work",
            )
            session_id = workflow.create_session()
            workflow.upload_files(
                session_id,
                [UploadData("Song.ttml", TTML.encode("utf-8"))],
            )
            job = workflow.create_preview_job(session_id)
            completed = workflow.step_preview_job(
                session_id,
                job["job_id"],
                owner="preview",
            )
            selection = _selection_from_preview(completed["results"][0])

            workflow.upload_files(
                session_id,
                [UploadData("unpaired.mp3", b"new upload")],
            )

            with self.assertRaises(SnapshotConflictError):
                workflow.apply(
                    session_id,
                    completed["snapshot_id"],
                    [selection],
                )

    def test_ambiguous_audio_blocks_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = SessionWorkflow(
                LocalJsonSessionRepository(root / "state"),
                FileArtifactStore(root / "artifacts"),
                MatchingApplication(MatchingEngine([])),
                work_root=root / "work",
            )
            session_id = workflow.create_session()
            pairing = workflow.upload_files(
                session_id,
                [
                    UploadData("Song.ttml", TTML.encode()),
                    UploadData("Song.mp3", b"one"),
                    UploadData("song.wav", b"two"),
                ],
            )
            self.assertEqual(pairing["issues"][0]["code"], "ambiguous_audio")
            with self.assertRaises(PairingConflictError):
                workflow.create_preview_job(session_id)

    def test_snapshot_publication_failure_marks_the_job_failed(self):
        class FailingSnapshotStore(FileArtifactStore):
            def put_json(self, key, payload):
                if "/snapshots/" in key:
                    raise OSError("blob publication failed")
                return super().put_json(key, payload)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = SessionWorkflow(
                LocalJsonSessionRepository(root / "state"),
                FailingSnapshotStore(root / "artifacts"),
                MatchingApplication(MatchingEngine([])),
                work_root=root / "work",
            )
            session_id = workflow.create_session()
            workflow.upload_files(session_id, [UploadData("Song.ttml", TTML.encode())])
            job = workflow.create_preview_job(session_id)

            failed = workflow.step_preview_job(session_id, job["job_id"], owner="worker")

            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["errors"][-1]["code"], "snapshot_publish_failed")
            self.assertIsNone(failed["snapshot_id"])


def _selection_from_preview(preview):
    data = preview["default_selection"]
    return Selection(
        pair_id=data["pair_id"],
        sources={key: tuple(value) for key, value in data["sources"].items()},
    )


if __name__ == "__main__":
    unittest.main()
