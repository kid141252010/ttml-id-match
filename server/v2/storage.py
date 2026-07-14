from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Protocol, runtime_checkable


JsonObject = dict[str, Any]
_LOCKS_GUARD = threading.Lock()
_ROOT_LOCKS: dict[Path, threading.RLock] = {}
_ROOT_RATE_LIMITS: dict[Path, dict[str, tuple[int, float]]] = {}


class VersionConflictError(RuntimeError):
    """Raised when a versioned write is based on stale state."""


class LeaseConflictError(RuntimeError):
    """Raised when a job step write is not owned by the active lease holder."""


class InvalidArtifactKeyError(ValueError):
    """Raised when an artifact key is not a safe root-relative path."""


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    count: int
    retry_after_seconds: int


def validate_identifier(value: str, *, label: str = "identifier") -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value or ""):
        raise ValueError(f"invalid {label}")
    return value


@runtime_checkable
class SessionRepository(Protocol):
    def create(
        self,
        data: JsonObject | None = None,
        *,
        session_id: str | None = None,
    ) -> "VersionedSession": ...

    def load(self, session_id: str) -> "VersionedSession | None": ...

    def save(
        self,
        session_id: str,
        data: JsonObject,
        *,
        expected_version: int,
    ) -> "VersionedSession": ...

    def save_with_job_lease(
        self,
        session_id: str,
        data: JsonObject,
        *,
        expected_version: int,
        job_id: str,
        owner: str,
    ) -> "VersionedSession": ...

    def delete(
        self,
        session_id: str,
        *,
        expected_version: int | None = None,
    ) -> bool: ...

    def acquire_job_lease(
        self,
        session_id: str,
        job_id: str,
        owner: str,
        *,
        ttl_seconds: float,
        now: float | None = None,
    ) -> bool: ...

    def release_job_lease(
        self,
        session_id: str,
        job_id: str,
        owner: str,
    ) -> bool: ...

    def renew_job_lease(
        self,
        session_id: str,
        job_id: str,
        owner: str,
        *,
        ttl_seconds: float,
    ) -> bool: ...

    def register_expiry(
        self,
        session_id: str,
        *,
        expires_at: float,
        ttl_seconds: float,
    ) -> None: ...

    def list_expired(self, *, before: float, limit: int) -> list[str]: ...

    def remove_expiry(self, session_id: str) -> None: ...

    def consume_rate_limit(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
        now: float | None = None,
    ) -> RateLimitResult: ...


@runtime_checkable
class ArtifactStore(Protocol):
    def put_bytes(self, key: str, content: bytes) -> str: ...

    def get_bytes(self, key: str) -> bytes: ...

    def put_file(self, key: str, source: Path) -> str: ...

    def get_file(self, key: str, destination: Path) -> Path: ...

    def put_json(self, key: str, payload: JsonObject) -> str: ...

    def get_json(self, key: str) -> JsonObject: ...

    def delete(self, key: str) -> bool: ...

    def delete_prefix(self, prefix: str) -> int: ...


@dataclass(frozen=True)
class VersionedSession:
    session_id: str
    version: int
    data: JsonObject


class LocalJsonSessionRepository:
    """Persist versioned session documents as JSON files."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.sessions_dir = self.root / "sessions"
        self.leases_dir = self.root / "leases"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.leases_dir.mkdir(parents=True, exist_ok=True)
        with _LOCKS_GUARD:
            self._lock = _ROOT_LOCKS.setdefault(self.root, threading.RLock())
            self._rate_limits = _ROOT_RATE_LIMITS.setdefault(self.root, {})

    def create(
        self,
        data: JsonObject | None = None,
        *,
        session_id: str | None = None,
    ) -> VersionedSession:
        if session_id is not None:
            validate_identifier(session_id, label="session id")
        with self._lock:
            record = VersionedSession(
                session_id=session_id or uuid.uuid4().hex,
                version=1,
                data=data or {},
            )
            if self._path(record.session_id).exists():
                raise VersionConflictError(f"session already exists: {record.session_id}")
            self._write_session(record)
            return record

    def load(self, session_id: str) -> VersionedSession | None:
        validate_identifier(session_id, label="session id")
        with self._lock:
            return self._load_unlocked(session_id)

    def _load_unlocked(self, session_id: str) -> VersionedSession | None:
        path = self._path(session_id)
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return VersionedSession(
            session_id=str(payload["session_id"]),
            version=int(payload["version"]),
            data=dict(payload["data"]),
        )

    def save(
        self,
        session_id: str,
        data: JsonObject,
        *,
        expected_version: int,
    ) -> VersionedSession:
        validate_identifier(session_id, label="session id")
        with self._lock:
            current = self._load_unlocked(session_id)
            if current is None:
                raise KeyError(f"session not found: {session_id}")
            if current.version != expected_version:
                raise VersionConflictError(
                    f"session {session_id!r} is at version {current.version}, "
                    f"not expected version {expected_version}"
                )
            record = VersionedSession(
                session_id=session_id,
                version=current.version + 1,
                data=data,
            )
            self._write_session(record)
            return record

    def save_with_job_lease(
        self,
        session_id: str,
        data: JsonObject,
        *,
        expected_version: int,
        job_id: str,
        owner: str,
    ) -> VersionedSession:
        validate_identifier(session_id, label="session id")
        validate_identifier(job_id, label="job id")
        if not owner:
            raise ValueError("lease owner must not be empty")
        with self._lock:
            current = self._load_unlocked(session_id)
            if current is None:
                raise KeyError(f"session not found: {session_id}")
            if current.version != expected_version:
                raise VersionConflictError(
                    f"session {session_id!r} is at version {current.version}, "
                    f"not expected version {expected_version}"
                )
            lease_path = self._lease_path(session_id, job_id)
            if not lease_path.is_file():
                raise LeaseConflictError(
                    f"preview job lease is missing: {job_id}"
                )
            lease = json.loads(lease_path.read_text(encoding="utf-8"))
            if (
                lease.get("owner") != owner
                or float(lease["expires_at"]) <= time.time()
            ):
                raise LeaseConflictError(
                    f"preview job lease is not owned by {owner}: {job_id}"
                )
            record = VersionedSession(
                session_id=session_id,
                version=current.version + 1,
                data=data,
            )
            self._write_session(record)
            return record

    def delete(
        self,
        session_id: str,
        *,
        expected_version: int | None = None,
    ) -> bool:
        validate_identifier(session_id, label="session id")
        with self._lock:
            current = self._load_unlocked(session_id)
            if current is None:
                return False
            if expected_version is not None and current.version != expected_version:
                raise VersionConflictError(
                    f"session {session_id!r} is at version {current.version}, "
                    f"not expected version {expected_version}"
                )
            self._path(session_id).unlink()
            for lease_path in self.leases_dir.glob("*.json"):
                lease = json.loads(lease_path.read_text(encoding="utf-8"))
                if lease.get("session_id") == session_id:
                    lease_path.unlink()
            return True

    def acquire_job_lease(
        self,
        session_id: str,
        job_id: str,
        owner: str,
        *,
        ttl_seconds: float,
        now: float | None = None,
    ) -> bool:
        validate_identifier(session_id, label="session id")
        validate_identifier(job_id, label="job id")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        if not owner:
            raise ValueError("lease owner must not be empty")
        acquired_at = time.time() if now is None else float(now)
        with self._lock:
            if self._load_unlocked(session_id) is None:
                raise KeyError(f"session not found: {session_id}")
            path = self._lease_path(session_id, job_id)
            if path.is_file():
                current = json.loads(path.read_text(encoding="utf-8"))
                if float(current["expires_at"]) > acquired_at:
                    return False
            self._write_json(
                path,
                {
                    "session_id": session_id,
                    "job_id": job_id,
                    "owner": owner,
                    "expires_at": acquired_at + ttl_seconds,
                },
            )
            return True

    def release_job_lease(self, session_id: str, job_id: str, owner: str) -> bool:
        validate_identifier(session_id, label="session id")
        validate_identifier(job_id, label="job id")
        with self._lock:
            path = self._lease_path(session_id, job_id)
            if not path.is_file():
                return False
            current = json.loads(path.read_text(encoding="utf-8"))
            if current.get("owner") != owner:
                return False
            path.unlink()
            return True

    def renew_job_lease(
        self,
        session_id: str,
        job_id: str,
        owner: str,
        *,
        ttl_seconds: float,
    ) -> bool:
        validate_identifier(session_id, label="session id")
        validate_identifier(job_id, label="job id")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        with self._lock:
            path = self._lease_path(session_id, job_id)
            if not path.is_file():
                return False
            current = json.loads(path.read_text(encoding="utf-8"))
            now = time.time()
            if current.get("owner") != owner or float(current["expires_at"]) <= now:
                return False
            current["expires_at"] = now + ttl_seconds
            self._write_json(path, current)
            return True

    def register_expiry(
        self,
        session_id: str,
        *,
        expires_at: float,
        ttl_seconds: float,
    ) -> None:
        validate_identifier(session_id, label="session id")
        if expires_at <= 0 or ttl_seconds <= 0:
            raise ValueError("session expiry must be greater than zero")

    def list_expired(self, *, before: float, limit: int) -> list[str]:
        if limit <= 0:
            return []
        expired: list[tuple[float, str]] = []
        with self._lock:
            for path in self.sessions_dir.glob("*.json"):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    expires_at = float(payload.get("data", {}).get("expires_at", 0))
                    session_id = str(payload["session_id"])
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if expires_at and expires_at <= before:
                    expired.append((expires_at, session_id))
        return [session_id for _, session_id in sorted(expired)[:limit]]

    def remove_expiry(self, session_id: str) -> None:
        validate_identifier(session_id, label="session id")

    def consume_rate_limit(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
        now: float | None = None,
    ) -> RateLimitResult:
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("rate limit and window must be greater than zero")
        current_time = time.time() if now is None else float(now)
        with self._lock:
            for expired_key in [
                item_key
                for item_key, (_, reset_at) in self._rate_limits.items()
                if reset_at <= current_time
            ]:
                self._rate_limits.pop(expired_key, None)
            count, reset_at = self._rate_limits.get(
                key,
                (0, current_time + window_seconds),
            )
            if reset_at <= current_time:
                count, reset_at = 0, current_time + window_seconds
            count += 1
            self._rate_limits[key] = (count, reset_at)
        retry_after = max(1, int(reset_at - current_time + 0.999))
        return RateLimitResult(count <= limit, count, retry_after)

    def _write_session(self, record: VersionedSession) -> None:
        self._write_json(
            self._path(record.session_id),
            {
                "session_id": record.session_id,
                "version": record.version,
                "data": record.data,
            },
        )

    @staticmethod
    def _write_json(path: Path, payload: JsonObject) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"

    def _lease_path(self, session_id: str, job_id: str) -> Path:
        digest = hashlib.sha256(f"{session_id}\0{job_id}".encode("utf-8")).hexdigest()
        return self.leases_dir / f"{digest}.json"


class FileArtifactStore:
    """Store artifacts beneath a filesystem root using portable keys."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, key: str, content: bytes) -> str:
        normalized_key, path = self._resolve_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(content)
        temporary.replace(path)
        return normalized_key

    def get_bytes(self, key: str) -> bytes:
        _, path = self._resolve_key(key)
        return path.read_bytes()

    def put_file(self, key: str, source: Path) -> str:
        normalized_key, path = self._resolve_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        shutil.copyfile(source, temporary)
        temporary.replace(path)
        return normalized_key

    def get_file(self, key: str, destination: Path) -> Path:
        _, source = self._resolve_key(key)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return destination

    def put_json(self, key: str, payload: JsonObject) -> str:
        return self.put_bytes(
            key,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            ),
        )

    def get_json(self, key: str) -> JsonObject:
        payload = json.loads(self.get_bytes(key).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"JSON artifact is not an object: {key}")
        return payload

    def delete(self, key: str) -> bool:
        _, path = self._resolve_key(key)
        if not path.is_file() and not path.is_symlink():
            return False
        path.unlink()
        return True

    def delete_prefix(self, prefix: str) -> int:
        _, path = self._resolve_key(prefix)
        if not path.exists() and not path.is_symlink():
            return 0
        if path.is_file() or path.is_symlink():
            path.unlink()
            return 1
        deleted = sum(
            1
            for child in path.rglob("*")
            if child.is_file() or child.is_symlink()
        )
        shutil.rmtree(path)
        return deleted

    def _resolve_key(self, key: str) -> tuple[str, Path]:
        if not isinstance(key, str) or not key or "\0" in key or "\\" in key:
            raise InvalidArtifactKeyError(f"invalid artifact key: {key!r}")
        if key.startswith("/") or PureWindowsPath(key).drive:
            raise InvalidArtifactKeyError(f"artifact key must be relative: {key!r}")
        parts = key.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise InvalidArtifactKeyError(f"invalid artifact key: {key!r}")
        path = self.root.joinpath(*parts).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise InvalidArtifactKeyError(
                f"artifact key escapes storage root: {key!r}"
            ) from exc
        return "/".join(parts), path
