from __future__ import annotations

import copy
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any
from types import SimpleNamespace
from unittest.mock import patch

from server.v2.vercel_storage import (
    ConditionalMutation,
    RedisJsonClient,
    RedisSessionRepository,
    SdkVercelBlobClient,
    UpstashRestRedisClient,
    VercelBlobClient,
    VercelBlobArtifactStore,
)
from server.v2.storage import (
    ArtifactStore,
    InvalidArtifactKeyError,
    LeaseConflictError,
    RateLimitResult,
    SessionRepository,
    VersionConflictError,
)


class FakeRedisJsonClient:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any] | str] = {}
        self.expirations: dict[str, float] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}
        self.now = 0.0
        self._lock = threading.Lock()

    def get_json(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            self._expire(key)
            value = self.values.get(key)
            return copy.deepcopy(value) if isinstance(value, dict) else None

    def create_json(self, key: str, value: dict[str, Any]) -> bool:
        with self._lock:
            self._expire(key)
            if key in self.values:
                return False
            self.values[key] = copy.deepcopy(value)
            return True

    def compare_and_set_json(
        self,
        key: str,
        value: dict[str, Any],
        *,
        expected_version: int,
    ) -> ConditionalMutation:
        with self._lock:
            self._expire(key)
            current = self.values.get(key)
            if not isinstance(current, dict):
                return ConditionalMutation.MISSING
            if current.get("version") != expected_version:
                return ConditionalMutation.CONFLICT
            self.values[key] = copy.deepcopy(value)
            return ConditionalMutation.APPLIED

    def compare_and_set_json_with_lease(
        self,
        key: str,
        value: dict[str, Any],
        *,
        expected_version: int,
        lease_key: str,
        expected_owner: str,
    ) -> ConditionalMutation:
        with self._lock:
            self._expire(key)
            self._expire(lease_key)
            current = self.values.get(key)
            if not isinstance(current, dict):
                return ConditionalMutation.MISSING
            if current.get("version") != expected_version:
                return ConditionalMutation.CONFLICT
            if self.values.get(lease_key) != expected_owner:
                return ConditionalMutation.LEASE_CONFLICT
            self.values[key] = copy.deepcopy(value)
            return ConditionalMutation.APPLIED

    def delete_json(
        self,
        key: str,
        *,
        expected_version: int | None = None,
    ) -> ConditionalMutation:
        with self._lock:
            self._expire(key)
            current = self.values.get(key)
            if not isinstance(current, dict):
                return ConditionalMutation.MISSING
            if expected_version is not None and current.get("version") != expected_version:
                return ConditionalMutation.CONFLICT
            del self.values[key]
            self.expirations.pop(key, None)
            return ConditionalMutation.APPLIED

    def set_nx_ex(
        self,
        key: str,
        value: str,
        *,
        ttl_seconds: float,
        now: float | None = None,
    ) -> bool:
        with self._lock:
            if now is not None:
                self.now = now
            self._expire(key)
            if key in self.values:
                return False
            self.values[key] = value
            self.expirations[key] = self.now + ttl_seconds
            return True

    def compare_and_delete(self, key: str, expected_value: str) -> bool:
        with self._lock:
            self._expire(key)
            if self.values.get(key) != expected_value:
                return False
            del self.values[key]
            self.expirations.pop(key, None)
            return True

    def compare_and_expire(self, key: str, expected_value: str, *, ttl_seconds: float) -> bool:
        with self._lock:
            self._expire(key)
            if self.values.get(key) != expected_value:
                return False
            self.expirations[key] = self.now + ttl_seconds
            return True

    def expire(self, key: str, *, ttl_seconds: float) -> bool:
        with self._lock:
            self._expire(key)
            if key not in self.values:
                return False
            self.expirations[key] = self.now + ttl_seconds
            return True

    def sorted_set_add(self, key: str, member: str, score: float) -> None:
        self.sorted_sets.setdefault(key, {})[member] = float(score)

    def sorted_set_range_by_score(
        self,
        key: str,
        *,
        maximum: float,
        limit: int,
    ) -> list[str]:
        values = self.sorted_sets.get(key, {})
        return [
            member
            for member, score in sorted(values.items(), key=lambda item: (item[1], item[0]))
            if score <= maximum
        ][:limit]

    def sorted_set_remove(self, key: str, member: str) -> None:
        self.sorted_sets.get(key, {}).pop(member, None)

    def consume_fixed_window(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> RateLimitResult:
        with self._lock:
            self._expire(key)
            count = int(self.values.get(key, 0)) + 1
            self.values[key] = str(count)
            self.expirations.setdefault(key, self.now + window_seconds)
            retry_after = max(1, int(self.expirations[key] - self.now + 0.999))
            return RateLimitResult(count <= limit, count, retry_after)

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def _expire(self, key: str) -> None:
        expires_at = self.expirations.get(key)
        if expires_at is not None and expires_at <= self.now:
            self.values.pop(key, None)
            self.expirations.pop(key, None)


class FakeBlobClient:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, pathname: str, content: bytes) -> None:
        self.objects[pathname] = bytes(content)

    def get_bytes(self, pathname: str) -> bytes:
        return self.objects[pathname]

    def put_file(self, pathname: str, source: Path) -> None:
        self.objects[pathname] = Path(source).read_bytes()

    def get_file(self, pathname: str, destination: Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.objects[pathname])
        return destination

    def delete(self, pathname: str) -> bool:
        return self.objects.pop(pathname, None) is not None

    def delete_prefix(self, prefix: str) -> int:
        matches = [key for key in self.objects if key.startswith(prefix)]
        for key in matches:
            del self.objects[key]
        return len(matches)


class RedisSessionRepositoryTests(unittest.TestCase):
    def test_session_expiry_index_and_rate_limit_are_shared_in_redis(self) -> None:
        redis = FakeRedisJsonClient()
        repository = RedisSessionRepository(redis)
        repository.create({"expires_at": 110}, session_id="session-1")

        repository.register_expiry(
            "session-1",
            expires_at=110,
            ttl_seconds=20,
        )
        first = repository.consume_rate_limit(
            "requests:client",
            limit=1,
            window_seconds=60,
        )
        second = RedisSessionRepository(redis).consume_rate_limit(
            "requests:client",
            limit=1,
            window_seconds=60,
        )

        self.assertEqual(repository.list_expired(before=109, limit=10), [])
        self.assertEqual(repository.list_expired(before=110, limit=10), ["session-1"])
        self.assertTrue(first.allowed)
        self.assertFalse(second.allowed)
        repository.remove_expiry("session-1")
        self.assertEqual(repository.list_expired(before=110, limit=10), [])

    def test_rejects_unsafe_identifiers_before_building_redis_keys(self) -> None:
        repository = RedisSessionRepository(FakeRedisJsonClient())
        with self.assertRaisesRegex(ValueError, "invalid session id"):
            repository.load("../escape")

    def test_created_session_loads_after_a_cold_start(self) -> None:
        redis = FakeRedisJsonClient()
        created = RedisSessionRepository(redis).create(
            {"status": "uploading"},
            session_id="session-1",
        )

        loaded = RedisSessionRepository(redis).load("session-1")

        self.assertEqual(loaded, created)
        self.assertEqual(
            list(redis.values),
            ["id-match:v2:sessions:session-1"],
        )

    def test_compare_and_swap_has_one_winner_across_repository_instances(self) -> None:
        redis = FakeRedisJsonClient()
        RedisSessionRepository(redis).create({}, session_id="session-1")
        repositories = (RedisSessionRepository(redis), RedisSessionRepository(redis))
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

        loaded = RedisSessionRepository(redis).load("session-1")
        self.assertEqual(sorted(results), ["conflict", "saved"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.version, 2)

    def test_expired_job_lease_can_be_taken_over_but_only_owner_can_release(self) -> None:
        redis = FakeRedisJsonClient()
        first = RedisSessionRepository(redis)
        second = RedisSessionRepository(redis)
        first.create({"jobs": {"job-1": {}}}, session_id="session-1")

        self.assertTrue(
            first.acquire_job_lease(
                "session-1", "job-1", "worker-a", ttl_seconds=10, now=100
            )
        )
        self.assertIn(
            "id-match:v2:sessions:session-1:jobs:job-1:lease",
            redis.values,
        )
        self.assertFalse(
            second.acquire_job_lease(
                "session-1", "job-1", "worker-b", ttl_seconds=10, now=105
            )
        )
        self.assertTrue(
            second.acquire_job_lease(
                "session-1", "job-1", "worker-b", ttl_seconds=10, now=111
            )
        )
        self.assertFalse(
            first.release_job_lease("session-1", "job-1", "worker-a")
        )
        self.assertTrue(
            second.release_job_lease("session-1", "job-1", "worker-b")
        )

    def test_job_lease_renewal_requires_the_current_owner(self) -> None:
        redis = FakeRedisJsonClient()
        repository = RedisSessionRepository(redis)
        repository.create({}, session_id="session-1")
        self.assertTrue(repository.acquire_job_lease(
            "session-1", "job-1", "worker-a", ttl_seconds=10, now=100
        ))

        self.assertFalse(repository.renew_job_lease(
            "session-1", "job-1", "worker-b", ttl_seconds=10
        ))
        self.assertTrue(repository.renew_job_lease(
            "session-1", "job-1", "worker-a", ttl_seconds=10
        ))

    def test_job_step_save_requires_the_current_redis_lease_owner(self) -> None:
        redis = FakeRedisJsonClient()
        repository = RedisSessionRepository(redis)
        repository.create({}, session_id="session-1")
        self.assertTrue(repository.acquire_job_lease(
            "session-1", "job-1", "worker-a", ttl_seconds=10, now=100
        ))

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

    def test_delete_optionally_rejects_a_stale_version(self) -> None:
        redis = FakeRedisJsonClient()
        repository = RedisSessionRepository(redis)
        repository.create({}, session_id="session-1")
        repository.save("session-1", {"ready": True}, expected_version=1)

        with self.assertRaises(VersionConflictError):
            repository.delete("session-1", expected_version=1)

        self.assertTrue(repository.delete("session-1", expected_version=2))
        self.assertFalse(repository.delete("session-1"))


class VercelBlobArtifactStoreTests(unittest.TestCase):
    def test_file_upload_and_download_do_not_require_byte_buffers(self) -> None:
        blob = FakeBlobClient()
        store = VercelBlobArtifactStore(blob)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.ttml"
            destination = root / "downloaded.ttml"
            source.write_bytes(b"streamed")

            key = store.put_file("sessions/session-1/uploads/one.ttml", source)
            result = store.get_file(key, destination)

            self.assertEqual(result, destination)
            self.assertEqual(destination.read_bytes(), b"streamed")

    def test_sdk_facade_matches_vercel_0_5_result_shapes(self) -> None:
        fake_sdk = SimpleNamespace(
            upload_file=lambda *args, **kwargs: None,
            get=lambda *args, **kwargs: SimpleNamespace(content=b"payload"),
            delete=lambda *args, **kwargs: None,
            list_objects=lambda **kwargs: SimpleNamespace(
                blobs=[], cursor=None, has_more=False
            ),
        )
        with patch("server.v2.vercel_storage.importlib.import_module", return_value=fake_sdk):
            client = SdkVercelBlobClient("token")

        self.assertEqual(client.get_bytes("sessions/one/file"), b"payload")
        self.assertEqual(client.delete_prefix("sessions/one"), 0)

    def test_snapshot_json_round_trips_after_a_cold_start(self) -> None:
        blob = FakeBlobClient()
        snapshot = {
            "snapshot_id": "snapshot-1",
            "pairs": [{"pair_id": "pair-1", "sources": {}}],
        }
        key = "sessions/session-1/snapshots/snapshot-1.json"

        stored_key = VercelBlobArtifactStore(blob).put_json(key, snapshot)
        loaded = VercelBlobArtifactStore(blob).get_json(stored_key)

        self.assertEqual(stored_key, key)
        self.assertEqual(loaded, snapshot)
        self.assertIn(f"id-match/v2/{key}", blob.objects)

    def test_delete_prefix_is_scoped_to_one_session_namespace(self) -> None:
        blob = FakeBlobClient()
        store = VercelBlobArtifactStore(blob)
        store.put_json(
            "sessions/session-1/snapshots/one.json",
            {"id": "one"},
        )
        store.put_json(
            "sessions/session-1/snapshots/two.json",
            {"id": "two"},
        )
        store.put_json(
            "sessions/session-2/snapshots/keep.json",
            {"id": "keep"},
        )

        deleted = store.delete_prefix("sessions/session-1/snapshots")

        self.assertEqual(deleted, 2)
        self.assertEqual(
            store.get_json("sessions/session-2/snapshots/keep.json"),
            {"id": "keep"},
        )

    def test_artifact_keys_must_include_a_session_namespace(self) -> None:
        store = VercelBlobArtifactStore(FakeBlobClient())

        for key in ("snapshots/one.json", "sessions//one.json", "../one.json"):
            with self.subTest(key=key):
                with self.assertRaises(InvalidArtifactKeyError):
                    store.put_bytes(key, b"unsafe")

    def test_sdk_client_lists_then_deletes_each_blob_in_a_prefix(self) -> None:
        objects: dict[str, bytes] = {}

        def upload_file(local_path: str, pathname: str, **kwargs: Any) -> None:
            self.assertEqual(kwargs["token"], "blob-token")
            objects[pathname] = Path(local_path).read_bytes()

        def get_blob(pathname: str, **kwargs: Any) -> bytes:
            return objects[pathname]

        def delete_blob(pathname: str, **kwargs: Any) -> None:
            objects.pop(pathname, None)

        def list_blobs(**kwargs: Any) -> dict[str, Any]:
            prefix = kwargs["prefix"]
            start = int(kwargs.get("cursor") or 0)
            matches = [key for key in sorted(objects) if key.startswith(prefix)]
            page = matches[start : start + 1]
            next_index = start + len(page)
            return {
                "blobs": [
                    {"pathname": key}
                    for key in page
                ],
                "cursor": str(next_index),
                "has_more": next_index < len(matches),
            }

        client = SdkVercelBlobClient(
            "blob-token",
            upload_file=upload_file,
            get_blob=get_blob,
            delete_blob=delete_blob,
            list_blobs=list_blobs,
        )
        store = VercelBlobArtifactStore(client)
        store.put_bytes("sessions/session-1/outputs/one.ttml", b"one")
        store.put_bytes("sessions/session-1/outputs/two.ttml", b"two")
        store.put_bytes("sessions/session-2/outputs/keep.ttml", b"keep")

        deleted = store.delete_prefix("sessions/session-1/outputs")

        self.assertEqual(deleted, 2)
        self.assertEqual(
            store.get_bytes("sessions/session-2/outputs/keep.ttml"),
            b"keep",
        )


class UpstashRestRedisClientTests(unittest.TestCase):
    def test_expiry_index_and_fixed_window_commands_use_rest_redis(self) -> None:
        commands: list[list[Any]] = []
        responses = iter((
            b'{"result":1}',
            b'{"result":1}',
            b'{"result":["session-1"]}',
            b'{"result":1}',
            b'{"result":[2,30]}',
        ))

        def requester(request: Any, _timeout: float) -> bytes:
            commands.append(json.loads(request.data.decode("utf-8")))
            return next(responses)

        client = UpstashRestRedisClient(
            "https://redis.example",
            "secret-token",
            requester=requester,
        )

        self.assertTrue(client.expire("session-key", ttl_seconds=20))
        client.sorted_set_add("expiry-key", "session-1", 110)
        self.assertEqual(
            client.sorted_set_range_by_score("expiry-key", maximum=110, limit=5),
            ["session-1"],
        )
        client.sorted_set_remove("expiry-key", "session-1")
        limited = client.consume_fixed_window(
            "rate-key",
            limit=1,
            window_seconds=60,
        )

        self.assertFalse(limited.allowed)
        self.assertEqual(commands[0], ["EXPIRE", "session-key", 20])
        self.assertEqual(commands[1], ["ZADD", "expiry-key", 110, "session-1"])
        self.assertEqual(commands[2][0:4], ["ZRANGEBYSCORE", "expiry-key", "-inf", 110])
        self.assertEqual(commands[3], ["ZREM", "expiry-key", "session-1"])
        self.assertEqual(commands[4][0], "EVAL")

    def test_lease_fenced_cas_checks_session_and_lease_in_one_script(self) -> None:
        commands: list[list[Any]] = []
        responses = iter((b'{"result":1}', b'{"result":-2}'))

        def requester(request: Any, _timeout: float) -> bytes:
            commands.append(json.loads(request.data.decode("utf-8")))
            return next(responses)

        client = UpstashRestRedisClient(
            "https://redis.example",
            "secret-token",
            requester=requester,
        )
        record = {"session_id": "session-1", "version": 2, "data": {}}

        self.assertEqual(
            client.compare_and_set_json_with_lease(
                "session-key",
                record,
                expected_version=1,
                lease_key="lease-key",
                expected_owner="worker-a",
            ),
            ConditionalMutation.APPLIED,
        )
        self.assertEqual(
            client.compare_and_set_json_with_lease(
                "session-key",
                record,
                expected_version=1,
                lease_key="lease-key",
                expected_owner="worker-b",
            ),
            ConditionalMutation.LEASE_CONFLICT,
        )
        self.assertEqual(commands[0][0], "EVAL")
        self.assertEqual(commands[0][2:7], [2, "session-key", "lease-key", 1, "worker-a"])
        self.assertIn("PTTL", commands[0][1])
        self.assertIn("math.max(ttl, 1)", commands[0][1])

    def test_atomic_operations_use_rest_command_json_without_network(self) -> None:
        commands: list[list[Any]] = []
        responses = iter(
            (
                b'{"result":"OK"}',
                b'{"result":1}',
                b'{"result":0}',
                b'{"result":1}',
                b'{"result":"OK"}',
                b'{"result":1}',
                b'{"result":1}',
            )
        )

        def requester(request: Any, timeout: float) -> bytes:
            self.assertEqual(timeout, 3)
            commands.append(json.loads(request.data.decode("utf-8")))
            return next(responses)

        client = UpstashRestRedisClient(
            "https://redis.example",
            "secret-token",
            timeout_seconds=3,
            requester=requester,
        )
        record = {"session_id": "session-1", "version": 1, "data": {}}

        self.assertTrue(client.create_json("session-key", record))
        self.assertEqual(
            client.compare_and_set_json(
                "session-key",
                {**record, "version": 2},
                expected_version=1,
            ),
            ConditionalMutation.APPLIED,
        )
        self.assertEqual(
            client.delete_json("session-key", expected_version=1),
            ConditionalMutation.CONFLICT,
        )
        self.assertEqual(
            client.delete_json("session-key"),
            ConditionalMutation.APPLIED,
        )
        self.assertTrue(
            client.set_nx_ex("lease-key", "worker-a", ttl_seconds=2.2)
        )
        self.assertTrue(client.compare_and_delete("lease-key", "worker-a"))
        self.assertTrue(client.compare_and_expire(
            "lease-key", "worker-a", ttl_seconds=2.2
        ))

        self.assertEqual(commands[0][0:2], ["SET", "session-key"])
        self.assertEqual(commands[0][-1], "NX")
        self.assertEqual(commands[1][0], "EVAL")
        self.assertEqual(commands[1][3], "session-key")
        self.assertEqual(commands[2][0], "EVAL")
        self.assertEqual(commands[3], ["DEL", "session-key"])
        self.assertEqual(
            commands[4],
            ["SET", "lease-key", "worker-a", "NX", "EX", 3],
        )
        self.assertEqual(commands[5][0], "EVAL")
        self.assertEqual(commands[6][0], "EVAL")
        self.assertEqual(commands[6][-1], 3)


class VercelStorageProtocolTests(unittest.TestCase):
    def test_adapters_implement_v2_storage_and_client_protocols(self) -> None:
        redis = FakeRedisJsonClient()
        blob = FakeBlobClient()

        self.assertIsInstance(redis, RedisJsonClient)
        self.assertIsInstance(blob, VercelBlobClient)
        self.assertIsInstance(RedisSessionRepository(redis), SessionRepository)
        self.assertIsInstance(VercelBlobArtifactStore(blob), ArtifactStore)


if __name__ == "__main__":
    unittest.main()
