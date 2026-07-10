from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from server.v2.storage import (
    ArtifactStore,
    FileArtifactStore,
    InvalidArtifactKeyError,
    LeaseConflictError,
    LocalJsonSessionRepository,
    SessionRepository,
    VersionConflictError,
)


class LocalJsonSessionRepositoryTests(unittest.TestCase):
    def test_rejects_session_and_job_identifiers_that_can_escape_storage_seams(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = LocalJsonSessionRepository(Path(tmp))
            with self.assertRaisesRegex(ValueError, "invalid session id"):
                repository.load("../escape")
            repository.create({}, session_id="session-1")
            with self.assertRaisesRegex(ValueError, "invalid job id"):
                repository.acquire_job_lease(
                    "session-1",
                    "../job",
                    "owner",
                    ttl_seconds=10,
                )

    def test_created_session_can_be_loaded_by_a_fresh_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = LocalJsonSessionRepository(root).create(
                {"status": "uploading", "pairs": []},
                session_id="session-1",
            )

            loaded = LocalJsonSessionRepository(root).load("session-1")

        self.assertEqual(created.session_id, "session-1")
        self.assertEqual(created.version, 1)
        self.assertEqual(loaded, created)

    def test_save_uses_version_compare_and_swap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = LocalJsonSessionRepository(Path(tmp))
            repository.create({"status": "created"}, session_id="session-1")

            saved = repository.save(
                "session-1",
                {"status": "previewing"},
                expected_version=1,
            )

            self.assertEqual(saved.version, 2)
            self.assertEqual(saved.data, {"status": "previewing"})
            with self.assertRaises(VersionConflictError):
                repository.save(
                    "session-1",
                    {"status": "stale-write"},
                    expected_version=1,
                )
            self.assertEqual(repository.load("session-1"), saved)

    def test_concurrent_saves_from_the_same_version_have_one_winner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            LocalJsonSessionRepository(root).create({}, session_id="session-1")
            repositories = (
                LocalJsonSessionRepository(root),
                LocalJsonSessionRepository(root),
            )
            barrier = Barrier(2)

            def save(index: int) -> str:
                barrier.wait()
                try:
                    repositories[index].save(
                        "session-1",
                        {"winner": index},
                        expected_version=1,
                    )
                except VersionConflictError:
                    return "conflict"
                return "saved"

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(save, (0, 1)))

            loaded = LocalJsonSessionRepository(root).load("session-1")

        self.assertEqual(sorted(results), ["conflict", "saved"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.version, 2)

    def test_delete_can_reject_a_stale_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = LocalJsonSessionRepository(Path(tmp))
            repository.create({"status": "created"}, session_id="session-1")
            saved = repository.save(
                "session-1",
                {"status": "ready"},
                expected_version=1,
            )

            with self.assertRaises(VersionConflictError):
                repository.delete("session-1", expected_version=1)

            self.assertEqual(repository.load("session-1"), saved)
            self.assertTrue(repository.delete("session-1", expected_version=2))
            self.assertIsNone(repository.load("session-1"))
            self.assertFalse(repository.delete("session-1"))

    def test_delete_removes_leases_before_the_session_id_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = LocalJsonSessionRepository(Path(tmp))
            repository.create({}, session_id="session-1")
            repository.acquire_job_lease(
                "session-1",
                "job-1",
                "old-worker",
                ttl_seconds=100,
                now=100,
            )

            repository.delete("session-1")
            repository.create({}, session_id="session-1")

            self.assertTrue(
                repository.acquire_job_lease(
                    "session-1",
                    "job-1",
                    "new-worker",
                    ttl_seconds=10,
                    now=101,
                )
            )

    def test_create_does_not_overwrite_an_existing_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = LocalJsonSessionRepository(Path(tmp))
            created = repository.create({"owner": "first"}, session_id="session-1")

            with self.assertRaises(VersionConflictError):
                repository.create({"owner": "second"}, session_id="session-1")

            self.assertEqual(repository.load("session-1"), created)

    def test_job_lease_is_exclusive_until_released_or_expired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = LocalJsonSessionRepository(root)
            second = LocalJsonSessionRepository(root)
            first.create({"jobs": {"job-1": {}}}, session_id="session-1")

            self.assertTrue(
                first.acquire_job_lease(
                    "session-1",
                    "job-1",
                    "worker-a",
                    ttl_seconds=10,
                    now=100,
                )
            )
            self.assertFalse(
                second.acquire_job_lease(
                    "session-1",
                    "job-1",
                    "worker-b",
                    ttl_seconds=10,
                    now=105,
                )
            )
            self.assertTrue(
                second.acquire_job_lease(
                    "session-1",
                    "job-1",
                    "worker-b",
                    ttl_seconds=10,
                    now=111,
                )
            )
            self.assertFalse(
                first.release_job_lease("session-1", "job-1", "worker-a")
            )
            self.assertTrue(
                second.release_job_lease("session-1", "job-1", "worker-b")
            )
            self.assertTrue(
                first.acquire_job_lease(
                    "session-1",
                    "job-1",
                    "worker-c",
                    ttl_seconds=10,
                    now=112,
                )
            )

    def test_concurrent_job_lease_attempts_have_one_winner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            LocalJsonSessionRepository(root).create({}, session_id="session-1")
            repositories = (
                LocalJsonSessionRepository(root),
                LocalJsonSessionRepository(root),
            )
            barrier = Barrier(2)

            def acquire(index: int) -> bool:
                barrier.wait()
                return repositories[index].acquire_job_lease(
                    "session-1",
                    "job-1",
                    f"worker-{index}",
                    ttl_seconds=30,
                    now=100,
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(acquire, (0, 1)))

        self.assertEqual(sorted(results), [False, True])

    def test_job_step_save_requires_the_current_lease_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = LocalJsonSessionRepository(Path(tmp))
            repository.create({"jobs": {"job-1": {}}}, session_id="session-1")
            self.assertTrue(
                repository.acquire_job_lease(
                    "session-1",
                    "job-1",
                    "worker-a",
                    ttl_seconds=30,
                )
            )

            with self.assertRaises(LeaseConflictError):
                repository.save_with_job_lease(
                    "session-1",
                    {"winner": "worker-b"},
                    expected_version=1,
                    job_id="job-1",
                    owner="worker-b",
                )

            saved = repository.save_with_job_lease(
                "session-1",
                {"winner": "worker-a"},
                expected_version=1,
                job_id="job-1",
                owner="worker-a",
            )
            self.assertEqual(saved.data, {"winner": "worker-a"})


class FileArtifactStoreTests(unittest.TestCase):
    def test_bytes_can_be_read_by_a_fresh_store_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = FileArtifactStore(root)

            key = first.put_bytes(
                "sessions/session-1/uploads/Song.ttml",
                b"<tt>\xe6\xad\x8c\xe8\xaf\x8d</tt>",
            )
            loaded = FileArtifactStore(root).get_bytes(key)

        self.assertEqual(key, "sessions/session-1/uploads/Song.ttml")
        self.assertEqual(loaded, b"<tt>\xe6\xad\x8c\xe8\xaf\x8d</tt>")

    def test_snapshot_json_round_trips_as_an_artifact(self) -> None:
        snapshot = {
            "snapshot_id": "snapshot-1",
            "pairs": [
                {
                    "pair_id": "pair-1",
                    "sources": {"apple_music": {"warnings": ["地区不可用"]}},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key = FileArtifactStore(root).put_json(
                "sessions/session-1/snapshots/snapshot-1.json",
                snapshot,
            )

            loaded = FileArtifactStore(root).get_json(key)

        self.assertEqual(loaded, snapshot)

    def test_artifact_keys_cannot_escape_or_ambiguously_address_the_root(self) -> None:
        invalid_keys = (
            "",
            "../outside.json",
            "/absolute.json",
            "C:/absolute.json",
            "sessions\\session-1\\snapshot.json",
            "sessions//snapshot.json",
            "sessions/./snapshot.json",
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = FileArtifactStore(Path(tmp))

            for key in invalid_keys:
                with self.subTest(key=key):
                    with self.assertRaises(InvalidArtifactKeyError):
                        store.put_bytes(key, b"unsafe")

    def test_artifacts_can_be_deleted_individually_or_by_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileArtifactStore(Path(tmp))
            store.put_json("sessions/session-1/snapshots/one.json", {"id": "one"})
            store.put_json("sessions/session-1/snapshots/two.json", {"id": "two"})
            store.put_bytes("sessions/session-1/outputs/Song.ttml", b"output")

            self.assertTrue(
                store.delete("sessions/session-1/snapshots/one.json")
            )
            self.assertFalse(
                store.delete("sessions/session-1/snapshots/one.json")
            )
            self.assertEqual(
                store.delete_prefix("sessions/session-1/snapshots"),
                1,
            )
            self.assertEqual(
                store.get_bytes("sessions/session-1/outputs/Song.ttml"),
                b"output",
            )


class StorageProtocolTests(unittest.TestCase):
    def test_local_adapters_implement_the_public_storage_protocols(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            self.assertIsInstance(
                LocalJsonSessionRepository(root / "state"),
                SessionRepository,
            )
            self.assertIsInstance(
                FileArtifactStore(root / "artifacts"),
                ArtifactStore,
            )


if __name__ == "__main__":
    unittest.main()
