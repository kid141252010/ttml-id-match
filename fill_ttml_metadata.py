#!/usr/bin/env python3
"""Fill AMLL metadata in TTML files from paired audio metadata."""

from __future__ import annotations

import argparse
import concurrent.futures
import html
import json
import re
import shutil
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol


AMLL_NS = "http://www.example.com/ns/amll"

DEFAULT_STORES = ["cn", "tw", "jp", "kr", "us"]
DEFAULT_NCM_API_BASES = [
    "https://music163.xuanmou.com.cn",
    "https://neteasecloudmusicapi-main-api.vercel.app",
    "https://api-enhanced-six-beta.vercel.app",
]
TARGET_KEY_ORDER = ["musicName", "artists", "album", "qqMusicId", "ncmMusicId", "appleMusicId", "isrc"]
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
class AppleMusicTrackMatch:
    track: dict[str, Any] | None
    source: str


@dataclass
class AppleMusicMetadataResult:
    values: dict[str, list[str]] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class QQMusicCandidate:
    song_id: str
    mid: str
    title: str | None = None
    subtitle: str | None = None
    artists: list[str] = field(default_factory=list)
    album: str | None = None
    source_index: int = 0


@dataclass
class QQMusicSearchResult:
    candidates: list[QQMusicCandidate] = field(default_factory=list)
    selected: QQMusicCandidate | None = None
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NCMusicCandidate:
    song_id: str
    title: str | None = None
    aliases: list[str] = field(default_factory=list)
    artists: list[str] = field(default_factory=list)
    album: str | None = None
    source_index: int = 0


@dataclass
class NCMusicSearchResult:
    candidates: list[NCMusicCandidate] = field(default_factory=list)
    selected: NCMusicCandidate | None = None
    errors: list[str] = field(default_factory=list)


@dataclass
class PairMetadata:
    audio_path: Path | None
    ttml_path: Path
    metadata: AudioMetadata
    apple_music_metadata: AppleMusicMetadataResult
    qq_music_metadata: QQMusicSearchResult
    ncm_music_metadata: NCMusicSearchResult = field(default_factory=NCMusicSearchResult)


@dataclass(frozen=True)
class WorkItem:
    ttml_path: Path
    audio_path: Path | None = None


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


class QQMusicClientProtocol(Protocol):
    def search_songs(self, query: str) -> list[QQMusicCandidate]:
        ...


class NCMusicClientProtocol(Protocol):
    def search_songs(self, query: str) -> list[NCMusicCandidate]:
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
        album_name = album.get("attributes", {}).get("name")
        tracks = album.get("relationships", {}).get("tracks", {}).get("data", [])
        return [self._track_from_amp_api_track(track, album_name) for track in tracks if track.get("type") == "songs"]

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
    def _track_from_amp_api_track(track: dict[str, Any], album_name: Any = None) -> dict[str, Any]:
        attributes = track.get("attributes", {})
        return {
            "id": str(track.get("id") or ""),
            "name": attributes.get("name"),
            "artistName": attributes.get("artistName"),
            "albumName": album_name,
            "isrc": attributes.get("isrc"),
            "discNumber": attributes.get("discNumber"),
            "trackNumber": attributes.get("trackNumber"),
            "durationInMillis": attributes.get("durationInMillis"),
        }


class QQMusicClient:
    def __init__(self, timeout: int = 20):
        self.timeout = timeout

    def search_songs(self, query: str) -> list[QQMusicCandidate]:
        request = self._build_search_request(query)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "ignore"))
        return _parse_qq_music_candidates(payload)

    def _build_search_request(self, query: str) -> urllib.request.Request:
        data = json.dumps(_qq_music_search_payload(query), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return urllib.request.Request(
            "http://u.y.qq.com/cgi-bin/musicu.fcg",
            data=data,
            headers={
                "Accept-Language": "zh-CN",
                "Accept": "application/json",
                "User-Agent": "QQMusic 14090508(android 12)",
                "Content-Type": "application/json",
            },
            method="POST",
        )


class NCMusicClient:
    def __init__(
        self,
        timeout: int = 20,
        api_bases: Iterable[str] | None = None,
        read_json: Callable[[str], dict[str, Any]] | None = None,
    ):
        self.timeout = timeout
        self.api_bases = [base.rstrip("/") for base in (api_bases or DEFAULT_NCM_API_BASES) if base]
        self._read_json = read_json or self._read_json_from_url

    def search_songs(self, query: str) -> list[NCMusicCandidate]:
        if not self.api_bases:
            return []

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(self.api_bases))
        futures = {
            executor.submit(self._search_base, base, query): base
            for base in self.api_bases
        }
        errors: list[str] = []
        successful_responses = 0
        try:
            for future in concurrent.futures.as_completed(futures):
                base = futures[future]
                try:
                    candidates = future.result()
                except Exception as exc:
                    errors.append(f"{base}: {exc}")
                    continue

                successful_responses += 1
                if candidates:
                    return candidates

            if errors and not successful_responses:
                raise LookupError("; ".join(errors))
            return []
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _search_base(self, base: str, query: str) -> list[NCMusicCandidate]:
        url = self._build_search_url(base, query)
        payload = self._read_json(url)
        return _parse_ncm_music_candidates(payload)

    @staticmethod
    def _build_search_url(base: str, query: str) -> str:
        params = urllib.parse.urlencode({"keywords": query})
        return f"{base.rstrip('/')}/cloudsearch?{params}"

    def _read_json_from_url(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json,text/plain,*/*",
                "User-Agent": "Mozilla/5.0",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "ignore"))
        if not isinstance(payload, dict):
            raise ValueError("NCM API returned a non-object payload")
        return payload


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


def read_ttml_metadata(path: Path) -> AudioMetadata:
    text = path.read_text(encoding="utf-8")
    metadata_start, metadata_end = _find_metadata_inner_bounds(text)
    metadata = text[metadata_start:metadata_end]
    amll_prefix = _find_amll_prefix(text)
    values: dict[str, list[str]] = {}

    for tag in _iter_amll_meta_tags(metadata, amll_prefix):
        key = _xml_attr_value(tag, "key")
        if key not in {"musicName", "artists", "album"}:
            continue
        value = _real_meta_value(_xml_attr_value(tag, "value"))
        if value:
            _add_unique_value(values, key, value)

    return AudioMetadata(
        title=values.get("musicName", [None])[0],
        artists=split_artists(values.get("artists", [])),
        album=values.get("album", [None])[0],
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


def collect_apple_music_metadata(
    metadata: AudioMetadata,
    client: AppleMusicClientProtocol,
    stores: list[str] | None = None,
) -> AppleMusicMetadataResult:
    result = AppleMusicMetadataResult()
    if is_valid_apple_music_song_id(metadata.catalog_id):
        _add_unique_value(result.values, "appleMusicId", str(metadata.catalog_id))
        result.sources.append("catalog")

    if not metadata.playlist_id:
        if not result.values:
            result.sources.append("missing-apple-music-id")
            result.errors.append("音频中未读取到 Apple Music 歌曲 ID 或专辑 ID")
        return result

    tried_stores: set[str] = set()
    for store in stores or DEFAULT_STORES:
        if not store or store in tried_stores:
            continue
        tried_stores.add(store)
        match = _match_album_store(metadata, client, store, metadata.playlist_id, result.errors)
        result.sources.append(match.source)
        if match.track:
            _merge_track_metadata(result.values, match.track)

    if not result.values:
        result.sources.append("not-found")
    return result


def collect_qq_music_metadata(
    metadata: AudioMetadata,
    client: QQMusicClientProtocol,
) -> QQMusicSearchResult:
    result = QQMusicSearchResult()
    if not metadata.title:
        result.errors.append("音频中未读取到歌名，跳过 QQ 音乐搜索")
        return result

    try:
        candidates = client.search_songs(metadata.title)
    except Exception as exc:
        result.errors.append(f"QQ 音乐搜索失败: {exc}")
        return result

    result.candidates = sorted(
        candidates,
        key=lambda candidate: (-_qq_music_candidate_score(metadata, candidate), candidate.source_index),
    )
    if not result.candidates:
        result.errors.append("QQ 音乐未找到带 songid 和 mid 的候选")
    return result


def collect_ncm_music_metadata(
    metadata: AudioMetadata,
    client: NCMusicClientProtocol,
) -> NCMusicSearchResult:
    result = NCMusicSearchResult()
    if not metadata.title:
        result.errors.append("未读取到歌名，跳过网易云音乐搜索")
        return result

    try:
        candidates = client.search_songs(metadata.title)
    except Exception as exc:
        result.errors.append(f"网易云音乐搜索失败: {exc}")
        return result

    result.candidates = sorted(
        candidates,
        key=lambda candidate: (-_ncm_music_candidate_score(metadata, candidate), candidate.source_index),
    )
    if not result.candidates:
        result.errors.append("网易云音乐未找到带歌曲 ID 的候选")
    return result


def confirm_qq_music_candidates(
    pairs: list[PairMetadata],
    dry_run: bool,
    input_func: Callable[[str], str] = input,
    print_func: Callable[..., None] | None = None,
) -> None:
    if print_func is None:
        print_func = _safe_print

    available = [pair for pair in pairs if pair.qq_music_metadata.candidates]
    for pair in available:
        pair.qq_music_metadata.selected = pair.qq_music_metadata.candidates[0]

    if dry_run or not available:
        return

    print_func("")
    print_func("QQ 音乐最佳候选：")
    for pair in available:
        best = pair.qq_music_metadata.candidates[0]
        print_func(f"  {pair.ttml_path.name}: {_format_qq_music_candidate(best)}")

    while True:
        answer = input_func("Accept all QQ Music best candidates? Type Y to accept, N to choose alternatives: ").strip()
        if answer.casefold() in {"y", "n"}:
            break
        print_func("Please type Y or N.")

    if answer.casefold() == "y":
        return

    for pair in available:
        options = pair.qq_music_metadata.candidates[:5]
        print_func("")
        print_func(f"{pair.ttml_path.name} QQ 音乐候选：")
        for index, candidate in enumerate(options, start=1):
            print_func(f"  {index}. {_format_qq_music_candidate(candidate)}")
        while True:
            answer = input_func("Select 1-5, or press Enter to skip this song: ").strip()
            if not answer:
                pair.qq_music_metadata.selected = None
                break
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                pair.qq_music_metadata.selected = options[int(answer) - 1]
                break
            print_func("Invalid selection.")


def confirm_ncm_music_candidates(
    pairs: list[PairMetadata],
    dry_run: bool,
    input_func: Callable[[str], str] = input,
    print_func: Callable[..., None] | None = None,
) -> None:
    if print_func is None:
        print_func = _safe_print

    available = [pair for pair in pairs if pair.ncm_music_metadata.candidates]
    for pair in available:
        pair.ncm_music_metadata.selected = pair.ncm_music_metadata.candidates[0]

    if dry_run or not available:
        return

    print_func("")
    print_func("网易云音乐最佳候选：")
    for pair in available:
        best = pair.ncm_music_metadata.candidates[0]
        print_func(f"  {pair.ttml_path.name}: {_format_ncm_music_candidate(best)}")

    while True:
        answer = input_func("Accept all NetEase Cloud Music best candidates? Type Y to accept, N to choose alternatives: ").strip()
        if answer.casefold() in {"y", "n"}:
            break
        print_func("Please type Y or N.")

    if answer.casefold() == "y":
        return

    for pair in available:
        options = pair.ncm_music_metadata.candidates[:5]
        print_func("")
        print_func(f"{pair.ttml_path.name} 网易云音乐候选：")
        for index, candidate in enumerate(options, start=1):
            print_func(f"  {index}. {_format_ncm_music_candidate(candidate)}")
        while True:
            answer = input_func("Select 1-5, or press Enter to skip this song: ").strip()
            if not answer:
                pair.ncm_music_metadata.selected = None
                break
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                pair.ncm_music_metadata.selected = options[int(answer) - 1]
                break
            print_func("Invalid selection.")


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


def values_from_metadata(
    metadata: AudioMetadata,
    apple_music_values: dict[str, list[str]] | None = None,
    qq_music_candidate: QQMusicCandidate | None = None,
    ncm_music_candidate: NCMusicCandidate | None = None,
) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    if metadata.title:
        _add_unique_value(values, "musicName", metadata.title)
    if metadata.artists:
        for artist in metadata.artists:
            _add_unique_value(values, "artists", artist)
    if metadata.album:
        _add_unique_value(values, "album", metadata.album)
    for key, proposed_values in (apple_music_values or {}).items():
        for value in proposed_values:
            _add_unique_value(values, key, value)
    if qq_music_candidate:
        _merge_qq_music_metadata(values, metadata, qq_music_candidate)
    if ncm_music_candidate:
        _merge_ncm_music_metadata(values, metadata, ncm_music_candidate)
    if metadata.isrc:
        _add_unique_value(values, "isrc", metadata.isrc)
    return values


def find_directory_work_items(directory: Path) -> tuple[list[WorkItem], list[str]]:
    ttml_files = sorted(directory.glob("*.ttml"))
    audio_by_stem: dict[str, list[Path]] = {}
    for child in directory.iterdir():
        if child.is_file() and child.suffix.lower() in AUDIO_EXTENSIONS:
            audio_by_stem.setdefault(child.stem, []).append(child)

    work_items: list[WorkItem] = []
    warnings: list[str] = []
    for ttml in ttml_files:
        matches = sorted(audio_by_stem.get(ttml.stem, []), key=lambda path: (path.suffix.lower(), path.name.lower()))
        if len(matches) == 1:
            work_items.append(WorkItem(ttml, matches[0]))
        elif not matches:
            work_items.append(WorkItem(ttml))
        else:
            flac_matches = [match for match in matches if match.suffix.lower() == ".flac"]
            if len(flac_matches) == 1:
                work_items.append(WorkItem(ttml, flac_matches[0]))
            else:
                names = ", ".join(match.name for match in matches)
                warnings.append(f"{ttml.name}: multiple same-stem audio files found: {names}")
    return work_items, warnings


def find_directory_pairs(directory: Path) -> tuple[list[tuple[Path, Path]], list[str]]:
    work_items, warnings = find_directory_work_items(directory)
    pairs: list[tuple[Path, Path]] = []
    legacy_warnings = list(warnings)
    for item in work_items:
        if item.audio_path:
            pairs.append((item.audio_path, item.ttml_path))
        else:
            legacy_warnings.append(f"{item.ttml_path.name}: no same-stem audio file found")
    return pairs, legacy_warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fill AMLL TTML metadata from paired audio files.")
    parser.add_argument("path", nargs="?", default=".", help="directory to batch-process")
    parser.add_argument("--audio", type=Path, help="single audio file")
    parser.add_argument("--ttml", type=Path, help="single TTML file")
    parser.add_argument("--dry-run", action="store_true", help="show changes without writing files")
    args = parser.parse_args(argv)

    if args.audio and not args.ttml:
        parser.error("--audio requires --ttml")

    if args.audio and args.ttml:
        work_items = [WorkItem(args.ttml, args.audio)]
        warnings: list[str] = []
    elif args.ttml:
        work_items = [WorkItem(args.ttml)]
        warnings: list[str] = []
    else:
        directory = Path(args.path)
        if not directory.is_dir():
            parser.error(f"{directory} is not a directory")
        work_items, warnings = find_directory_work_items(directory)

    for warning in warnings:
        _safe_print(f"[skip] {warning}")

    apple_music_client = AppleMusicClient()
    qq_music_client = QQMusicClient()
    ncm_music_client = NCMusicClient()
    failures = 0
    prepared_pairs: list[PairMetadata] = []
    for work_item in work_items:
        try:
            prepared_pairs.append(_prepare_work_item(work_item, apple_music_client, qq_music_client, ncm_music_client))
        except Exception as exc:
            failures += 1
            _safe_print(f"[error] {work_item.ttml_path.name}: {exc}", file=sys.stderr)

    confirm_qq_music_candidates(prepared_pairs, dry_run=args.dry_run)
    confirm_ncm_music_candidates(prepared_pairs, dry_run=args.dry_run)

    for pair in prepared_pairs:
        try:
            _process_prepared_pair(pair, dry_run=args.dry_run)
        except Exception as exc:
            failures += 1
            _safe_print(f"[error] {pair.ttml_path.name}: {exc}", file=sys.stderr)

    return 1 if failures else 0


def _prepare_pair(
    audio_path: Path,
    ttml_path: Path,
    apple_music_client: AppleMusicClientProtocol,
    qq_music_client: QQMusicClientProtocol,
    ncm_music_client: NCMusicClientProtocol | None = None,
) -> PairMetadata:
    metadata = read_audio_metadata(audio_path)
    apple_music_metadata = collect_apple_music_metadata(metadata, apple_music_client)
    qq_music_metadata = collect_qq_music_metadata(metadata, qq_music_client)
    ncm_music_metadata = collect_ncm_music_metadata(metadata, ncm_music_client or NCMusicClient())
    return PairMetadata(audio_path, ttml_path, metadata, apple_music_metadata, qq_music_metadata, ncm_music_metadata)


def _prepare_work_item(
    work_item: WorkItem,
    apple_music_client: AppleMusicClientProtocol,
    qq_music_client: QQMusicClientProtocol,
    ncm_music_client: NCMusicClientProtocol | None = None,
) -> PairMetadata:
    if work_item.audio_path:
        return _prepare_pair(work_item.audio_path, work_item.ttml_path, apple_music_client, qq_music_client, ncm_music_client)

    metadata = read_ttml_metadata(work_item.ttml_path)
    if not metadata.title:
        raise ValueError("TTML 中未读取到歌名，跳过 QQ 音乐搜索和网易云音乐搜索")
    return PairMetadata(
        None,
        work_item.ttml_path,
        metadata,
        AppleMusicMetadataResult(),
        collect_qq_music_metadata(metadata, qq_music_client),
        collect_ncm_music_metadata(metadata, ncm_music_client or NCMusicClient()),
    )


def _process_pair(
    audio_path: Path,
    ttml_path: Path,
    client: AppleMusicClientProtocol,
    dry_run: bool,
    qq_music_client: QQMusicClientProtocol | None = None,
    ncm_music_client: NCMusicClientProtocol | None = None,
) -> None:
    pair = _prepare_pair(audio_path, ttml_path, client, qq_music_client or QQMusicClient(), ncm_music_client)
    confirm_qq_music_candidates([pair], dry_run=dry_run)
    confirm_ncm_music_candidates([pair], dry_run=dry_run)
    _process_prepared_pair(pair, dry_run=dry_run)


def _process_prepared_pair(pair: PairMetadata, dry_run: bool) -> None:
    values = values_from_metadata(
        pair.metadata,
        pair.apple_music_metadata.values,
        qq_music_candidate=pair.qq_music_metadata.selected,
        ncm_music_candidate=pair.ncm_music_metadata.selected,
    )
    audio_path = pair.audio_path
    ttml_path = pair.ttml_path
    apple_music_metadata = pair.apple_music_metadata
    qq_music_metadata = pair.qq_music_metadata
    ncm_music_metadata = pair.ncm_music_metadata
    result = update_ttml_metadata(ttml_path, values, dry_run=dry_run)

    status = "dry-run" if dry_run else "updated"
    if not result.changed:
        status = "unchanged"
    _safe_print(f"[{status}] {ttml_path.name}")
    _safe_print(f"  audio: {audio_path.name if audio_path else '-'}")
    _safe_print(f"  appleMusicId: {', '.join(apple_music_metadata.values.get('appleMusicId', [])) or '-'}")
    _safe_print(f"  appleMusicSources: {', '.join(apple_music_metadata.sources) or '-'}")
    if apple_music_metadata.errors:
        for error in apple_music_metadata.errors:
            _safe_print(f"  lookup warning: {error}")
    best = qq_music_metadata.candidates[0] if qq_music_metadata.candidates else None
    _safe_print(f"  qqMusicBest: {_format_qq_music_candidate(best) if best else '-'}")
    selected = qq_music_metadata.selected
    _safe_print(f"  qqMusicId: {', '.join([selected.song_id, selected.mid]) if selected else '-'}")
    if qq_music_metadata.errors:
        for error in qq_music_metadata.errors:
            _safe_print(f"  lookup warning: {error}")
    best = ncm_music_metadata.candidates[0] if ncm_music_metadata.candidates else None
    _safe_print(f"  ncmMusicBest: {_format_ncm_music_candidate(best) if best else '-'}")
    selected_ncm = ncm_music_metadata.selected
    _safe_print(f"  ncmMusicId: {selected_ncm.song_id if selected_ncm else '-'}")
    if ncm_music_metadata.errors:
        for error in ncm_music_metadata.errors:
            _safe_print(f"  lookup warning: {error}")
    _print_change_group("added", result.added)
    _print_change_group("replaced", result.replaced)
    _print_change_group("skipped", result.skipped)
    if result.backup_path:
        _safe_print(f"  backup: {result.backup_path}")


def _print_change_group(label: str, changes: dict[str, list[str]]) -> None:
    for key, values in changes.items():
        joined = ", ".join(values)
        _safe_print(f"  {label}: {key} = {joined}")


def _safe_print(*values: Any, file: Any = None, **kwargs: Any) -> None:
    stream = file or sys.stdout
    try:
        print(*values, file=stream, **kwargs)
    except UnicodeEncodeError:
        text = kwargs.get("sep", " ").join(str(value) for value in values)
        end = kwargs.get("end", "\n")
        encoded = text.encode(getattr(stream, "encoding", None) or "utf-8", "backslashreplace").decode(
            getattr(stream, "encoding", None) or "utf-8"
        )
        stream.write(encoded + end)


def _merge_track_metadata(values: dict[str, list[str]], track: dict[str, Any]) -> None:
    _add_unique_value(values, "musicName", _stringify_tag_value(track.get("name")))
    for artist in split_artists([track.get("artistName")]):
        _add_unique_value(values, "artists", artist)
    _add_unique_value(values, "album", _stringify_tag_value(track.get("albumName")))
    _add_unique_value(values, "appleMusicId", _track_id(track))
    _add_unique_value(values, "isrc", _stringify_tag_value(track.get("isrc")))


def _merge_qq_music_metadata(
    values: dict[str, list[str]],
    metadata: AudioMetadata,
    candidate: QQMusicCandidate,
) -> None:
    _add_unique_value(values, "qqMusicId", candidate.song_id)
    _add_unique_value(values, "qqMusicId", candidate.mid)
    if candidate.title and not _same_raw_text(candidate.title, metadata.title):
        _add_unique_value(values, "musicName", candidate.title)
    if (
        candidate.subtitle
        and not _same_raw_text(candidate.subtitle, metadata.title)
        and not _same_raw_text(candidate.subtitle, candidate.title)
    ):
        _add_unique_value(values, "musicName", candidate.subtitle)
    for artist in candidate.artists:
        if not any(_same_raw_text(artist, existing) for existing in metadata.artists):
            _add_unique_value(values, "artists", artist)
    if candidate.album and not _same_raw_text(candidate.album, metadata.album):
        _add_unique_value(values, "album", candidate.album)


def _merge_ncm_music_metadata(
    values: dict[str, list[str]],
    metadata: AudioMetadata,
    candidate: NCMusicCandidate,
) -> None:
    _add_unique_value(values, "ncmMusicId", candidate.song_id)
    existing_titles = [metadata.title, *values.get("musicName", [])]
    for title in [candidate.title, *candidate.aliases]:
        if title and not any(_same_raw_text(title, existing) for existing in existing_titles):
            _add_unique_value(values, "musicName", title)
            existing_titles.append(title)
    for artist in candidate.artists:
        if not any(_same_raw_text(artist, existing) for existing in metadata.artists):
            _add_unique_value(values, "artists", artist)
    if candidate.album and not _same_raw_text(candidate.album, metadata.album):
        _add_unique_value(values, "album", candidate.album)


def _qq_music_search_payload(query: str) -> dict[str, Any]:
    return {
        "comm": {
            "ct": "11",
            "cv": "14090508",
            "v": "14090508",
            "tmeAppID": "qqmusic",
            "phonetype": "EBG-AN10",
            "deviceScore": "553.47",
            "devicelevel": "50",
            "newdevicelevel": "20",
            "rom": "HuaWei/EMOTION/EmotionUI_14.2.0",
            "os_ver": "12",
            "OpenUDID": "0",
            "OpenUDID2": "0",
            "QIMEI36": "0",
            "udid": "0",
            "chid": "0",
            "aid": "0",
            "oaid": "0",
            "taid": "0",
            "tid": "0",
            "wid": "0",
            "uid": "0",
            "sid": "0",
            "modeSwitch": "6",
            "teenMode": "0",
            "ui_mode": "2",
            "nettype": "1020",
            "v4ip": "",
        },
        "req": {
            "module": "music.search.SearchCgiService",
            "method": "DoSearchForQQMusicMobile",
            "param": {
                "search_type": 0,
                "query": query,
                "page_num": 1,
                "num_per_page": 30,
                "highlight": 0,
                "nqc_flag": 0,
                "multi_zhida": 0,
                "cat": 2,
                "grp": 1,
                "sin": 0,
                "sem": 0,
            },
        },
    }


def _parse_qq_music_candidates(payload: dict[str, Any]) -> list[QQMusicCandidate]:
    songs = _nested_get(payload, "req", "data", "body", "item_song")
    if not isinstance(songs, list):
        return []

    candidates: list[QQMusicCandidate] = []
    for index, song in enumerate(songs):
        if not isinstance(song, dict):
            continue
        song_id = _stringify_tag_value(song.get("id") or song.get("songid"))
        mid = _stringify_tag_value(song.get("mid") or song.get("songmid"))
        if not song_id or not mid:
            continue
        candidates.append(
            QQMusicCandidate(
                song_id=song_id,
                mid=mid,
                title=_stringify_tag_value(song.get("name") or song.get("title")),
                subtitle=_stringify_tag_value(song.get("subtitle")),
                artists=_qq_music_artists(song.get("singer")),
                album=_qq_music_album(song.get("album")),
                source_index=index,
            )
        )
    return candidates


def _parse_ncm_music_candidates(payload: dict[str, Any]) -> list[NCMusicCandidate]:
    songs = _nested_get(payload, "result", "songs")
    if not isinstance(songs, list):
        return []

    candidates: list[NCMusicCandidate] = []
    for index, song in enumerate(songs):
        if not isinstance(song, dict):
            continue
        song_id = _stringify_tag_value(song.get("id") or song.get("songid"))
        if not song_id:
            continue
        candidates.append(
            NCMusicCandidate(
                song_id=song_id,
                title=_stringify_tag_value(song.get("name") or song.get("title")),
                aliases=_ncm_music_aliases(song),
                artists=_ncm_music_artists(song.get("ar") or song.get("artists")),
                album=_ncm_music_album(song.get("al") or song.get("album")),
                source_index=index,
            )
        )
    return candidates


def _ncm_music_aliases(song: dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    for key in ("alia", "alias", "tns"):
        value = song.get(key)
        if isinstance(value, list):
            pieces = value
        elif value:
            pieces = [value]
        else:
            pieces = []
        for piece in pieces:
            text = _stringify_tag_value(piece)
            if text and text not in aliases:
                aliases.append(text)
    return aliases


def _ncm_music_artists(value: Any) -> list[str]:
    if isinstance(value, dict):
        return split_artists([value.get("name")])
    if not isinstance(value, list):
        return split_artists([value]) if value else []
    artists: list[str] = []
    for item in value:
        name = _stringify_tag_value(item.get("name") if isinstance(item, dict) else item)
        for artist in split_artists([name]):
            if artist not in artists:
                artists.append(artist)
    return artists


def _ncm_music_album(value: Any) -> str | None:
    if isinstance(value, dict):
        return _stringify_tag_value(value.get("name") or value.get("title"))
    return _stringify_tag_value(value)


def _qq_music_artists(value: Any) -> list[str]:
    if isinstance(value, dict):
        return split_artists([value.get("name")])
    if not isinstance(value, list):
        return split_artists([value]) if value else []
    artists: list[str] = []
    for item in value:
        name = _stringify_tag_value(item.get("name") if isinstance(item, dict) else item)
        for artist in split_artists([name]):
            if artist not in artists:
                artists.append(artist)
    return artists


def _qq_music_album(value: Any) -> str | None:
    if isinstance(value, dict):
        return _stringify_tag_value(value.get("name") or value.get("title"))
    return _stringify_tag_value(value)


def _qq_music_candidate_score(metadata: AudioMetadata, candidate: QQMusicCandidate) -> int:
    score = _text_match_score(metadata.title, candidate.title) * 100
    for artist in metadata.artists:
        score += max((_text_match_score(artist, candidate_artist) for candidate_artist in candidate.artists), default=0) * 60
    score += _text_match_score(metadata.album, candidate.album) * 30
    return score


def _ncm_music_candidate_score(metadata: AudioMetadata, candidate: NCMusicCandidate) -> int:
    title_score = max(
        [_text_match_score(metadata.title, candidate.title)]
        + [_text_match_score(metadata.title, alias) for alias in candidate.aliases]
    )
    score = title_score * 100
    for artist in metadata.artists:
        score += max((_text_match_score(artist, candidate_artist) for candidate_artist in candidate.artists), default=0) * 60
    score += _text_match_score(metadata.album, candidate.album) * 30
    return score


def _text_match_score(expected: Any, actual: Any) -> int:
    expected_text = _normalize_match_text(expected)
    actual_text = _normalize_match_text(actual)
    if not expected_text or not actual_text:
        return 0
    if expected_text == actual_text:
        return 2
    if expected_text in actual_text or actual_text in expected_text:
        return 1
    return 0


def _normalize_match_text(value: Any) -> str:
    if value is None:
        return ""
    normalized = str(value).casefold().strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _same_raw_text(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    return str(left).strip() == str(right).strip()


def _nested_get(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _format_qq_music_candidate(candidate: QQMusicCandidate) -> str:
    title = candidate.title or "-"
    subtitle = f" ({candidate.subtitle})" if candidate.subtitle else ""
    artists = "/".join(candidate.artists) or "-"
    album = candidate.album or "-"
    return f"{title}{subtitle} - {artists} - {album} [{candidate.song_id}, {candidate.mid}]"


def _format_ncm_music_candidate(candidate: NCMusicCandidate) -> str:
    title = candidate.title or "-"
    aliases = f" ({'; '.join(candidate.aliases)})" if candidate.aliases else ""
    artists = "/".join(candidate.artists) or "-"
    album = candidate.album or "-"
    return f"{title}{aliases} - {artists} - {album} [{candidate.song_id}]"


def _add_unique_value(values: dict[str, list[str]], key: str, value: str | None) -> None:
    if not value:
        return
    if value not in values.setdefault(key, []):
        values[key].append(value)


def _real_meta_value(value: str | None) -> str | None:
    if _is_placeholder(value):
        return None
    assert value is not None
    return value.strip()


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
    unique_proposed_values: list[str] = []
    for value in proposed_values:
        if value not in unique_proposed_values:
            unique_proposed_values.append(value)

    if placeholders:
        replacements: list[tuple[int, int, str]] = []
        replacement_values = [value for value in unique_proposed_values if value not in real_values]
        for tag, value in zip(placeholders, replacement_values):
            value_attr = tag.attrs.get("value")
            if value_attr:
                replacements.append((value_attr.value_start, value_attr.value_end, _escape_xml_attr(value)))
            else:
                replacements.append((tag.start, tag.end, _make_meta_node(amll_prefix, key, value)))
        consumed_count = min(len(placeholders), len(replacement_values))
        for extra in placeholders[consumed_count:]:
            replacements.append((extra.start, extra.end, ""))
        metadata = _apply_text_replacements(metadata, replacements)

        remaining = replacement_values[consumed_count:]
        if replacement_values:
            result.replaced[key] = replacement_values
        if real_values:
            result.skipped[key] = real_values
        if remaining:
            metadata = _insert_meta_values(metadata, amll_prefix, key, remaining)
        return metadata

    missing_values = [value for value in unique_proposed_values if value not in real_values]
    if real_values:
        result.skipped[key] = real_values
    if not missing_values:
        return metadata

    metadata = _insert_meta_values(metadata, amll_prefix, key, missing_values)
    result.added[key] = missing_values
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
) -> AppleMusicTrackMatch:
    try:
        tracks = client.fetch_album_tracks(store, album_id)
    except Exception as exc:
        errors.append(f"{store}: {exc}")
        return AppleMusicTrackMatch(None, f"album:{store}:error")

    track_match = _match_by_track_number(metadata, tracks)
    if track_match:
        return AppleMusicTrackMatch(track_match, f"album:{store}:track")

    title_match = _match_by_title(metadata, tracks)
    if title_match:
        return AppleMusicTrackMatch(title_match, f"album:{store}:title")

    errors.append(f"{store}: no matching track in album {album_id}")
    return AppleMusicTrackMatch(None, f"album:{store}:not-found")


def _match_by_track_number(metadata: AudioMetadata, tracks: list[dict[str, Any]]) -> dict[str, Any] | None:
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
    if len(candidates) == 1:
        return candidates[0]

    normalized_title = _normalize_title(metadata.title)
    for candidate in candidates:
        if _normalize_title(candidate.get("name")) == normalized_title:
            return candidate
    return None


def _match_by_title(metadata: AudioMetadata, tracks: list[dict[str, Any]]) -> dict[str, Any] | None:
    normalized_title = _normalize_title(metadata.title)
    if not normalized_title:
        return None

    candidates = [track for track in tracks if _normalize_title(track.get("name")) == normalized_title]
    if len(candidates) == 1:
        return candidates[0]

    if metadata.duration_seconds is not None:
        timed = [
            track
            for track in candidates or tracks
            if _normalize_title(track.get("name")) == normalized_title
            and _duration_close(metadata.duration_seconds, track.get("durationInMillis"))
        ]
        if len(timed) == 1:
            return timed[0]
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
