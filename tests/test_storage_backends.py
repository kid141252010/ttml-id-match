import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.services.storage import (
    BlobArtifactRecord,
    LocalArtifactStore,
    StoredSession,
    VercelArtifactStore,
    build_session_store,
)


class StorageBackendTests(unittest.TestCase):
    def test_local_artifact_store_preserves_existing_directory_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalArtifactStore(Path(tmp))
            session = store.create_session()

            self.assertTrue(session.upload_dir.is_dir())
            self.assertTrue(session.output_dir.is_dir())
            self.assertEqual(session.root.parent, Path(tmp))

            loaded = store.get_session(session.session_id)

        self.assertEqual(loaded.session_id, session.session_id)
        self.assertIsInstance(loaded, StoredSession)

    def test_build_session_store_defaults_to_local(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=True):
            store = build_session_store(Path(tmp))

            self.assertIsInstance(store, LocalArtifactStore)

    def test_build_session_store_rejects_vercel_without_required_blob_and_kv_environment(self):
        with patch.dict("os.environ", {"ID_MATCH_STORAGE_BACKEND": "vercel"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "BLOB_READ_WRITE_TOKEN"):
                build_session_store()

    def test_vercel_artifact_store_syncs_session_files_and_rehydrates_from_remote_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blob = FakeBlobStore()
            kv = FakeKeyValueStore()
            store = VercelArtifactStore(
                root,
                blob_token="blob-token",
                kv_rest_api_url="https://kv.example",
                kv_rest_api_token="kv-token",
                blob_store=blob,
                key_value_store=kv,
            )
            session = store.create_session()
            (session.upload_dir / "Song.ttml").write_text("<tt />", encoding="utf-8")
            (session.output_dir / "Song.ttml").write_text("<tt done=\"1\" />", encoding="utf-8")
            session.pairs = [{"id": "pair-1", "ttml": "Song.ttml", "audio": None, "status": "ttml_only"}]

            store.sync_session(session)
            store = VercelArtifactStore(
                root / "second",
                blob_token="blob-token",
                kv_rest_api_url="https://kv.example",
                kv_rest_api_token="kv-token",
                blob_store=blob,
                key_value_store=kv,
            )
            loaded = store.get_session(session.session_id)

            self.assertEqual((loaded.upload_dir / "Song.ttml").read_text(encoding="utf-8"), "<tt />")
            self.assertEqual((loaded.output_dir / "Song.ttml").read_text(encoding="utf-8"), '<tt done="1" />')
            self.assertEqual(loaded.pairs[0]["id"], "pair-1")

    def test_vercel_artifact_store_reuses_unchanged_blob_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blob = FakeBlobStore()
            kv = FakeKeyValueStore()
            store = VercelArtifactStore(
                root,
                blob_token="blob-token",
                kv_rest_api_url="https://kv.example",
                kv_rest_api_token="kv-token",
                blob_store=blob,
                key_value_store=kv,
            )
            session = store.create_session()
            (session.upload_dir / "Song.ttml").write_text("<tt />", encoding="utf-8")
            (session.output_dir / "Song.ttml").write_text("<tt done=\"1\" />", encoding="utf-8")

            store.sync_session(session)
            first_uploads = list(blob.put_calls)
            store.sync_session(session)

            self.assertEqual(blob.put_calls, first_uploads)

            (session.output_dir / "Song.ttml").write_text("<tt done=\"2\" />", encoding="utf-8")
            store.sync_session(session)

            self.assertEqual(len(blob.put_calls), len(first_uploads) + 1)
            self.assertEqual(blob.put_calls[-1], f"id-match/{session.session_id}/outputs/Song.ttml")


class FakeBlobStore:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.put_calls: list[str] = []

    def put_file(self, local_path: Path, blob_path: str) -> BlobArtifactRecord:
        self.put_calls.append(blob_path)
        self.objects[blob_path] = Path(local_path).read_bytes()
        return BlobArtifactRecord(pathname=blob_path, url=f"blob://{blob_path}", download_url=f"blob://{blob_path}")

    def get_to_file(self, blob_path: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.objects[blob_path])

    def delete_prefix(self, prefix: str) -> None:
        for key in list(self.objects):
            if key.startswith(prefix):
                del self.objects[key]


class FakeKeyValueStore:
    def __init__(self):
        self.values: dict[str, dict] = {}

    def set_json(self, key: str, value: dict) -> None:
        self.values[key] = value

    def get_json(self, key: str) -> dict | None:
        return self.values.get(key)


if __name__ == "__main__":
    unittest.main()
