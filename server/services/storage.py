from __future__ import annotations

import json
import hashlib
import os
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib import error, parse, request

from server.models.schemas import PreviewResult
from ttml_metadata.models import PairMetadata


@dataclass(frozen=True)
class BlobArtifactRecord:
    pathname: str
    url: str
    download_url: str
    sha256: str | None = None


@dataclass
class StoredSession:
    session_id: str
    root: Path
    upload_dir: Path
    output_dir: Path
    pairs: list[dict[str, str | None]] = field(default_factory=list)
    prepared_pairs: dict[str, PairMetadata] = field(default_factory=dict)
    previews: dict[str, PreviewResult] = field(default_factory=dict)
    preview_fingerprint: str | None = None
    preview_jobs: dict[str, dict[str, Any]] = field(default_factory=dict)


class ArtifactStore(Protocol):
    def create_session(self) -> StoredSession:
        ...

    def get_session(self, session_id: str) -> StoredSession:
        ...

    def sync_session(self, session: StoredSession) -> None:
        ...

    def cleanup_session(self, session_id: str) -> None:
        ...


class LocalArtifactStore:
    def __init__(self, root: Path | None = None):
        self.root = root or Path(".codex-tmp") / "id-match-sessions"
        self.root.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, StoredSession] = {}

    def create_session(self) -> StoredSession:
        session_id = uuid.uuid4().hex
        session = self._create_session_dirs(session_id)
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> StoredSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"session not found: {session_id}") from exc

    def sync_session(self, session: StoredSession) -> None:
        self._sessions[session.session_id] = session

    def cleanup_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session:
            shutil.rmtree(session.root, ignore_errors=True)

    def _create_session_dirs(self, session_id: str) -> StoredSession:
        session_root = self.root / session_id
        upload_dir = session_root / "uploads"
        output_dir = session_root / "outputs"
        upload_dir.mkdir(parents=True, exist_ok=False)
        output_dir.mkdir(parents=True, exist_ok=False)
        return StoredSession(session_id=session_id, root=session_root, upload_dir=upload_dir, output_dir=output_dir)


class BlobStoreProtocol(Protocol):
    def put_file(self, local_path: str | Path, blob_path: str) -> BlobArtifactRecord:
        ...

    def get_to_file(self, blob_path: str, local_path: Path) -> None:
        ...

    def delete_prefix(self, prefix: str) -> None:
        ...


class RedisLikeKeyValueStore(Protocol):
    def set_json(self, key: str, value: dict[str, Any]) -> None:
        ...

    def get_json(self, key: str) -> dict[str, Any] | None:
        ...

    def delete(self, key: str) -> None:
        ...


class VercelBlobStore:
    def __init__(self, token: str):
        self.token = token
        self._client = None

    def put_file(self, local_path: str | Path, blob_path: str) -> BlobArtifactRecord:
        from vercel.blob import upload_file

        result = upload_file(
            local_path,
            blob_path,
            token=self.token,
            access="private",
            overwrite=True,
        )
        return BlobArtifactRecord(pathname=result.pathname, url=result.url, download_url=result.download_url)

    def get_to_file(self, blob_path: str, local_path: Path) -> None:
        from vercel.blob import get

        result = get(blob_path, token=self.token, access="private")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(bytes(result))

    def delete_prefix(self, prefix: str) -> None:
        from vercel.blob import delete

        delete(prefix, token=self.token)


class RestKeyValueStore:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def set_json(self, key: str, value: dict[str, Any]) -> None:
        self._request("POST", f"/set/{_encode_key(key)}", json.dumps(value).encode("utf-8"))

    def get_json(self, key: str) -> dict[str, Any] | None:
        payload = self._request("GET", f"/get/{_encode_key(key)}")
        if not payload:
            return None
        data = json.loads(payload.decode("utf-8"))
        if isinstance(data, dict):
            value = data.get("result")
            return value if isinstance(value, dict) else None
        return None

    def delete(self, key: str) -> None:
        self._request("POST", f"/del/{_encode_key(key)}")

    def _request(self, method: str, path: str, body: bytes | None = None) -> bytes:
        req = request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with request.urlopen(req, timeout=30) as response:
            return response.read()


class VercelArtifactStore(LocalArtifactStore):
    def __init__(
        self,
        root: Path | None = None,
        *,
        blob_token: str | None = None,
        kv_rest_api_url: str | None = None,
        kv_rest_api_token: str | None = None,
        blob_store: BlobStoreProtocol | None = None,
        key_value_store: RedisLikeKeyValueStore | None = None,
    ):
        self.blob_token = blob_token or os.environ.get("BLOB_READ_WRITE_TOKEN")
        self.kv_rest_api_url = kv_rest_api_url or os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
        self.kv_rest_api_token = kv_rest_api_token or os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
        missing = [
            name
            for name, value in (
                ("BLOB_READ_WRITE_TOKEN", self.blob_token),
                ("KV_REST_API_URL", self.kv_rest_api_url),
                ("KV_REST_API_TOKEN", self.kv_rest_api_token),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"Vercel storage backend requires {', '.join(missing)}")
        super().__init__(root or Path(tempfile.gettempdir()) / "id-match-sessions")
        self.blob_store = blob_store or VercelBlobStore(self.blob_token)
        self.key_value_store = key_value_store or RestKeyValueStore(self.kv_rest_api_url, self.kv_rest_api_token)

    def create_session(self) -> StoredSession:
        session = super().create_session()
        self.sync_session(session)
        return session

    def get_session(self, session_id: str) -> StoredSession:
        cached = self._sessions.get(session_id)
        if cached is not None:
            return cached
        record = self.key_value_store.get_json(self._session_key(session_id))
        if record is None:
            raise KeyError(f"session not found: {session_id}")
        session = self._rehydrate_session(record)
        self._sessions[session_id] = session
        return session

    def sync_session(self, session: StoredSession) -> None:
        self._sessions[session.session_id] = session
        record = self._persist_session(session)
        self.key_value_store.set_json(self._session_key(session.session_id), record)

    def cleanup_session(self, session_id: str) -> None:
        super().cleanup_session(session_id)
        self.key_value_store.delete(self._session_key(session_id))
        self.blob_store.delete_prefix(self._blob_prefix(session_id))

    def _persist_session(self, session: StoredSession) -> dict[str, Any]:
        previous = self.key_value_store.get_json(self._session_key(session.session_id)) or {}
        upload_records = self._sync_directory(
            session.upload_dir,
            "uploads",
            previous_records=_records_by_pathname(previous.get("upload_records", [])),
        )
        output_records = self._sync_directory(
            session.output_dir,
            "outputs",
            previous_records=_records_by_pathname(previous.get("output_records", [])),
        )
        return {
            "session_id": session.session_id,
            "pairs": session.pairs,
            "preview_jobs": session.preview_jobs,
            "upload_records": [asdict(record) for record in upload_records],
            "output_records": [asdict(record) for record in output_records],
        }

    def _rehydrate_session(self, record: dict[str, Any]) -> StoredSession:
        session_id = str(record["session_id"])
        session = self._create_session_dirs(session_id)
        session.pairs = list(record.get("pairs", []))
        session.preview_jobs = dict(record.get("preview_jobs", {}))
        for artifact in record.get("upload_records", []):
            self.blob_store.get_to_file(artifact["pathname"], session.upload_dir / Path(artifact["pathname"]).name)
        for artifact in record.get("output_records", []):
            self.blob_store.get_to_file(artifact["pathname"], session.output_dir / Path(artifact["pathname"]).name)
        return session

    def _sync_directory(
        self,
        directory: Path,
        kind: str,
        *,
        previous_records: dict[str, BlobArtifactRecord] | None = None,
    ) -> list[BlobArtifactRecord]:
        records: list[BlobArtifactRecord] = []
        previous_records = previous_records or {}
        for path in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_file():
                continue
            blob_path = f"{self._blob_prefix(path.parent.parent.name)}/{kind}/{path.name}"
            digest = _file_sha256(path)
            previous = previous_records.get(blob_path)
            if previous is not None and previous.sha256 == digest:
                records.append(previous)
                continue
            uploaded = self.blob_store.put_file(path, blob_path)
            records.append(
                BlobArtifactRecord(
                    pathname=uploaded.pathname,
                    url=uploaded.url,
                    download_url=uploaded.download_url,
                    sha256=digest,
                )
            )
        return records

    @staticmethod
    def _session_key(session_id: str) -> str:
        return f"id-match:session:{session_id}"

    @staticmethod
    def _blob_prefix(session_id: str) -> str:
        return f"id-match/{session_id}"


def build_session_store(root: Path | None = None) -> ArtifactStore:
    backend = os.environ.get("ID_MATCH_STORAGE_BACKEND", "local").strip().casefold()
    if backend in {"", "local"}:
        return LocalArtifactStore(root)
    if backend == "vercel":
        return VercelArtifactStore(root)
    raise RuntimeError(f"unsupported session storage backend: {backend}")


def _encode_key(value: str) -> str:
    return parse.quote(value, safe="")


def _records_by_pathname(values: object) -> dict[str, BlobArtifactRecord]:
    if not isinstance(values, list):
        return {}
    records: dict[str, BlobArtifactRecord] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        pathname = value.get("pathname")
        if not isinstance(pathname, str):
            continue
        records[pathname] = BlobArtifactRecord(
            pathname=pathname,
            url=str(value.get("url") or ""),
            download_url=str(value.get("download_url") or ""),
            sha256=value.get("sha256") if isinstance(value.get("sha256"), str) else None,
        )
    return records


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
