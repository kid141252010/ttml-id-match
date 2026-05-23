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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol


AMLL_NS = "http://www.example.com/ns/amll"

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


@dataclass(frozen=True)
class _XmlAttribute:
    value: str
    value_start: int
    value_end: int


@dataclass(frozen=True)
class _MetaTag:
    start: int
    end: int
    attrs: dict[str, _XmlAttribute]


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
        return AppleMusicMatch(
            None,
            "missing-apple-music-id",
            ["音频中未读取到 Apple Music 歌曲 ID 或专辑 ID"],
        )

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
    text = path.read_text(encoding="utf-8")
    text, amll_prefix = _ensure_amll_namespace(text)
    metadata_start, metadata_end = _find_metadata_inner_bounds(text)
    metadata = text[metadata_start:metadata_end]
    result = TtmlUpdateResult()

    for key in TARGET_KEY_ORDER:
        proposed_values = [value for value in values.get(key, []) if value]
        if not proposed_values:
            continue
        metadata = _apply_meta_values(metadata, amll_prefix, key, proposed_values, result)

    if result.changed and not dry_run:
        backup_path = _backup_path(path)
        shutil.copy2(path, backup_path)
        result.backup_path = backup_path
        output = text[:metadata_start] + metadata + text[metadata_end:]
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


def _find_metadata_inner_bounds(text: str) -> tuple[int, int]:
    open_match = re.search(r"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?metadata)\b[^>]*>", text)
    if not open_match:
        raise ValueError("missing <metadata>; refusing to create TTML metadata nodes")

    tag_name = open_match.group("tag")
    close_match = re.search(rf"</{re.escape(tag_name)}\s*>", text[open_match.end() :])
    if not close_match:
        raise ValueError(f"missing </{tag_name}>; refusing to rewrite TTML")

    return open_match.end(), open_match.end() + close_match.start()


def _find_amll_prefix(text: str) -> str:
    prefixes: list[str] = []
    for match in re.finditer(
        r"\bxmlns:(?P<prefix>[A-Za-z_][\w.-]*)\s*=\s*(?P<quote>[\"'])(?P<uri>.*?)\2",
        text,
        flags=re.DOTALL,
    ):
        if html.unescape(match.group("uri")) == AMLL_NS:
            prefixes.append(match.group("prefix"))

    if not prefixes:
        raise ValueError("missing AMLL namespace")
    if "amll" in prefixes:
        return "amll"
    return prefixes[0]


def _ensure_amll_namespace(text: str) -> tuple[str, str]:
    try:
        return text, _find_amll_prefix(text)
    except ValueError:
        pass

    root_match = re.search(r"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?tt)\b[^>]*>", text, flags=re.DOTALL)
    if not root_match:
        raise ValueError("missing <tt> root; refusing to add AMLL namespace declaration")

    root_tag = root_match.group(0)
    amll_prefix_match = re.search(
        r"\bxmlns:amll\s*=\s*(?P<quote>[\"'])(?P<uri>.*?)\1",
        root_tag,
        flags=re.DOTALL,
    )
    if amll_prefix_match and html.unescape(amll_prefix_match.group("uri")) != AMLL_NS:
        raise ValueError("xmlns:amll already uses a different namespace; refusing to rewrite TTML")

    insert_at = root_match.end() - 1
    insertion = f' xmlns:amll="{AMLL_NS}"'
    return text[:insert_at] + insertion + text[insert_at:], "amll"


def _apply_meta_values(
    metadata: str,
    amll_prefix: str,
    key: str,
    proposed_values: list[str],
    result: TtmlUpdateResult,
) -> str:
    existing = [
        tag
        for tag in _iter_amll_meta_tags(metadata, amll_prefix)
        if _xml_attr_value(tag, "key") == key
    ]
    real_values = [
        _xml_attr_value(tag, "value") or ""
        for tag in existing
        if not _is_placeholder(_xml_attr_value(tag, "value"))
    ]
    placeholders = [tag for tag in existing if _is_placeholder(_xml_attr_value(tag, "value"))]

    if real_values:
        result.skipped[key] = real_values
        return metadata

    if placeholders:
        replacements: list[tuple[int, int, str]] = []
        for tag, value in zip(placeholders, proposed_values):
            value_attr = tag.attrs.get("value")
            if value_attr:
                replacements.append((value_attr.value_start, value_attr.value_end, _escape_xml_attr(value)))
            else:
                replacements.append((tag.start, tag.end, _make_meta_node(amll_prefix, key, value)))
        consumed_count = min(len(placeholders), len(proposed_values))
        for extra in placeholders[consumed_count:]:
            replacements.append((extra.start, extra.end, ""))
        metadata = _apply_text_replacements(metadata, replacements)

        remaining = proposed_values[consumed_count:]
        result.replaced[key] = proposed_values
        if remaining:
            metadata = _insert_meta_values(metadata, amll_prefix, key, remaining)
        return metadata

    metadata = _insert_meta_values(metadata, amll_prefix, key, proposed_values)
    result.added[key] = proposed_values
    return metadata


def _iter_amll_meta_tags(metadata: str, amll_prefix: str) -> Iterable[_MetaTag]:
    pattern = re.compile(
        rf"<{re.escape(amll_prefix)}:meta\b[^<>]*(?:/>|>\s*</{re.escape(amll_prefix)}:meta\s*>)",
        flags=re.DOTALL,
    )
    for match in pattern.finditer(metadata):
        yield _MetaTag(match.start(), match.end(), _parse_xml_attributes(match.group(0), match.start()))


def _parse_xml_attributes(tag_text: str, absolute_start: int) -> dict[str, _XmlAttribute]:
    attrs: dict[str, _XmlAttribute] = {}
    for match in re.finditer(
        r"(?P<name>[^\s=/>]+)\s*=\s*(?P<quote>[\"'])(?P<value>.*?)\2",
        tag_text,
        flags=re.DOTALL,
    ):
        attrs[match.group("name")] = _XmlAttribute(
            value=html.unescape(match.group("value")),
            value_start=absolute_start + match.start("value"),
            value_end=absolute_start + match.end("value"),
        )
    return attrs


def _xml_attr_value(tag: _MetaTag, name: str) -> str | None:
    attr = tag.attrs.get(name)
    return attr.value if attr else None


def _apply_text_replacements(text: str, replacements: list[tuple[int, int, str]]) -> str:
    output = text
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        output = output[:start] + replacement + output[end:]
    return output


def _insert_meta_values(metadata: str, amll_prefix: str, key: str, values: list[str]) -> str:
    insertion = "".join(_make_meta_node(amll_prefix, key, value) for value in values)
    index = _metadata_insert_index(metadata)
    return metadata[:index] + insertion + metadata[index:]


def _metadata_insert_index(metadata: str) -> int:
    match = re.search(r"<(?:[A-Za-z_][\w.-]*:)?iTunesMetadata\b", metadata)
    return match.start() if match else len(metadata)


def _make_meta_node(amll_prefix: str, key: str, value: str) -> str:
    return f'<{amll_prefix}:meta key="{_escape_xml_attr(key)}" value="{_escape_xml_attr(value)}"/>'


def _escape_xml_attr(value: str) -> str:
    return html.escape(str(value), quote=True)


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
