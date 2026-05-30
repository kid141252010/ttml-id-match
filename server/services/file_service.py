from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable

from fastapi import UploadFile

from server.models.schemas import FilePair, SessionFile
from ttml_metadata.models import AUDIO_EXTENSIONS


def classify_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".ttml":
        return "ttml"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    return "other"


async def save_uploads(upload_dir: Path, files: Iterable[UploadFile]) -> list[SessionFile]:
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved: list[SessionFile] = []
    for upload in files:
        filename = Path(upload.filename or "uploaded.bin").name
        target = unique_path(upload_dir / filename)
        with target.open("wb") as output:
            while chunk := await upload.read(1024 * 1024):
                output.write(chunk)
        saved.append(SessionFile(name=target.name, size=target.stat().st_size, kind=classify_file(target)))
    return saved


def list_session_files(upload_dir: Path) -> list[SessionFile]:
    files = []
    for path in sorted(upload_dir.iterdir(), key=lambda item: item.name.lower()):
        if path.is_file():
            files.append(SessionFile(name=path.name, size=path.stat().st_size, kind=classify_file(path)))
    return files


def pair_session_files(upload_dir: Path) -> list[FilePair]:
    files = list_session_files(upload_dir)
    audio_by_stem: dict[str, Path] = {}
    for file in files:
        if file.kind == "audio":
            path = upload_dir / file.name
            current = audio_by_stem.get(path.stem.casefold())
            if current is None or (path.suffix.lower() == ".flac" and current.suffix.lower() != ".flac"):
                audio_by_stem[path.stem.casefold()] = path

    pairs: list[FilePair] = []
    for file in [item for item in files if item.kind == "ttml"]:
        ttml_path = upload_dir / file.name
        audio = audio_by_stem.get(ttml_path.stem.casefold())
        pairs.append(
            FilePair(
                id=f"pair-{len(pairs) + 1}",
                ttml=ttml_path.name,
                audio=audio.name if audio else None,
                status="paired" if audio else "ttml_only",
            )
        )
    return pairs


def copy_uploads_to_outputs(upload_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for path in upload_dir.iterdir():
        if path.is_file():
            shutil.copy2(path, output_dir / path.name)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    counter = 1
    while True:
        candidate = path.with_name(f"{stem}-{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1
