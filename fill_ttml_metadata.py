#!/usr/bin/env python3
"""Fill AMLL metadata in TTML files from paired audio metadata."""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol


TTML_NS = "http://www.w3.org/ns/ttml"
TTM_NS = "http://www.w3.org/ns/ttml#metadata"
TTS_NS = "http://www.w3.org/ns/ttml#styling"
AMLL_NS = "http://www.example.com/ns/amll"
XML_NS = "http://www.w3.org/XML/1998/namespace"

DEFAULT_STORE = "cn"
DEFAULT_FALLBACK_STORE = "us"
TARGET_KEY_ORDER = ["musicName", "artists", "album", "appleMusicId", "isrc"]
AUDIO_EXTENSIONS = {
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


@dataclass(frozen=True)
class AudioMetadata:
    title: str | None = None
    artists: list[str] = field(default_factory=list)
    album: str | None = None
    isrc: str | None = None
    catalog_id: str | None = None
    playlist_id: str | None = None
    track_number: int | None = None
    disc_number: int | None = None
    duration_seconds: float | None = None


@dataclass(frozen=True)
class AppleMusicMatch:
    value: str | None
    source: str
    errors: list[str] = field(default_factory=list)


@dataclass
class TtmlUpdateResult:
    added: dict[str, list[str]] = field(default_factory=dict)
    replaced: dict[str, list[str]] = field(default_factory=dict)
    skipped: dict[str, list[str]] = field(default_factory=dict)
    backup_path: Path | None = None

    @property
    def changed(self) -> bool:
        return bool(self.added or self.replaced)


class AppleMusicClientProtocol(Protocol):
    def fetch_album_tracks(self, store: str, album_id: str) -> list[dict[str, Any]]:
        ...


class InMemoryAppleMusicClient:
    def __init__(self, albums: dict[tuple[str, str], list[dict[str, Any]]]):
        self.albums = albums

    def fetch_album_tracks(self, store: str, album_id: str) -> list[dict[str, Any]]:
        tracks = self.albums.get((store, album_id))
        if tracks is None:
            raise LookupError(f"album {album_id} not found in {store}")
        return tracks


class AppleMusicClient:
    def __init__(self, timeout: int = 20):
        self.timeout = timeout
        self._token: str | None = None
        self._page_cache: dict[tuple[str, str], str] = {}
        self._track_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def fetch_album_tracks(self, store: str, album_id: str) -> list[dict[str, Any]]:
        cache_key = (store, album_id)
        if cache_key in self._track_cache:
            return self._track_cache[cache_key]

        try:
            tracks = self._fetch_album_tracks_from_amp_api(store, album_id)
        except Exception:
            tracks = self._fetch_album_tracks_from_json_ld(store, album_id)

        self._track_cache[cache_key] = tracks
        return tracks

    def _fetch_album_tracks_from_amp_api(self, store: str, album_id: str) -> list[dict[str, Any]]:
        token = self._get_bearer_token(store, album_id)
        url = f"https://amp-api.music.apple.com/v1/catalog/{store}/albums/{album_id}"
        data = self._read_text(
            url,
            {
                "Authorization": f"Bearer {token}",
                "Origin": "https://music.apple.com",
                "Referer": "https://music.apple.com/",
            },
        )
        payload = json.loads(data)
        album = payload["data"][0]
        tracks = album.get("relationships", {}).get("tracks", {}).get("data", [])
        return [self._track_from_amp_api_track(track) for track in tracks if track.get("type") == "songs"]

    def _fetch_album_tracks_from_json_ld(self, store: str, album_id: str) -> list[dict[str, Any]]:
        page = self._get_album_page(store, album_id)
        tracks: list[dict[str, Any]] = []
        for script_body in re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            page,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            try:
                payload = json.loads(html.unescape(script_body.strip()))
            except json.JSONDecodeError:
                continue
            for item in _walk_json(payload):
                if isinstance(item, dict) and item.get("@type") == "MusicRecording":
                    track_id = _id_from_url(str(item.get("url") or ""))
                    if not track_id:
                        continue
                    tracks.append(
                        {
                            "id": track_id,
                            "name": item.get("name"),
                            "durationInMillis": _iso8601_duration_to_millis(item.get("duration")),
                        }
                    )
        if not tracks:
            raise LookupError(f"no Apple Music tracks found for {album_id} in {store}")
        return tracks

    def _get_bearer_token(self, store: str, album_id: str) -> str:
        if self._token:
            return self._token
        page = self._get_album_page(store, album_id)
        script_sources = re.findall(
            r'<script[^>]+type=["\']module["\'][^>]+src=["\']([^"\']+)["\']',
            page,
            flags=re.IGNORECASE,
        )
        for source in script_sources:
            script_url = urllib.parse.urljoin("https://music.apple.com/", html.unescape(source))
            script = self._read_text(script_url)
            match = re.search(r'eyJhbGciOiJ[^"\']+', script)
            if match:
                self._token = match.group(0)
                return self._token
        raise LookupError("failed to find Apple Music bearer token")

    def _get_album_page(self, store: str, album_id: str) -> str:
        cache_key = (store, album_id)
        if cache_key not in self._page_cache:
            self._page_cache[cache_key] = self._read_text(
                f"https://music.apple.com/{store}/album/{album_id}"
            )
        return self._page_cache[cache_key]

    def _read_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        request_headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/json,*/*",
        }
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(url, headers=request_headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read().decode("utf-8", "ignore")

    @staticmethod
    def _track_from_amp_api_track(track: dict[str, Any]) -> dict[str, Any]:
        attributes = track.get("attributes", {})
        return {
            "id": str(track.get("id") or ""),
            "name": attributes.get("name"),
            "artistName": attributes.get("artistName"),
            "isrc": attributes.get("isrc"),
            "discNumber": attributes.get("discNumber"),
            "trackNumber": attributes.get("trackNumber"),
            "durationInMillis": attributes.get("durationInMillis"),
        }


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
    )


def split_artists(values: Iterable[Any]) -> list[str]:
    artists: list[str] = []
    for raw_value in values:
        value = str(raw_value).strip()
        if not value:
            continue
        pieces = _split_artist_value(value)
        for piece in pieces:
            if piece and piece not in artists:
                artists.append(piece)
    return artists


def choose_apple_music_id(
    metadata: AudioMetadata,
    client: AppleMusicClientProtocol,
    stores: list[str],
    interactive: bool,
) -> AppleMusicMatch:
    if is_valid_apple_music_song_id(metadata.catalog_id):
        return AppleMusicMatch(str(metadata.catalog_id), "catalog")

    if not metadata.playlist_id:
        return AppleMusicMatch(None, "missing-playlist-id", ["missing ITUNESPLAYLISTID"])

    errors: list[str] = []
    tried_stores: set[str] = set()
    for store in stores:
        if not store or store in tried_stores:
            continue
        tried_stores.add(store)
        result = _match_album_store(metadata, client, store, metadata.playlist_id, errors)
        if result.value:
            return result

    while interactive:
        store = input("cn/us 均未匹配到歌曲，请输入 Apple Music 区域名（直接回车跳过）：").strip().lower()
        if not store:
            break
        if store in tried_stores:
            print(f"已尝试过 {store}，跳过。", file=sys.stderr)
            continue
        tried_stores.add(store)
        result = _match_album_store(metadata, client, store, metadata.playlist_id, errors)
        if result.value:
            return result

    return AppleMusicMatch(None, "not-found", errors)


def update_ttml_metadata(path: Path, values: dict[str, list[str]], dry_run: bool) -> TtmlUpdateResult:
    namespaces = _collect_namespaces(path)
    _register_namespaces(namespaces)

    tree = ET.parse(path)
    root = tree.getroot()
    metadata = _ensure_metadata(root)
    result = TtmlUpdateResult()

    for key in TARGET_KEY_ORDER:
        proposed_values = [value for value in values.get(key, []) if value]
        if not proposed_values:
            continue
        _apply_meta_values(metadata, key, proposed_values, result)

    if result.changed and not dry_run:
        backup_path = _backup_path(path)
        shutil.copy2(path, backup_path)
        result.backup_path = backup_path
        output = _serialize_ttml(root)
        path.write_text(output, encoding="utf-8")

    return result


def values_from_metadata(metadata: AudioMetadata, apple_music_id: str | None) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    if metadata.title:
        values["musicName"] = [metadata.title]
    if metadata.artists:
        values["artists"] = metadata.artists
    if metadata.album:
        values["album"] = [metadata.album]
    if apple_music_id:
        values["appleMusicId"] = [apple_music_id]
    if metadata.isrc:
        values["isrc"] = [metadata.isrc]
    return values


def find_directory_pairs(directory: Path) -> tuple[list[tuple[Path, Path]], list[str]]:
    ttml_files = sorted(directory.glob("*.ttml"))
    audio_by_stem: dict[str, list[Path]] = {}
    for child in directory.iterdir():
        if child.is_file() and child.suffix.lower() in AUDIO_EXTENSIONS:
            audio_by_stem.setdefault(child.stem, []).append(child)

    pairs: list[tuple[Path, Path]] = []
    warnings: list[str] = []
    for ttml in ttml_files:
        matches = sorted(audio_by_stem.get(ttml.stem, []), key=lambda path: (path.suffix.lower(), path.name.lower()))
        if len(matches) == 1:
            pairs.append((matches[0], ttml))
        elif not matches:
            warnings.append(f"{ttml.name}: no same-stem audio file found")
        else:
            flac_matches = [match for match in matches if match.suffix.lower() == ".flac"]
            if len(flac_matches) == 1:
                pairs.append((flac_matches[0], ttml))
            else:
                names = ", ".join(match.name for match in matches)
                warnings.append(f"{ttml.name}: multiple same-stem audio files found: {names}")
    return pairs, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fill AMLL TTML metadata from paired audio files.")
    parser.add_argument("path", nargs="?", default=".", help="directory to batch-process")
    parser.add_argument("--audio", type=Path, help="single audio file")
    parser.add_argument("--ttml", type=Path, help="single TTML file")
    parser.add_argument("--dry-run", action="store_true", help="show changes without writing files")
    parser.add_argument("--store", default=DEFAULT_STORE, help="first Apple Music storefront to try")
    parser.add_argument("--fallback-store", default=DEFAULT_FALLBACK_STORE, help="fallback Apple Music storefront")
    parser.add_argument("--non-interactive", action="store_true", help="do not prompt for extra storefronts")
    args = parser.parse_args(argv)

    if bool(args.audio) != bool(args.ttml):
        parser.error("--audio and --ttml must be provided together")

    if args.audio and args.ttml:
        pairs = [(args.audio, args.ttml)]
        warnings: list[str] = []
    else:
        directory = Path(args.path)
        if not directory.is_dir():
            parser.error(f"{directory} is not a directory")
        pairs, warnings = find_directory_pairs(directory)

    for warning in warnings:
        print(f"[skip] {warning}")

    client = AppleMusicClient()
    stores = [args.store.lower(), args.fallback_store.lower()]
    failures = 0
    for audio_path, ttml_path in pairs:
        try:
            _process_pair(
                audio_path,
                ttml_path,
                client,
                stores,
                interactive=not args.non_interactive,
                dry_run=args.dry_run,
            )
        except Exception as exc:
            failures += 1
            print(f"[error] {ttml_path.name}: {exc}", file=sys.stderr)

    return 1 if failures else 0


def _process_pair(
    audio_path: Path,
    ttml_path: Path,
    client: AppleMusicClientProtocol,
    stores: list[str],
    interactive: bool,
    dry_run: bool,
) -> None:
    metadata = read_audio_metadata(audio_path)
    match = choose_apple_music_id(metadata, client, stores, interactive)
    values = values_from_metadata(metadata, match.value)
    result = update_ttml_metadata(ttml_path, values, dry_run=dry_run)

    status = "dry-run" if dry_run else "updated"
    if not result.changed:
        status = "unchanged"
    print(f"[{status}] {ttml_path.name}")
    print(f"  audio: {audio_path.name}")
    print(f"  appleMusicId: {match.value or '-'} ({match.source})")
    if match.errors:
        for error in match.errors:
            print(f"  lookup warning: {error}")
    _print_change_group("added", result.added)
    _print_change_group("replaced", result.replaced)
    _print_change_group("skipped", result.skipped)
    if result.backup_path:
        print(f"  backup: {result.backup_path}")


def _print_change_group(label: str, changes: dict[str, list[str]]) -> None:
    for key, values in changes.items():
        joined = ", ".join(values)
        print(f"  {label}: {key} = {joined}")


def _serialize_ttml(root: ET.Element) -> str:
    output = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    return re.sub(r"(<[^<>]*?)\s+/>", r"\1/>", output)


def _apply_meta_values(
    metadata: ET.Element,
    key: str,
    proposed_values: list[str],
    result: TtmlUpdateResult,
) -> None:
    existing = [
        child
        for child in list(metadata)
        if child.tag == f"{{{AMLL_NS}}}meta" and child.attrib.get("key") == key
    ]
    real_values = [child.attrib.get("value", "") for child in existing if not _is_placeholder(child.attrib.get("value"))]
    placeholders = [child for child in existing if _is_placeholder(child.attrib.get("value"))]

    if real_values:
        result.skipped[key] = real_values
        return

    if placeholders:
        consumed = 0
        for element, value in zip(placeholders, proposed_values):
            element.set("value", value)
            consumed += 1
        for extra in placeholders[consumed:]:
            metadata.remove(extra)
        remaining = proposed_values[consumed:]
        result.replaced[key] = proposed_values
        if remaining:
            _insert_meta_values(metadata, key, remaining)
        return

    _insert_meta_values(metadata, key, proposed_values)
    result.added[key] = proposed_values


def _insert_meta_values(metadata: ET.Element, key: str, values: list[str]) -> None:
    index = _metadata_insert_index(metadata)
    for offset, value in enumerate(values):
        element = ET.Element(f"{{{AMLL_NS}}}meta", {"key": key, "value": value})
        metadata.insert(index + offset, element)


def _metadata_insert_index(metadata: ET.Element) -> int:
    children = list(metadata)
    for index, child in enumerate(children):
        if _local_name(child.tag) == "iTunesMetadata":
            return index
    index = 0
    for child in children:
        if child.tag == f"{{{TTM_NS}}}agent" or child.tag == f"{{{AMLL_NS}}}meta":
            index += 1
            continue
        break
    return index


def _ensure_metadata(root: ET.Element) -> ET.Element:
    head = root.find(f"{{{TTML_NS}}}head")
    if head is None:
        head = ET.Element(f"{{{TTML_NS}}}head")
        root.insert(0, head)
    metadata = head.find(f"{{{TTML_NS}}}metadata")
    if metadata is None:
        metadata = ET.Element(f"{{{TTML_NS}}}metadata")
        head.insert(0, metadata)
    return metadata


def _collect_namespaces(path: Path) -> dict[str, str]:
    namespaces: dict[str, str] = {}
    for _, item in ET.iterparse(path, events=("start-ns",)):
        prefix, uri = item
        namespaces[prefix or ""] = uri
    return namespaces


def _register_namespaces(namespaces: dict[str, str]) -> None:
    merged = {
        "": namespaces.get("", TTML_NS),
        "ttm": namespaces.get("ttm", TTM_NS),
        "tts": namespaces.get("tts", TTS_NS),
        "amll": namespaces.get("amll", AMLL_NS),
    }
    for prefix, uri in namespaces.items():
        if prefix == "xml":
            continue
        merged[prefix] = uri
    for prefix, uri in merged.items():
        if prefix == "xml":
            continue
        ET.register_namespace(prefix, uri)


def _backup_path(path: Path) -> Path:
    candidate = path.with_suffix(path.suffix + ".bak")
    if not candidate.exists():
        return candidate
    counter = 1
    while True:
        numbered = path.with_suffix(path.suffix + f".bak{counter}")
        if not numbered.exists():
            return numbered
        counter += 1


def _match_album_store(
    metadata: AudioMetadata,
    client: AppleMusicClientProtocol,
    store: str,
    album_id: str,
    errors: list[str],
) -> AppleMusicMatch:
    try:
        tracks = client.fetch_album_tracks(store, album_id)
    except Exception as exc:
        errors.append(f"{store}: {exc}")
        return AppleMusicMatch(None, f"album:{store}:error", errors)

    track_match = _match_by_track_number(metadata, tracks)
    if track_match:
        return AppleMusicMatch(track_match, f"album:{store}:track", errors)

    title_match = _match_by_title(metadata, tracks)
    if title_match:
        return AppleMusicMatch(title_match, f"album:{store}:title", errors)

    errors.append(f"{store}: no matching track in album {album_id}")
    return AppleMusicMatch(None, f"album:{store}:not-found", errors)


def _match_by_track_number(metadata: AudioMetadata, tracks: list[dict[str, Any]]) -> str | None:
    if metadata.track_number is None:
        return None
    candidates = []
    for track in tracks:
        if _parse_number(track.get("trackNumber")) != metadata.track_number:
            continue
        if metadata.disc_number is not None and _parse_number(track.get("discNumber")) != metadata.disc_number:
            continue
        candidates.append(track)

    if not candidates:
        return None
    if not metadata.title and len(candidates) == 1:
        return _track_id(candidates[0])

    normalized_title = _normalize_title(metadata.title)
    for candidate in candidates:
        if _normalize_title(candidate.get("name")) == normalized_title:
            return _track_id(candidate)
    return None


def _match_by_title(metadata: AudioMetadata, tracks: list[dict[str, Any]]) -> str | None:
    normalized_title = _normalize_title(metadata.title)
    if not normalized_title:
        return None

    candidates = [track for track in tracks if _normalize_title(track.get("name")) == normalized_title]
    if len(candidates) == 1:
        return _track_id(candidates[0])

    if metadata.duration_seconds is not None:
        timed = [
            track
            for track in candidates or tracks
            if _normalize_title(track.get("name")) == normalized_title
            and _duration_close(metadata.duration_seconds, track.get("durationInMillis"))
        ]
        if len(timed) == 1:
            return _track_id(timed[0])
    return None


def _duration_close(seconds: float, millis: Any, tolerance_seconds: float = 2.0) -> bool:
    try:
        track_seconds = float(millis) / 1000
    except (TypeError, ValueError):
        return False
    return abs(track_seconds - seconds) <= tolerance_seconds


def is_valid_apple_music_song_id(value: str | None) -> bool:
    if not value:
        return False
    value = str(value).strip()
    return value.isdigit() and int(value) >= 100000


def _track_id(track: dict[str, Any]) -> str | None:
    value = str(track.get("id") or "").strip()
    return value or None


def _normalize_title(value: Any) -> str:
    if value is None:
        return ""
    normalized = str(value).casefold().strip()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized.replace("’", "'").replace("`", "'")
    return normalized


def _split_artist_value(value: str) -> list[str]:
    if ";" in value:
        return [part.strip() for part in value.split(";") if part.strip()]
    if "," not in value:
        return [value]
    pieces: list[str] = []
    for comma_part in value.split(","):
        pieces.extend(part.strip() for part in re.split(r"\s+&\s+", comma_part) if part.strip())
    return pieces


def _flatten_tags(tags: Any) -> dict[str, list[Any]]:
    flattened: dict[str, list[Any]] = {}
    for key in tags.keys():
        try:
            raw_value = tags[key]
        except Exception:
            continue
        values = _coerce_tag_values(raw_value)
        flattened.setdefault(str(key).casefold(), []).extend(values)
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


def _stringify_tag_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", "ignore")
    if isinstance(value, tuple):
        value = "/".join(str(part) for part in value if part)
    text = str(value).strip()
    return text or None


def _parse_number(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, tuple | list):
        if not value:
            return None
        value = value[0]
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def _is_placeholder(value: str | None) -> bool:
    return value is None or value.strip() in {"", "*"}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _walk_json(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _id_from_url(url: str) -> str | None:
    match = re.search(r"/(\d+)(?:[/?#].*)?$", url)
    return match.group(1) if match else None


def _iso8601_duration_to_millis(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(
        r"P(?:T)?(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?",
        value,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    return ((hours * 60 + minutes) * 60 + seconds) * 1000


if __name__ == "__main__":
    raise SystemExit(main())
