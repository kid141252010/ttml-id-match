from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import AudioMetadata
from .text_utils import _normalize_release_date, _parse_number, _stringify_tag_value, split_artists

def read_audio_metadata(path: Path) -> AudioMetadata:
    try:
        from mutagen import File
    except ModuleNotFoundError as exc:
        raise RuntimeError("mutagen is required. Install it with: python -m pip install -r requirements.txt") from exc

    audio = File(path)
    if audio is None or audio.tags is None:
        raise ValueError(f"unsupported or untagged audio file: {path}")

    tags = _flatten_tags(audio.tags)
    duration = getattr(getattr(audio, "info", None), "length", None)

    title = _first_tag(tags, "title", "\xa9nam")
    raw_artists = _tag_values(tags, "artist", "artists", "\xa9ART")
    album = _first_tag(tags, "album", "\xa9alb")
    isrc = _first_tag(tags, "isrc", "tsrc")
    catalog_id = _first_tag(tags, "itunescatalogid")
    playlist_id = _first_tag(tags, "itunesplaylistid")
    track_number = _parse_number(_first_tag(tags, "track", "tracknumber", "trkn"))
    disc_number = _parse_number(_first_tag(tags, "disc", "discnumber", "disk"))
    release_date = _normalize_release_date(
        _first_tag(
            tags,
            "date",
            "year",
            "originaldate",
            "originalyear",
            "releasedate",
            "release_date",
            "\xa9day",
            "tdrc",
            "tdor",
        )
    )

    return AudioMetadata(
        title=title,
        artists=split_artists(raw_artists),
        album=album,
        isrc=isrc,
        catalog_id=catalog_id,
        playlist_id=playlist_id,
        track_number=track_number,
        disc_number=disc_number,
        duration_seconds=float(duration) if duration is not None else None,
        release_date=release_date,
    )


def _flatten_tags(tags: Any) -> dict[str, list[Any]]:
    flattened: dict[str, list[Any]] = {}
    for key in tags.keys():
        try:
            raw_value = tags[key]
        except Exception:
            continue
        values = _coerce_tag_values(raw_value)
        normalized_key = _normalize_tag_key(str(key))
        flattened.setdefault(normalized_key, []).extend(
            value for value in (_stringify_tag_value(value) for value in values) if value
        )
    return flattened


def _coerce_tag_values(raw_value: Any) -> list[Any]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list | tuple):
        if len(raw_value) == 1 and isinstance(raw_value[0], tuple):
            return list(raw_value[0])
        return list(raw_value)
    text = getattr(raw_value, "text", None)
    if text is not None:
        return list(text) if isinstance(text, list) else [text]
    return [raw_value]


def _normalize_tag_key(key: str) -> str:
    normalized = key.casefold()
    aliases = {
        "cnid": "itunescatalogid",
        "plid": "itunesplaylistid",
        "atid": "itunesalbumtitleid",
        "----:com.apple.itunes:isrc": "isrc",
        "----:com.apple.itunes:barcode": "barcode",
    }
    return aliases.get(normalized, normalized)


def _tag_values(tags: dict[str, list[Any]], *names: str) -> list[str]:
    values: list[str] = []
    for name in names:
        for value in tags.get(name.casefold(), []):
            text = _stringify_tag_value(value)
            if text:
                values.append(text)
    return values


def _first_tag(tags: dict[str, list[Any]], *names: str) -> str | None:
    values = _tag_values(tags, *names)
    return values[0] if values else None
