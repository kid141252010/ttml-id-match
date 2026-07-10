from __future__ import annotations

import hashlib
import io
import shutil
import threading
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from ttml_metadata.v2.application import MatchingApplication, PairSnapshot
from ttml_metadata.v2.domain import Selection
from ttml_metadata.v2.pairing import PairingPair, build_pairing_plan
from ttml_metadata.v2.ttml_plan import ChangePlan, TtmlWriter

from .storage import (
    ArtifactStore,
    LeaseConflictError,
    SessionRepository,
    VersionedSession,
    validate_identifier,
)


class SessionNotFoundError(KeyError):
    pass


class PreviewJobNotFoundError(KeyError):
    pass


class JobBusyError(RuntimeError):
    pass


class SnapshotConflictError(RuntimeError):
    pass


class InvalidSelectionError(ValueError):
    pass


class PairingConflictError(ValueError):
    pass


@dataclass(frozen=True)
class UploadData:
    filename: str
    content: bytes


class SessionWorkflow:
    def __init__(
        self,
        repository: SessionRepository,
        artifacts: ArtifactStore,
        application: MatchingApplication,
        *,
        work_root: Path,
        lease_seconds: float = 60.0,
    ) -> None:
        self._repository = repository
        self._artifacts = artifacts
        self._application = application
        self._work_root = Path(work_root)
        self._work_root.mkdir(parents=True, exist_ok=True)
        self._lease_seconds = lease_seconds

    def create_session(self) -> str:
        return self._repository.create(_empty_session()).session_id

    def delete_session(self, session_id: str) -> bool:
        current = self._repository.load(session_id)
        if current is None:
            return False
        if not current.data.get("deleting"):
            current = self._repository.save(
                session_id,
                {"deleting": True},
                expected_version=current.version,
            )
        self._artifacts.delete_prefix(_session_prefix(session_id))
        shutil.rmtree(self._work_root / session_id, ignore_errors=True)
        return self._repository.delete(
            session_id,
            expected_version=current.version,
        )

    def upload_files(self, session_id: str, uploads: Iterable[UploadData]) -> dict[str, object]:
        current = self._load(session_id)
        data = _copy_json(current.data)
        records = [dict(record) for record in data.get("uploads", [])]
        upload_generation = uuid.uuid4().hex
        upload_prefix = (
            f"{_session_prefix(session_id)}/uploads/{upload_generation}"
        )
        try:
            for upload_index, upload in enumerate(uploads):
                filename = _safe_filename(upload.filename)
                key = f"{upload_prefix}/{upload_index}/{filename}"
                self._artifacts.put_bytes(key, bytes(upload.content))
                records.append(
                    {
                        "filename": filename,
                        "artifact_key": key,
                        "sha256": _sha256(upload.content),
                        "size": len(upload.content),
                    }
                )

            pairing = build_pairing_plan(Path(record["filename"]) for record in records)
            data.update(
                {
                    "uploads": records,
                    "pairing": pairing.to_dict(),
                    "jobs": {},
                    "current_snapshot_id": None,
                    "outputs": [],
                }
            )
            self._repository.save(session_id, data, expected_version=current.version)
        except Exception:
            _delete_prefix_quietly(self._artifacts, upload_prefix)
            raise
        return pairing.to_dict()

    def create_preview_job(self, session_id: str) -> dict[str, object]:
        current = self._load(session_id)
        data = _copy_json(current.data)
        pairing = data.get("pairing", {"pairs": [], "issues": []})
        issues = list(pairing.get("issues", []))
        if issues:
            raise PairingConflictError("ambiguous audio files must be resolved before preview")
        pairs = [pair for pair in pairing.get("pairs", []) if pair.get("status") != "ambiguous"]
        job_id = uuid.uuid4().hex
        job_prefix = f"{_session_prefix(session_id)}/jobs/{job_id}"
        draft_key = f"{job_prefix}/draft.json"
        pending_snapshot_id = uuid.uuid4().hex
        job = {
            "job_id": job_id,
            "status": "pending" if pairs else "completed",
            "pair_ids": [str(pair["pair_id"]) for pair in pairs],
            "next_index": 0,
            "total": len(pairs),
            "errors": [],
            "draft_key": draft_key,
            "snapshot_id": None,
            "pending_snapshot_id": pending_snapshot_id,
        }
        jobs = dict(data.get("jobs", {}))
        jobs[job_id] = job
        data["jobs"] = jobs
        try:
            self._artifacts.put_json(draft_key, {"version": 2, "steps": {}})
            if not pairs:
                self._publish_snapshot(session_id, data, job, [])
            saved = self._repository.save(
                session_id,
                data,
                expected_version=current.version,
            )
        except Exception:
            _delete_prefix_quietly(self._artifacts, job_prefix)
            _delete_quietly(
                self._artifacts,
                _snapshot_key(session_id, pending_snapshot_id),
            )
            raise
        return self._job_response(session_id, saved.data, job)

    def get_preview_job(self, session_id: str, job_id: str) -> dict[str, object]:
        current = self._load(session_id)
        job = _get_job(current.data, job_id)
        return self._job_response(session_id, current.data, job)

    def step_preview_job(
        self,
        session_id: str,
        job_id: str,
        *,
        owner: str | None = None,
    ) -> dict[str, object]:
        validate_identifier(session_id, label="session id")
        validate_identifier(job_id, label="job id")
        lease_owner = owner or uuid.uuid4().hex
        if not self._repository.acquire_job_lease(
            session_id,
            job_id,
            lease_owner,
            ttl_seconds=self._lease_seconds,
        ):
            raise JobBusyError(f"preview job is busy: {job_id}")
        heartbeat = _LeaseHeartbeat(
            self._repository,
            session_id,
            job_id,
            lease_owner,
            ttl_seconds=self._lease_seconds,
        )
        heartbeat.start()
        job: dict[str, Any] = {}
        try:
            current = self._load(session_id)
            data = _copy_json(current.data)
            jobs = dict(data.get("jobs", {}))
            job = dict(_get_job(data, job_id))
            if job.get("status") in {"completed", "completed_with_errors", "failed"}:
                return self._job_response(session_id, data, job)
            heartbeat.ensure_owned()

            draft = self._artifacts.get_json(str(job["draft_key"]))
            steps = _draft_steps(draft)
            next_index = int(job.get("next_index", 0))
            pair_ids = [str(pair_id) for pair_id in job.get("pair_ids", [])]
            if next_index < len(pair_ids):
                pair_id = pair_ids[next_index]
                step_key = str(next_index)
                step = steps.get(step_key)
                if step is None:
                    step_errors: list[dict[str, object]] = []
                    pair_snapshot: dict[str, Any] | None = None
                    try:
                        pair = self._materialize_pair(session_id, data, pair_id)
                        snapshot = self._application.preview_pair(pair)
                        pair_snapshot = snapshot.to_dict()
                        for source_key, source_result in snapshot.match_result.sources.items():
                            for warning in source_result.warnings:
                                step_errors.append(
                                    _error(
                                        "source_warning",
                                        warning,
                                        retryable=True,
                                        source=source_key,
                                        pair_id=pair_id,
                                    )
                                )
                    except Exception as exc:
                        step_errors.append(
                            _error(
                                "pair_preview_failed",
                                str(exc),
                                retryable=False,
                                pair_id=pair_id,
                            )
                        )
                    step = {
                        "pair_id": pair_id,
                        "snapshot": pair_snapshot,
                        "errors": step_errors,
                    }
                    steps[step_key] = step
                    try:
                        heartbeat.ensure_owned()
                        self._artifacts.put_json(
                            str(job["draft_key"]),
                            {"version": 2, "steps": steps},
                        )
                    except Exception as exc:
                        job.setdefault("errors", []).append(
                            _error(
                                "draft_persist_failed",
                                str(exc),
                                retryable=True,
                                pair_id=pair_id,
                            )
                        )
                        job["status"] = "failed"
                elif step.get("pair_id") != pair_id:
                    raise RuntimeError(
                        f"preview draft pair mismatch at index {next_index}"
                    )

                if job.get("status") != "failed":
                    job.setdefault("errors", []).extend(step.get("errors", []))
                    next_index += 1
                    job["next_index"] = next_index

            snapshots = _draft_snapshots({"version": 2, "steps": steps})

            if job.get("status") != "failed" and next_index >= len(pair_ids):
                try:
                    heartbeat.ensure_owned()
                    self._publish_snapshot(session_id, data, job, snapshots)
                except Exception as exc:
                    job.setdefault("errors", []).append(
                        _error(
                            "snapshot_publish_failed",
                            str(exc),
                            retryable=True,
                        )
                    )
                    job["status"] = "failed"
                else:
                    job["status"] = "completed_with_errors" if job.get("errors") else "completed"
            elif job.get("status") != "failed":
                job["status"] = "running"
            jobs[job_id] = job
            data["jobs"] = jobs
            heartbeat.ensure_owned()
            try:
                saved = self._repository.save_with_job_lease(
                    session_id,
                    data,
                    expected_version=current.version,
                    job_id=job_id,
                    owner=lease_owner,
                )
            except LeaseConflictError as exc:
                raise JobBusyError(
                    f"preview job lease was lost: {job_id}"
                ) from exc
            return self._job_response(session_id, saved.data, job)
        finally:
            heartbeat.stop()
            self._repository.release_job_lease(session_id, job_id, lease_owner)
            self._cleanup_abandoned_job_artifacts(session_id, job_id, job)

    def change_plan(
        self,
        session_id: str,
        snapshot_id: str,
        selection: Selection,
    ) -> ChangePlan:
        current = self._load(session_id)
        snapshot = self._load_snapshot(session_id, current.data, snapshot_id)
        pair_snapshot = _find_pair_snapshot(snapshot, selection.pair_id)
        ttml_path = self._materialize_upload(session_id, current.data, pair_snapshot.ttml_filename)
        return self._application.plan_selection(pair_snapshot, selection, ttml_path)

    def apply(
        self,
        session_id: str,
        snapshot_id: str,
        selections: Sequence[Selection],
    ) -> dict[str, object]:
        current = self._load(session_id)
        data = _copy_json(current.data)
        snapshot = self._load_snapshot(session_id, data, snapshot_id)
        pair_snapshots = [PairSnapshot.from_dict(pair) for pair in snapshot.get("pairs", [])]
        selection_by_pair = {selection.pair_id: selection for selection in selections}
        if len(selection_by_pair) != len(selections):
            raise InvalidSelectionError("duplicate pair selections are not allowed")
        expected_pairs = {pair.pair_id for pair in pair_snapshots}
        if set(selection_by_pair) != expected_pairs:
            missing = sorted(expected_pairs - set(selection_by_pair))
            extra = sorted(set(selection_by_pair) - expected_pairs)
            raise InvalidSelectionError(f"selections must cover snapshot pairs; missing={missing}, extra={extra}")

        planned: list[tuple[PairSnapshot, Selection, ChangePlan, Path]] = []
        for pair_snapshot in pair_snapshots:
            upload_path = self._materialize_upload(session_id, data, pair_snapshot.ttml_filename)
            selection = selection_by_pair[pair_snapshot.pair_id]
            plan = self._application.plan_selection(pair_snapshot, selection, upload_path)
            planned.append((pair_snapshot, selection, plan, upload_path))

        files: list[dict[str, object]] = []
        output_records: list[dict[str, object]] = []
        output_generation = uuid.uuid4().hex
        output_prefix = (
            f"{_session_prefix(session_id)}/outputs/{output_generation}"
        )
        output_root = self._work_root / session_id / "apply" / output_generation
        for pair_snapshot, _selection, plan, upload_path in planned:
            output_path = output_root / pair_snapshot.ttml_filename
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(upload_path.read_bytes())
            try:
                write_result = TtmlWriter().write(output_path, plan)
                output_key = f"{output_prefix}/{pair_snapshot.ttml_filename}"
                content = output_path.read_bytes()
                self._artifacts.put_bytes(output_key, content)
                status = "applied" if write_result.changed else "unchanged"
                files.append(
                    {
                        "pair_id": pair_snapshot.pair_id,
                        "ttml": pair_snapshot.ttml_filename,
                        "status": status,
                        "output_sha256": write_result.output_sha256,
                        "backup": None,
                        "error": None,
                    }
                )
                output_records.append(
                    {
                        "pair_id": pair_snapshot.pair_id,
                        "filename": pair_snapshot.ttml_filename,
                        "artifact_key": output_key,
                        "sha256": _sha256(content),
                    }
                )
            except Exception as exc:
                files.append(
                    {
                        "pair_id": pair_snapshot.pair_id,
                        "ttml": pair_snapshot.ttml_filename,
                        "status": "failed",
                        "output_sha256": None,
                        "backup": None,
                        "error": _error("apply_failed", str(exc), retryable=False, pair_id=pair_snapshot.pair_id),
                    }
                )

        data["outputs"] = output_records
        try:
            self._repository.save(session_id, data, expected_version=current.version)
        except Exception:
            _delete_prefix_quietly(self._artifacts, output_prefix)
            raise
        finally:
            shutil.rmtree(output_root, ignore_errors=True)
        succeeded = sum(1 for item in files if item["status"] == "applied")
        skipped = sum(1 for item in files if item["status"] == "unchanged")
        failed = sum(1 for item in files if item["status"] == "failed")
        return {
            "snapshot_id": snapshot_id,
            "succeeded": succeeded,
            "failed": failed,
            "skipped": skipped,
            "files": files,
        }

    def get_output(self, session_id: str, filename: str) -> bytes:
        current = self._load(session_id)
        safe_name = _safe_filename(filename)
        record = next(
            (
                record
                for record in current.data.get("outputs", [])
                if record.get("filename") == safe_name
            ),
            None,
        )
        if record is None:
            raise FileNotFoundError(f"output not found: {safe_name}")
        return self._artifacts.get_bytes(str(record["artifact_key"]))

    def get_outputs_zip(self, session_id: str) -> bytes:
        current = self._load(session_id)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for record in sorted(current.data.get("outputs", []), key=lambda item: str(item["filename"]).casefold()):
                archive.writestr(
                    str(record["filename"]),
                    self._artifacts.get_bytes(str(record["artifact_key"])),
                )
        return buffer.getvalue()

    def _load(self, session_id: str) -> VersionedSession:
        current = self._repository.load(session_id)
        if current is None or current.data.get("deleting"):
            raise SessionNotFoundError(f"session not found: {session_id}")
        return current

    def _publish_snapshot(
        self,
        session_id: str,
        data: dict[str, Any],
        job: dict[str, Any],
        pairs: list[dict[str, Any]],
    ) -> None:
        snapshot_id = str(job.get("pending_snapshot_id") or uuid.uuid4().hex)
        snapshot = {
            "version": 1,
            "snapshot_id": snapshot_id,
            "upload_fingerprint": _upload_fingerprint(data),
            "pairs": pairs,
        }
        self._artifacts.put_json(
            _snapshot_key(session_id, snapshot_id),
            snapshot,
        )
        job["snapshot_id"] = snapshot_id
        data["current_snapshot_id"] = snapshot_id

    def _load_snapshot(
        self,
        session_id: str,
        data: dict[str, Any],
        snapshot_id: str,
    ) -> dict[str, Any]:
        validate_identifier(snapshot_id, label="snapshot id")
        if data.get("current_snapshot_id") != snapshot_id:
            raise SnapshotConflictError(f"snapshot is no longer current: {snapshot_id}")
        try:
            snapshot = self._artifacts.get_json(
                _snapshot_key(session_id, snapshot_id)
            )
        except (FileNotFoundError, KeyError) as exc:
            raise SnapshotConflictError(f"snapshot not found: {snapshot_id}") from exc
        if snapshot.get("upload_fingerprint") != _upload_fingerprint(data):
            raise SnapshotConflictError("uploaded files changed after preview")
        return snapshot

    def _materialize_pair(
        self,
        session_id: str,
        data: dict[str, Any],
        pair_id: str,
    ) -> PairingPair:
        pair_data = next(
            (pair for pair in data.get("pairing", {}).get("pairs", []) if pair.get("pair_id") == pair_id),
            None,
        )
        if pair_data is None:
            raise KeyError(f"pair not found: {pair_id}")
        ttml_path = self._materialize_upload(session_id, data, Path(str(pair_data["ttml_path"])).name)
        audio_name = pair_data.get("audio_path")
        audio_path = self._materialize_upload(session_id, data, Path(str(audio_name)).name) if audio_name else None
        return PairingPair(
            pair_id=pair_id,
            status=str(pair_data["status"]),
            ttml_path=ttml_path,
            audio_path=audio_path,
            audio_candidates=tuple(Path(value) for value in pair_data.get("audio_candidates", [])),
        )

    def _materialize_upload(self, session_id: str, data: dict[str, Any], filename: str) -> Path:
        record = next(
            (record for record in data.get("uploads", []) if record.get("filename") == filename),
            None,
        )
        if record is None:
            raise KeyError(f"uploaded file not found: {filename}")
        path = self._work_root / session_id / "uploads" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        content = self._artifacts.get_bytes(str(record["artifact_key"]))
        if _sha256(content) != record.get("sha256"):
            raise SnapshotConflictError(f"uploaded artifact hash mismatch: {filename}")
        path.write_bytes(content)
        return path

    def _job_response(
        self,
        session_id: str,
        data: dict[str, Any],
        job: dict[str, Any],
    ) -> dict[str, object]:
        snapshot_id = job.get("snapshot_id")
        if snapshot_id:
            snapshot = self._artifacts.get_json(
                _snapshot_key(session_id, str(snapshot_id))
            )
            results = list(snapshot.get("pairs", []))
        else:
            results = _draft_snapshots(
                self._artifacts.get_json(str(job["draft_key"]))
            )
        return {
            "job_id": str(job["job_id"]),
            "status": str(job["status"]),
            "total": int(job.get("total", 0)),
            "completed": int(job.get("next_index", 0)),
            "results": results,
            "errors": list(job.get("errors", [])),
            "snapshot_id": snapshot_id,
        }

    def _cleanup_abandoned_job_artifacts(
        self,
        session_id: str,
        job_id: str,
        job: dict[str, Any],
    ) -> None:
        try:
            current = self._repository.load(session_id)
        except Exception:
            return
        if current is None or current.data.get("deleting"):
            try:
                self._artifacts.delete_prefix(_session_prefix(session_id))
            except Exception:
                self._retain_cleanup_tombstone(session_id, current)
            return
        if job_id in current.data.get("jobs", {}):
            return
        _delete_prefix_quietly(
            self._artifacts,
            f"{_session_prefix(session_id)}/jobs/{job_id}",
        )
        snapshot_id = job.get("snapshot_id") or job.get("pending_snapshot_id")
        if snapshot_id:
            _delete_quietly(
                self._artifacts,
                _snapshot_key(session_id, str(snapshot_id)),
            )

    def _retain_cleanup_tombstone(
        self,
        session_id: str,
        current: VersionedSession | None,
    ) -> None:
        marker = {
            "deleting": True,
            "cleanup_token": uuid.uuid4().hex,
        }
        try:
            if current is None:
                self._repository.create(marker, session_id=session_id)
            else:
                self._repository.save(
                    session_id,
                    marker,
                    expected_version=current.version,
                )
            return
        except Exception:
            pass
        try:
            latest = self._repository.load(session_id)
            if latest is None:
                self._repository.create(marker, session_id=session_id)
        except Exception:
            pass


def _empty_session() -> dict[str, object]:
    return {
        "uploads": [],
        "pairing": {"pairs": [], "issues": []},
        "jobs": {},
        "current_snapshot_id": None,
        "outputs": [],
    }


def _get_job(data: dict[str, Any], job_id: str) -> dict[str, Any]:
    job = data.get("jobs", {}).get(job_id)
    if not isinstance(job, dict):
        raise PreviewJobNotFoundError(f"preview job not found: {job_id}")
    return job


def _find_pair_snapshot(snapshot: dict[str, Any], pair_id: str) -> PairSnapshot:
    for pair in snapshot.get("pairs", []):
        if pair.get("pair_id") == pair_id:
            return PairSnapshot.from_dict(pair)
    raise InvalidSelectionError(f"pair not found in snapshot: {pair_id}")


def _upload_fingerprint(data: dict[str, Any]) -> str:
    records = {str(record["filename"]): record for record in data.get("uploads", [])}
    digest = hashlib.sha256()
    uploads = sorted(
        data.get("uploads", []),
        key=lambda record: (
            str(record.get("filename", "")).casefold(),
            str(record.get("filename", "")),
            str(record.get("sha256", "")),
        ),
    )
    for record in uploads:
        digest.update(str(record.get("filename", "")).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(record.get("sha256", "")).encode("ascii"))
        digest.update(b"\0")
    pairs = sorted(data.get("pairing", {}).get("pairs", []), key=lambda pair: str(pair.get("pair_id")))
    for pair in pairs:
        digest.update(str(pair.get("pair_id", "")).encode("utf-8"))
        for key in ("ttml_path", "audio_path"):
            value = pair.get(key)
            if value:
                filename = Path(str(value)).name
                digest.update(filename.encode("utf-8"))
                digest.update(str(records[filename]["sha256"]).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _session_prefix(session_id: str) -> str:
    validate_identifier(session_id, label="session id")
    return f"sessions/{session_id}"


def _snapshot_key(session_id: str, snapshot_id: str) -> str:
    validate_identifier(snapshot_id, label="snapshot id")
    return f"{_session_prefix(session_id)}/snapshots/{snapshot_id}.json"


def _delete_prefix_quietly(artifacts: ArtifactStore, prefix: str) -> None:
    try:
        artifacts.delete_prefix(prefix)
    except Exception:
        pass


def _delete_quietly(artifacts: ArtifactStore, key: str) -> None:
    try:
        artifacts.delete(key)
    except Exception:
        pass


def _safe_filename(filename: str) -> str:
    safe = Path(filename or "uploaded.bin").name
    if safe in {"", ".", ".."}:
        raise ValueError("invalid filename")
    return safe


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _error(code: str, message: str, *, retryable: bool, **details: object) -> dict[str, object]:
    return {"code": code, "message": message, "retryable": retryable, "details": details}


def _copy_json(value: dict[str, Any]) -> dict[str, Any]:
    import json

    return json.loads(json.dumps(value))


def _draft_steps(draft: dict[str, Any]) -> dict[str, dict[str, Any]]:
    steps = draft.get("steps")
    if isinstance(steps, dict):
        return {
            str(index): dict(step)
            for index, step in steps.items()
            if isinstance(step, dict)
        }
    return {
        str(index): {
            "pair_id": snapshot.get("pair_id"),
            "snapshot": snapshot,
            "errors": [],
        }
        for index, snapshot in enumerate(draft.get("pairs", []))
        if isinstance(snapshot, dict)
    }


def _draft_snapshots(draft: dict[str, Any]) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for _, step in sorted(
        _draft_steps(draft).items(),
        key=lambda item: int(item[0]),
    ):
        snapshot = step.get("snapshot")
        if isinstance(snapshot, dict):
            snapshots.append(snapshot)
    return snapshots


class _LeaseHeartbeat:
    def __init__(
        self,
        repository: SessionRepository,
        session_id: str,
        job_id: str,
        owner: str,
        *,
        ttl_seconds: float,
    ) -> None:
        self._repository = repository
        self._session_id = session_id
        self._job_id = job_id
        self._owner = owner
        self._ttl_seconds = ttl_seconds
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"preview-lease-{job_id[:8]}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=min(1.0, self._ttl_seconds))

    def ensure_owned(self) -> None:
        if self._lost.is_set() or not self._renew():
            self._lost.set()
            raise JobBusyError(f"preview job lease was lost: {self._job_id}")

    def _run(self) -> None:
        interval = max(0.05, self._ttl_seconds / 3)
        while not self._stop.wait(interval):
            if not self._renew():
                self._lost.set()
                return

    def _renew(self) -> bool:
        try:
            return self._repository.renew_job_lease(
                self._session_id,
                self._job_id,
                self._owner,
                ttl_seconds=self._ttl_seconds,
            )
        except Exception:
            return False
