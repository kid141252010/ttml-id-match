from __future__ import annotations

import json
import importlib
import math
import os
import tempfile
import uuid
from enum import Enum
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Protocol, runtime_checkable
from urllib import request as urllib_request

from server.v2.storage import (
    InvalidArtifactKeyError,
    JsonObject,
    LeaseConflictError,
    VersionConflictError,
    VersionedSession,
    validate_identifier,
)


class ConditionalMutation(str, Enum):
    """Outcome of an atomic version-conditional Redis mutation."""

    APPLIED = "applied"
    MISSING = "missing"
    CONFLICT = "conflict"
    LEASE_CONFLICT = "lease_conflict"


@runtime_checkable
class RedisJsonClient(Protocol):
    def get_json(self, key: str) -> JsonObject | None: ...

    def create_json(self, key: str, value: JsonObject) -> bool: ...

    def compare_and_set_json(
        self,
        key: str,
        value: JsonObject,
        *,
        expected_version: int,
    ) -> ConditionalMutation: ...

    def compare_and_set_json_with_lease(
        self,
        key: str,
        value: JsonObject,
        *,
        expected_version: int,
        lease_key: str,
        expected_owner: str,
    ) -> ConditionalMutation: ...

    def delete_json(
        self,
        key: str,
        *,
        expected_version: int | None = None,
    ) -> ConditionalMutation: ...

    def set_nx_ex(
        self,
        key: str,
        value: str,
        *,
        ttl_seconds: float,
        now: float | None = None,
    ) -> bool: ...

    def compare_and_delete(self, key: str, expected_value: str) -> bool: ...

    def compare_and_expire(
        self,
        key: str,
        expected_value: str,
        *,
        ttl_seconds: float,
    ) -> bool: ...


@runtime_checkable
class VercelBlobClient(Protocol):
    def put_bytes(self, pathname: str, content: bytes) -> None: ...

    def get_bytes(self, pathname: str) -> bytes: ...

    def delete(self, pathname: str) -> bool: ...

    def delete_prefix(self, prefix: str) -> int: ...


class SdkVercelBlobClient:
    """Byte-oriented facade over the official ``vercel.blob`` SDK."""

    def __init__(
        self,
        token: str,
        *,
        upload_file: Callable[..., Any] | None = None,
        get_blob: Callable[..., Any] | None = None,
        delete_blob: Callable[..., Any] | None = None,
        list_blobs: Callable[..., Any] | None = None,
    ) -> None:
        if not token:
            raise ValueError("Vercel Blob token must not be empty")
        if any(
            callback is None
            for callback in (upload_file, get_blob, delete_blob, list_blobs)
        ):
            try:
                sdk_blob = importlib.import_module("vercel.blob")
            except ImportError as exc:
                raise RuntimeError(
                    "Vercel Blob SDK is required for the Vercel storage backend"
                ) from exc
            upload_file = upload_file or getattr(sdk_blob, "upload_file", None)
            get_blob = get_blob or getattr(sdk_blob, "get", None)
            delete_blob = delete_blob or getattr(sdk_blob, "delete", None)
            list_blobs = list_blobs or (
                getattr(sdk_blob, "list_objects", None)
                or getattr(sdk_blob, "list", None)
                or getattr(sdk_blob, "list_blobs", None)
            )
        if any(
            callback is None
            for callback in (upload_file, get_blob, delete_blob, list_blobs)
        ):
            raise RuntimeError("installed Vercel Blob SDK lacks required operations")
        self.token = token
        self._upload_file = upload_file
        self._get_blob = get_blob
        self._delete_blob = delete_blob
        self._list_blobs = list_blobs

    def put_bytes(self, pathname: str, content: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(prefix="id-match-blob-")
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            temporary.write_bytes(content)
            self._upload_file(
                str(temporary),
                pathname,
                token=self.token,
                access="private",
                overwrite=True,
            )
        finally:
            temporary.unlink(missing_ok=True)

    def get_bytes(self, pathname: str) -> bytes:
        result = self._get_blob(
            pathname,
            token=self.token,
            access="private",
        )
        if hasattr(result, "read"):
            result = result.read()
        elif isinstance(result, dict) and "content" in result:
            result = result["content"]
        elif hasattr(result, "content"):
            result = result.content
        return bytes(result)

    def delete(self, pathname: str) -> bool:
        result = self._delete_blob(pathname, token=self.token)
        return result is not False

    def delete_prefix(self, prefix: str) -> int:
        pathnames_to_delete: list[str] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page = self._list_page(prefix, cursor)
            pathnames, next_cursor, has_more = _read_blob_page(page)
            pathnames_to_delete.extend(pathnames)
            if not has_more or not next_cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        unique_pathnames = list(dict.fromkeys(pathnames_to_delete))
        for pathname in unique_pathnames:
            self._delete_blob(pathname, token=self.token)
        return len(unique_pathnames)

    def _list_page(self, prefix: str, cursor: str | None) -> Any:
        options: dict[str, Any] = {"prefix": prefix, "token": self.token}
        if cursor:
            options["cursor"] = cursor
        try:
            return self._list_blobs(**options)
        except TypeError:
            sdk_options = {key: value for key, value in options.items() if key != "token"}
            return self._list_blobs(sdk_options, token=self.token)


_CAS_JSON_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then return -1 end
local document = cjson.decode(current)
if tonumber(document['version']) ~= tonumber(ARGV[1]) then return 0 end
redis.call('SET', KEYS[1], ARGV[2])
return 1
""".strip()

_CAS_JSON_WITH_LEASE_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then return -1 end
local document = cjson.decode(current)
if tonumber(document['version']) ~= tonumber(ARGV[1]) then return 0 end
local lease_owner = redis.call('GET', KEYS[2])
if lease_owner ~= ARGV[2] then return -2 end
redis.call('SET', KEYS[1], ARGV[3])
return 1
""".strip()

_DELETE_JSON_VERSION_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then return -1 end
local document = cjson.decode(current)
if tonumber(document['version']) ~= tonumber(ARGV[1]) then return 0 end
redis.call('DEL', KEYS[1])
return 1
""".strip()

_COMPARE_DELETE_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if current ~= ARGV[1] then return 0 end
return redis.call('DEL', KEYS[1])
""".strip()

_COMPARE_EXPIRE_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if current ~= ARGV[1] then return 0 end
return redis.call('EXPIRE', KEYS[1], ARGV[2])
""".strip()


class UpstashRestRedisClient:
    """Redis JSON primitives implemented through Upstash REST command arrays."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_seconds: float = 10,
        requester: Callable[[urllib_request.Request, float], bytes] | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("Redis REST base URL must not be empty")
        if not token:
            raise ValueError("Redis REST token must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self._requester = requester or _read_url_request

    def get_json(self, key: str) -> JsonObject | None:
        result = self._command("GET", key)
        if result is None:
            return None
        if isinstance(result, str):
            result = json.loads(result)
        if not isinstance(result, dict):
            raise ValueError(f"Redis value is not a JSON object: {key}")
        return dict(result)

    def create_json(self, key: str, value: JsonObject) -> bool:
        result = self._command("SET", key, _dump_json(value), "NX")
        return result == "OK" or result is True

    def compare_and_set_json(
        self,
        key: str,
        value: JsonObject,
        *,
        expected_version: int,
    ) -> ConditionalMutation:
        result = self._command(
            "EVAL",
            _CAS_JSON_SCRIPT,
            1,
            key,
            expected_version,
            _dump_json(value),
        )
        return _conditional_mutation(result)

    def compare_and_set_json_with_lease(
        self,
        key: str,
        value: JsonObject,
        *,
        expected_version: int,
        lease_key: str,
        expected_owner: str,
    ) -> ConditionalMutation:
        result = self._command(
            "EVAL",
            _CAS_JSON_WITH_LEASE_SCRIPT,
            2,
            key,
            lease_key,
            expected_version,
            expected_owner,
            _dump_json(value),
        )
        return _conditional_mutation(result)

    def delete_json(
        self,
        key: str,
        *,
        expected_version: int | None = None,
    ) -> ConditionalMutation:
        if expected_version is None:
            result = self._command("DEL", key)
            return (
                ConditionalMutation.APPLIED
                if int(result or 0) > 0
                else ConditionalMutation.MISSING
            )
        result = self._command(
            "EVAL",
            _DELETE_JSON_VERSION_SCRIPT,
            1,
            key,
            expected_version,
        )
        return _conditional_mutation(result)

    def set_nx_ex(
        self,
        key: str,
        value: str,
        *,
        ttl_seconds: float,
        now: float | None = None,
    ) -> bool:
        del now  # Production Redis uses its own authoritative expiry clock.
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        result = self._command(
            "SET",
            key,
            value,
            "NX",
            "EX",
            math.ceil(ttl_seconds),
        )
        return result == "OK" or result is True

    def compare_and_delete(self, key: str, expected_value: str) -> bool:
        result = self._command(
            "EVAL",
            _COMPARE_DELETE_SCRIPT,
            1,
            key,
            expected_value,
        )
        return int(result or 0) > 0

    def compare_and_expire(
        self,
        key: str,
        expected_value: str,
        *,
        ttl_seconds: float,
    ) -> bool:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        result = self._command(
            "EVAL",
            _COMPARE_EXPIRE_SCRIPT,
            1,
            key,
            expected_value,
            math.ceil(ttl_seconds),
        )
        return int(result or 0) > 0

    def _command(self, *parts: Any) -> Any:
        body = json.dumps(
            list(parts),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib_request.Request(
            self.base_url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        raw = self._requester(request, self.timeout_seconds)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Redis REST response is not an object")
        error = payload.get("error")
        if error:
            raise RuntimeError(f"Redis REST command failed: {error}")
        return payload.get("result")


class RedisSessionRepository:
    """Versioned session repository backed by atomic Redis operations."""

    def __init__(
        self,
        client: RedisJsonClient,
        *,
        namespace: str = "id-match:v2",
    ) -> None:
        self.client = client
        self.namespace = namespace.rstrip(":")

    def create(
        self,
        data: JsonObject | None = None,
        *,
        session_id: str | None = None,
    ) -> VersionedSession:
        resolved_session_id = session_id or uuid.uuid4().hex
        validate_identifier(resolved_session_id, label="session id")
        record = VersionedSession(
            session_id=resolved_session_id,
            version=1,
            data=dict(data or {}),
        )
        if not self.client.create_json(
            self._session_key(record.session_id),
            self._serialize(record),
        ):
            raise VersionConflictError(f"session already exists: {record.session_id}")
        return record

    def load(self, session_id: str) -> VersionedSession | None:
        validate_identifier(session_id, label="session id")
        payload = self.client.get_json(self._session_key(session_id))
        if payload is None:
            return None
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
        record = VersionedSession(
            session_id=session_id,
            version=expected_version + 1,
            data=dict(data),
        )
        result = self.client.compare_and_set_json(
            self._session_key(session_id),
            self._serialize(record),
            expected_version=expected_version,
        )
        if result is ConditionalMutation.MISSING:
            raise KeyError(f"session not found: {session_id}")
        if result is ConditionalMutation.CONFLICT:
            raise VersionConflictError(
                f"session {session_id!r} is not at expected version "
                f"{expected_version}"
            )
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
        record = VersionedSession(
            session_id=session_id,
            version=expected_version + 1,
            data=dict(data),
        )
        result = self.client.compare_and_set_json_with_lease(
            self._session_key(session_id),
            self._serialize(record),
            expected_version=expected_version,
            lease_key=self._lease_key(session_id, job_id),
            expected_owner=owner,
        )
        if result is ConditionalMutation.MISSING:
            raise KeyError(f"session not found: {session_id}")
        if result is ConditionalMutation.CONFLICT:
            raise VersionConflictError(
                f"session {session_id!r} is not at expected version "
                f"{expected_version}"
            )
        if result is ConditionalMutation.LEASE_CONFLICT:
            raise LeaseConflictError(
                f"preview job lease is not owned by {owner}: {job_id}"
            )
        return record

    def delete(
        self,
        session_id: str,
        *,
        expected_version: int | None = None,
    ) -> bool:
        validate_identifier(session_id, label="session id")
        result = self.client.delete_json(
            self._session_key(session_id),
            expected_version=expected_version,
        )
        if result is ConditionalMutation.CONFLICT:
            raise VersionConflictError(
                f"session {session_id!r} is not at expected version "
                f"{expected_version}"
            )
        return result is ConditionalMutation.APPLIED

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
        if self.load(session_id) is None:
            raise KeyError(f"session not found: {session_id}")
        return self.client.set_nx_ex(
            self._lease_key(session_id, job_id),
            owner,
            ttl_seconds=ttl_seconds,
            now=now,
        )

    def release_job_lease(self, session_id: str, job_id: str, owner: str) -> bool:
        validate_identifier(session_id, label="session id")
        validate_identifier(job_id, label="job id")
        return self.client.compare_and_delete(
            self._lease_key(session_id, job_id),
            owner,
        )

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
        return self.client.compare_and_expire(
            self._lease_key(session_id, job_id),
            owner,
            ttl_seconds=ttl_seconds,
        )

    def _session_key(self, session_id: str) -> str:
        return f"{self.namespace}:sessions:{session_id}"

    def _lease_key(self, session_id: str, job_id: str) -> str:
        return f"{self._session_key(session_id)}:jobs:{job_id}:lease"

    @staticmethod
    def _serialize(record: VersionedSession) -> JsonObject:
        return {
            "session_id": record.session_id,
            "version": record.version,
            "data": record.data,
        }


class VercelBlobArtifactStore:
    """Artifact store using immutable, session-scoped Vercel Blob paths."""

    def __init__(
        self,
        client: VercelBlobClient | None = None,
        *,
        blob_client: VercelBlobClient | None = None,
        token: str | None = None,
        namespace: str = "id-match/v2",
    ) -> None:
        if client is not None and blob_client is not None:
            raise ValueError("pass either client or blob_client, not both")
        selected_client = client or blob_client
        if selected_client is None:
            selected_token = token or os.environ.get("BLOB_READ_WRITE_TOKEN")
            if not selected_token:
                raise RuntimeError(
                    "Vercel Blob storage requires BLOB_READ_WRITE_TOKEN"
                )
            selected_client = SdkVercelBlobClient(selected_token)
        self.client = selected_client
        self.namespace = namespace.strip("/")
        if not self.namespace:
            raise ValueError("blob namespace must not be empty")

    def put_bytes(self, key: str, content: bytes) -> str:
        normalized = _normalize_artifact_key(key, allow_session_root=False)
        self.client.put_bytes(self._pathname(normalized), bytes(content))
        return normalized

    def get_bytes(self, key: str) -> bytes:
        normalized = _normalize_artifact_key(key, allow_session_root=False)
        return bytes(self.client.get_bytes(self._pathname(normalized)))

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
        normalized = _normalize_artifact_key(key, allow_session_root=False)
        return self.client.delete(self._pathname(normalized))

    def delete_prefix(self, prefix: str) -> int:
        normalized = _normalize_artifact_key(prefix, allow_session_root=True)
        return self.client.delete_prefix(f"{self._pathname(normalized)}/")

    def _pathname(self, key: str) -> str:
        return f"{self.namespace}/{key}"


def _normalize_artifact_key(key: str, *, allow_session_root: bool) -> str:
    if not isinstance(key, str) or not key or "\0" in key or "\\" in key:
        raise InvalidArtifactKeyError(f"invalid artifact key: {key!r}")
    if key.startswith("/") or PureWindowsPath(key).drive:
        raise InvalidArtifactKeyError(f"artifact key must be relative: {key!r}")
    parts = key.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise InvalidArtifactKeyError(f"invalid artifact key: {key!r}")
    minimum_parts = 2 if allow_session_root else 3
    if parts[0] != "sessions" or len(parts) < minimum_parts or not parts[1]:
        raise InvalidArtifactKeyError(
            f"artifact key must be scoped to sessions/<session_id>: {key!r}"
        )
    return "/".join(parts)


def _read_blob_page(page: Any) -> tuple[list[str], str | None, bool]:
    if isinstance(page, dict):
        blobs = page.get("blobs", [])
        cursor_value = page.get("cursor")
        has_more = bool(page.get("has_more", page.get("hasMore", False)))
    else:
        blobs = getattr(page, "blobs", [])
        cursor_value = getattr(page, "cursor", None)
        has_more = bool(
            getattr(page, "has_more", getattr(page, "hasMore", False))
        )
    pathnames: list[str] = []
    for blob in blobs or []:
        pathname = (
            blob.get("pathname")
            if isinstance(blob, dict)
            else getattr(blob, "pathname", None)
        )
        if isinstance(pathname, str) and pathname:
            pathnames.append(pathname)
    cursor = str(cursor_value) if cursor_value else None
    return pathnames, cursor, has_more


def _dump_json(value: JsonObject) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _conditional_mutation(result: Any) -> ConditionalMutation:
    numeric = int(result)
    if numeric > 0:
        return ConditionalMutation.APPLIED
    if numeric == -1:
        return ConditionalMutation.MISSING
    if numeric == -2:
        return ConditionalMutation.LEASE_CONFLICT
    return ConditionalMutation.CONFLICT


def _read_url_request(request: urllib_request.Request, timeout: float) -> bytes:
    with urllib_request.urlopen(request, timeout=timeout) as response:
        return response.read()
