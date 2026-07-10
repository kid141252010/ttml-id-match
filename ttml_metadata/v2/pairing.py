from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal


PairStatus = Literal["paired", "ttml_only", "ambiguous"]

AUDIO_EXTENSIONS = frozenset(
    {
        ".aac",
        ".aif",
        ".aiff",
        ".alac",
        ".ape",
        ".flac",
        ".m4a",
        ".m4b",
        ".m4p",
        ".mp3",
        ".mp4",
        ".ogg",
        ".opus",
        ".wav",
        ".wma",
    }
)


@dataclass(frozen=True)
class PairingPair:
    pair_id: str
    status: PairStatus
    ttml_path: Path
    audio_path: Path | None = None
    audio_candidates: tuple[Path, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "pair_id": self.pair_id,
            "status": self.status,
            "ttml_path": self.ttml_path.as_posix(),
            "audio_path": self.audio_path.as_posix() if self.audio_path is not None else None,
            "audio_candidates": [path.as_posix() for path in self.audio_candidates],
        }


@dataclass(frozen=True)
class PairingIssue:
    code: str
    pair_id: str
    ttml_path: Path
    audio_candidates: tuple[Path, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "pair_id": self.pair_id,
            "ttml_path": self.ttml_path.as_posix(),
            "audio_candidates": [path.as_posix() for path in self.audio_candidates],
        }


@dataclass(frozen=True)
class PairingPlan:
    pairs: tuple[PairingPair, ...]
    issues: tuple[PairingIssue, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "pairs": [pair.to_dict() for pair in self.pairs],
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _normalized_stem(path: Path) -> str:
    return unicodedata.normalize("NFKC", path.stem).strip().casefold()


def _path_sort_key(path: Path) -> tuple[str, str]:
    return (
        unicodedata.normalize("NFKC", path.name).strip().casefold(),
        path.as_posix(),
    )


def stable_pair_id(ttml_path: str | Path) -> str:
    ttml_path = Path(ttml_path)
    normalized_name = f"{_normalized_stem(ttml_path)}.ttml"
    digest = hashlib.sha256(normalized_name.encode("utf-8")).hexdigest()[:16]
    return f"pair-{digest}"


def build_pairing_plan(files: Iterable[str | Path]) -> PairingPlan:
    paths = [Path(file) for file in files]
    ttml_files = sorted(
        (path for path in paths if path.suffix.lower() == ".ttml"),
        key=_path_sort_key,
    )
    audio_files = [path for path in paths if path.suffix.lower() in AUDIO_EXTENSIONS]
    ttml_by_key: dict[str, list[Path]] = {}
    for ttml_path in ttml_files:
        ttml_by_key.setdefault(_normalized_stem(ttml_path), []).append(ttml_path)
    pairs: list[PairingPair] = []
    issues: list[PairingIssue] = []
    collision_occurrences: dict[tuple[str, str], int] = {}
    for ttml_path in ttml_files:
        normalized_stem = _normalized_stem(ttml_path)
        pair_id = stable_pair_id(ttml_path)
        if len(ttml_by_key[normalized_stem]) > 1:
            logical_name = unicodedata.normalize("NFKC", ttml_path.name).strip()
            occurrence_key = (normalized_stem, logical_name)
            occurrence = collision_occurrences.get(occurrence_key, 0)
            collision_occurrences[occurrence_key] = occurrence + 1
            pair_id = _collision_pair_id(pair_id, logical_name, occurrence)
            issues.append(
                PairingIssue(
                    code="duplicate_ttml_key",
                    pair_id=pair_id,
                    ttml_path=ttml_path,
                    audio_candidates=(),
                )
            )
        matches = sorted(
            (audio_path for audio_path in audio_files if _normalized_stem(audio_path) == normalized_stem),
            key=_path_sort_key,
        )
        flac_matches = [match for match in matches if match.suffix.lower() == ".flac"]
        selected_audio = matches[0] if len(matches) == 1 else flac_matches[0] if len(flac_matches) == 1 else None
        if selected_audio is not None:
            pairs.append(
                PairingPair(
                    pair_id=pair_id,
                    status="paired",
                    ttml_path=ttml_path,
                    audio_path=selected_audio,
                )
            )
        elif not matches:
            pairs.append(
                PairingPair(
                    pair_id=pair_id,
                    status="ttml_only",
                    ttml_path=ttml_path,
                )
            )
        else:
            audio_candidates = tuple(matches)
            pair = PairingPair(
                pair_id=pair_id,
                status="ambiguous",
                ttml_path=ttml_path,
                audio_candidates=audio_candidates,
            )
            pairs.append(pair)
            issues.append(
                PairingIssue(
                    code="ambiguous_audio",
                    pair_id=pair.pair_id,
                    ttml_path=ttml_path,
                    audio_candidates=audio_candidates,
                )
            )
    return PairingPlan(pairs=tuple(pairs), issues=tuple(issues))


def _collision_pair_id(
    base_pair_id: str,
    logical_name: str,
    occurrence: int,
) -> str:
    signature = f"{logical_name}\0{occurrence}"
    suffix = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:8]
    return f"{base_pair_id}-{suffix}"
