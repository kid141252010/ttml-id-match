#!/usr/bin/env python3
"""Fill AMLL metadata in TTML files from paired audio metadata."""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import html
import json
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from opencc import OpenCC


AMLL_NS = "http://www.example.com/ns/amll"

DEFAULT_STORES = ["cn", "tw", "jp", "kr", "us"]
DEFAULT_SPOTIFY_MARKETS = ["US", "KR", "JP", "TW"]
DEFAULT_NCM_API_BASES = [
    "https://music163.xuanmou.com.cn",
    "https://neteasecloudmusicapi-main-api.vercel.app",
    "https://api-enhanced-six-beta.vercel.app",
]
SPOTIFY_SEARCH_LIMIT = 20
SPOTIFY_CANDIDATE_TARGET = 5
SPOTIFY_ARTIST_SEARCH_LIMIT = 10
SPOTIFY_ARTIST_ALBUM_LIMIT = 10
SPOTIFY_ARTIST_ALBUM_PAGE_LIMIT = 3
SPOTIFY_STRONG_MATCH_SCORE = 260
NCM_SEARCH_LIMIT = 100
NCM_ARTIST_SEARCH_LIMIT = 10
NCM_ARTIST_ALBUM_LIMIT = 50
TARGET_KEY_ORDER = ["musicName", "artists", "album", "qqMusicId", "ncmMusicId", "spotifyId", "appleMusicId", "isrc"]
OPENCC_T2S = OpenCC("t2s")
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
    release_date: str | None = None


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


@dataclass(frozen=True)
class NCMusicSearchContext:
    titles: list[str] = field(default_factory=list)
    artists: list[str] = field(default_factory=list)
    albums: list[str] = field(default_factory=list)


@dataclass
class NCMusicSearchResult:
    candidates: list[NCMusicCandidate] = field(default_factory=list)
    selected: NCMusicCandidate | None = None
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SpotifyCredentials:
    client_id: str | None = None
    client_secret: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)


@dataclass(frozen=True)
class SpotifyTrackCandidate:
    track_id: str
    title: str | None = None
    artists: list[str] = field(default_factory=list)
    album: str | None = None
    market: str = ""
    source_index: int = 0
    isrc: str | None = None
    duration_ms: int | None = None
    release_date: str | None = None
    release_date_precision: str | None = None
    album_id: str | None = None
    match_source: str = "search"


@dataclass
class SpotifySearchResult:
    candidates: list[SpotifyTrackCandidate] = field(default_factory=list)
    selected: list[SpotifyTrackCandidate] = field(default_factory=list)
    candidates_by_market: dict[str, list[SpotifyTrackCandidate]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _SpotifyArtistCandidate:
    artist_id: str
    name: str | None = None
    source_index: int = 0


@dataclass(frozen=True)
class _SpotifyAlbumCandidate:
    album_id: str
    name: str | None = None
    release_date: str | None = None
    release_date_precision: str | None = None
    source_index: int = 0


@dataclass(frozen=True)
class _NCMusicArtistCandidate:
    artist_id: str
    name: str | None = None
    aliases: list[str] = field(default_factory=list)
    source_index: int = 0


@dataclass(frozen=True)
class _NCMusicAlbumCandidate:
    album_id: str
    name: str | None = None
    source_index: int = 0


@dataclass
class PairMetadata:
    audio_path: Path | None
    ttml_path: Path
    metadata: AudioMetadata
    apple_music_metadata: AppleMusicMetadataResult
    qq_music_metadata: QQMusicSearchResult
    ncm_music_metadata: NCMusicSearchResult = field(default_factory=NCMusicSearchResult)
    spotify_metadata: SpotifySearchResult = field(default_factory=SpotifySearchResult)


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


@dataclass
class TtmlLanguageNormalizationResult:
    language_changed: bool = False
    body_text_changed: bool = False
    removed_translations: int = 0
    removed_transliterations: int = 0
    backup_path: Path | None = None

    @property
    def changed(self) -> bool:
        return bool(
            self.language_changed
            or self.body_text_changed
            or self.removed_translations
            or self.removed_transliterations
        )


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
    def search_songs(self, context: NCMusicSearchContext) -> list[NCMusicCandidate]:
        ...


class SpotifyClientProtocol(Protocol):
    def search_tracks(self, metadata: AudioMetadata) -> list[SpotifyTrackCandidate]:
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

    def search_songs(self, context: NCMusicSearchContext | str) -> list[NCMusicCandidate]:
        if not self.api_bases:
            return []
        if isinstance(context, str):
            context = NCMusicSearchContext(titles=_text_with_simplified_variants(context))

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(self.api_bases))
        futures = {
            executor.submit(self._search_base, base, context): base
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

    def _search_base(self, base: str, context: NCMusicSearchContext) -> list[NCMusicCandidate]:
        candidates: list[NCMusicCandidate] = []
        errors: list[str] = []
        for query in context.titles:
            try:
                payload = self._read_json(self._build_search_url(base, query))
            except Exception as exc:
                errors.append(f"song:{query}: {exc}")
                continue
            candidates.extend(_parse_ncm_music_candidates(payload))

        candidates.extend(self._search_album_song_candidates(base, context))
        deduped = _dedupe_ncm_music_candidates(candidates)
        if not deduped and errors:
            raise LookupError("; ".join(errors))
        return deduped

    def _search_album_song_candidates(self, base: str, context: NCMusicSearchContext) -> list[NCMusicCandidate]:
        if not context.titles or not context.artists or not context.albums:
            return []

        artists = self._find_matching_artists(base, context)
        candidates: list[NCMusicCandidate] = []
        seen_album_ids: set[str] = set()
        for artist in artists:
            try:
                payload = self._read_json(self._build_artist_album_url(base, artist.artist_id))
            except Exception:
                continue
            for album in _parse_ncm_artist_album_candidates(payload):
                if album.album_id in seen_album_ids or not _ncm_album_matches(context, album):
                    continue
                seen_album_ids.add(album.album_id)
                try:
                    album_payload = self._read_json(self._build_album_url(base, album.album_id))
                except Exception:
                    continue
                candidates.extend(
                    candidate
                    for candidate in _parse_ncm_album_song_candidates(album_payload)
                    if _ncm_candidate_title_score(context, candidate) > 0
                )
        return candidates

    def _find_matching_artists(self, base: str, context: NCMusicSearchContext) -> list[_NCMusicArtistCandidate]:
        matches: list[_NCMusicArtistCandidate] = []
        seen_artist_ids: set[str] = set()
        for query in context.artists:
            try:
                payload = self._read_json(self._build_artist_search_url(base, query))
            except Exception:
                continue
            for artist in _parse_ncm_artist_candidates(payload):
                if artist.artist_id in seen_artist_ids or not _ncm_artist_matches(context, artist):
                    continue
                seen_artist_ids.add(artist.artist_id)
                matches.append(artist)
        return matches

    @staticmethod
    def _build_search_url(base: str, query: str) -> str:
        params = urllib.parse.urlencode(
            {
                "keywords": query,
                "limit": NCM_SEARCH_LIMIT,
                "offset": 0,
                "type": 1,
            }
        )
        return f"{base.rstrip('/')}/cloudsearch?{params}"

    @staticmethod
    def _build_artist_search_url(base: str, query: str) -> str:
        params = urllib.parse.urlencode(
            {
                "keywords": query,
                "limit": NCM_ARTIST_SEARCH_LIMIT,
                "offset": 0,
                "type": 100,
            }
        )
        return f"{base.rstrip('/')}/cloudsearch?{params}"

    @staticmethod
    def _build_artist_album_url(base: str, artist_id: str) -> str:
        params = urllib.parse.urlencode(
            {
                "id": artist_id,
                "limit": NCM_ARTIST_ALBUM_LIMIT,
            }
        )
        return f"{base.rstrip('/')}/artist/album?{params}"

    @staticmethod
    def _build_album_url(base: str, album_id: str) -> str:
        params = urllib.parse.urlencode({"id": album_id})
        return f"{base.rstrip('/')}/album?{params}"

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


def load_spotify_credentials(
    env_path: Path | None = None,
    environ: dict[str, str] | None = None,
) -> SpotifyCredentials:
    env_path = env_path or Path(".env")
    environment = environ if environ is not None else os.environ
    values = _read_dotenv_values(env_path)

    client_id = _clean_env_value(environment.get("SPOTIFY_CLIENT_ID")) or values.get("SPOTIFY_CLIENT_ID")
    client_secret = _clean_env_value(environment.get("SPOTIFY_CLIENT_SECRET")) or values.get("SPOTIFY_CLIENT_SECRET")
    return SpotifyCredentials(client_id=client_id, client_secret=client_secret)


class SpotifyClient:
    TOKEN_URL = "https://accounts.spotify.com/api/token"
    SEARCH_URL = "https://api.spotify.com/v1/search"
    ARTIST_ALBUMS_URL = "https://api.spotify.com/v1/artists/{artist_id}/albums"
    ALBUM_URL = "https://api.spotify.com/v1/albums/{album_id}"
    TRACK_URL = "https://api.spotify.com/v1/tracks/{track_id}"

    def __init__(
        self,
        credentials: SpotifyCredentials,
        timeout: int = 20,
        markets: Iterable[str] | None = None,
        read_json: Callable[[str, str], dict[str, Any]] | None = None,
    ):
        self.credentials = credentials
        self.timeout = timeout
        self.markets = list(markets or DEFAULT_SPOTIFY_MARKETS)
        self._read_json = read_json or self._read_json_from_url
        self._access_token: str | None = None

    def search_tracks(self, metadata: AudioMetadata) -> list[SpotifyTrackCandidate]:
        if not metadata.title:
            return []
        access_token = self._get_access_token()
        candidates: list[SpotifyTrackCandidate] = []
        errors: list[str] = []
        for market in self.markets:
            market_candidates = self._search_market_tracks(metadata, access_token, market, len(candidates), errors)
            if self._should_search_artist_albums(metadata, market_candidates):
                market_candidates.extend(
                    self._search_artist_album_tracks(
                        metadata,
                        access_token,
                        market,
                        len(candidates) + len(market_candidates),
                    )
                )
            candidates.extend(_dedupe_spotify_candidates(market_candidates))
        if errors and not candidates:
            raise LookupError("; ".join(errors))
        return candidates

    def _search_market_tracks(
        self,
        metadata: AudioMetadata,
        access_token: str,
        market: str,
        source_offset: int,
        errors: list[str],
    ) -> list[SpotifyTrackCandidate]:
        market_candidates: list[SpotifyTrackCandidate] = []
        for query in _spotify_search_queries(metadata):
            try:
                payload = self._read_json(self._build_search_url_for_query(query, market), access_token)
            except Exception as exc:
                errors.append(f"{market}:{query}: {exc}")
                continue
            parsed = _parse_spotify_candidates(payload, market=market)
            if parsed:
                local_offset = len(market_candidates)
                market_candidates.extend(
                    SpotifyTrackCandidate(
                        track_id=candidate.track_id,
                        title=candidate.title,
                        artists=list(candidate.artists),
                        album=candidate.album,
                        market=candidate.market,
                        source_index=source_offset + local_offset + index,
                        isrc=candidate.isrc,
                        duration_ms=candidate.duration_ms,
                        release_date=candidate.release_date,
                        release_date_precision=candidate.release_date_precision,
                        album_id=candidate.album_id,
                        match_source=candidate.match_source,
                    )
                    for index, candidate in enumerate(parsed)
                )
            if len(_dedupe_spotify_candidates(market_candidates)) >= SPOTIFY_CANDIDATE_TARGET:
                break
        return market_candidates

    def _should_search_artist_albums(
        self,
        metadata: AudioMetadata,
        candidates: list[SpotifyTrackCandidate],
    ) -> bool:
        if not (metadata.title and metadata.artists and metadata.release_date and metadata.duration_seconds is not None):
            return False
        if not candidates:
            return True
        return max((_spotify_candidate_score(metadata, candidate) for candidate in candidates), default=0) < SPOTIFY_STRONG_MATCH_SCORE

    def _search_artist_album_tracks(
        self,
        metadata: AudioMetadata,
        access_token: str,
        market: str,
        source_offset: int,
    ) -> list[SpotifyTrackCandidate]:
        artists = self._find_matching_artists(metadata, access_token, market)
        if not artists:
            return []

        candidates: list[SpotifyTrackCandidate] = []
        seen_album_ids: set[str] = set()
        for artist in artists:
            for album in self._iter_artist_albums(artist.artist_id, access_token, market):
                if album.album_id in seen_album_ids:
                    continue
                seen_album_ids.add(album.album_id)
                if not _release_date_matches(metadata.release_date, album.release_date, album.release_date_precision):
                    continue
                try:
                    payload = self._read_json(self._build_album_url(album.album_id, market), access_token)
                except Exception:
                    continue
                album_candidates = _parse_spotify_album_track_candidates(payload, market)
                for candidate in album_candidates:
                    if not _spotify_album_fallback_track_matches(metadata, candidate):
                        continue
                    candidate = self._hydrate_track_candidate(candidate, access_token, market) or candidate
                    candidates.append(
                        SpotifyTrackCandidate(
                            track_id=candidate.track_id,
                            title=candidate.title,
                            artists=list(candidate.artists),
                            album=candidate.album,
                            market=candidate.market,
                            source_index=source_offset + len(candidates),
                            isrc=candidate.isrc,
                            duration_ms=candidate.duration_ms,
                            release_date=candidate.release_date,
                            release_date_precision=candidate.release_date_precision,
                            album_id=candidate.album_id,
                            match_source="artist-album",
                        )
                    )
        return _dedupe_spotify_candidates(candidates)

    def _hydrate_track_candidate(
        self,
        candidate: SpotifyTrackCandidate,
        access_token: str,
        market: str,
    ) -> SpotifyTrackCandidate | None:
        try:
            payload = self._read_json(self._build_track_url(candidate.track_id, market), access_token)
        except Exception:
            return None
        hydrated = _spotify_track_candidate_from_track(
            payload,
            market,
            candidate.source_index,
            candidate.match_source,
        )
        if hydrated is None:
            return None
        return SpotifyTrackCandidate(
            track_id=hydrated.track_id,
            title=hydrated.title or candidate.title,
            artists=list(hydrated.artists or candidate.artists),
            album=hydrated.album or candidate.album,
            market=candidate.market,
            source_index=candidate.source_index,
            isrc=hydrated.isrc or candidate.isrc,
            duration_ms=hydrated.duration_ms or candidate.duration_ms,
            release_date=hydrated.release_date or candidate.release_date,
            release_date_precision=hydrated.release_date_precision or candidate.release_date_precision,
            album_id=hydrated.album_id or candidate.album_id,
            match_source=candidate.match_source,
        )

    def _find_matching_artists(
        self,
        metadata: AudioMetadata,
        access_token: str,
        market: str,
    ) -> list[_SpotifyArtistCandidate]:
        matches: list[_SpotifyArtistCandidate] = []
        seen_artist_ids: set[str] = set()
        for artist_name in metadata.artists:
            try:
                payload = self._read_json(self._build_artist_search_url(artist_name, market), access_token)
            except Exception:
                continue
            for artist in _parse_spotify_artist_candidates(payload):
                if artist.artist_id in seen_artist_ids or not _spotify_artist_matches(metadata, artist):
                    continue
                seen_artist_ids.add(artist.artist_id)
                matches.append(artist)
        return matches

    def _iter_artist_albums(
        self,
        artist_id: str,
        access_token: str,
        market: str,
    ) -> Iterable[_SpotifyAlbumCandidate]:
        for page_index in range(SPOTIFY_ARTIST_ALBUM_PAGE_LIMIT):
            offset = page_index * SPOTIFY_ARTIST_ALBUM_LIMIT
            try:
                payload = self._read_json(self._build_artist_albums_url(artist_id, market, offset), access_token)
            except Exception:
                return
            albums = _parse_spotify_artist_album_candidates(payload)
            if not albums:
                return
            yield from albums
            total = _parse_number(payload.get("total"))
            if total is not None and offset + SPOTIFY_ARTIST_ALBUM_LIMIT >= total:
                return
            if not payload.get("next") and total is None:
                return

    def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token
        request = self._build_token_request()
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "ignore"))
        token = _stringify_tag_value(payload.get("access_token")) if isinstance(payload, dict) else None
        if not token:
            raise ValueError("Spotify token response did not include access_token")
        self._access_token = token
        return token

    def _build_token_request(self) -> urllib.request.Request:
        if not self.credentials.client_id or not self.credentials.client_secret:
            raise ValueError("missing Spotify client credentials")
        token = base64.b64encode(
            f"{self.credentials.client_id}:{self.credentials.client_secret}".encode("utf-8")
        ).decode("ascii")
        data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode("utf-8")
        return urllib.request.Request(
            self.TOKEN_URL,
            data=data,
            headers={
                "Accept": "application/json",
                "Authorization": f"Basic {token}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )

    def _build_search_url(self, metadata: AudioMetadata, market: str) -> str:
        queries = _spotify_search_queries(metadata)
        query = queries[0] if queries else ""
        if not query:
            raise ValueError("Spotify search requires a title")
        return self._build_search_url_for_query(query, market)

    def _build_search_url_for_query(self, query: str, market: str) -> str:
        params = urllib.parse.urlencode(
            {
                "q": query,
                "type": "track",
                "market": market,
                "limit": SPOTIFY_SEARCH_LIMIT,
            }
        )
        return f"{self.SEARCH_URL}?{params}"

    def _build_artist_search_url(self, query: str, market: str) -> str:
        params = urllib.parse.urlencode(
            {
                "q": query,
                "type": "artist",
                "market": market,
                "limit": SPOTIFY_ARTIST_SEARCH_LIMIT,
            }
        )
        return f"{self.SEARCH_URL}?{params}"

    def _build_artist_albums_url(self, artist_id: str, market: str, offset: int = 0) -> str:
        params = urllib.parse.urlencode(
            {
                "include_groups": "album,single",
                "market": market,
                "limit": SPOTIFY_ARTIST_ALBUM_LIMIT,
                "offset": offset,
            }
        )
        return f"{self.ARTIST_ALBUMS_URL.format(artist_id=urllib.parse.quote(artist_id, safe=''))}?{params}"

    def _build_album_url(self, album_id: str, market: str) -> str:
        params = urllib.parse.urlencode({"market": market})
        return f"{self.ALBUM_URL.format(album_id=urllib.parse.quote(album_id, safe=''))}?{params}"

    def _build_track_url(self, track_id: str, market: str) -> str:
        params = urllib.parse.urlencode({"market": market})
        return f"{self.TRACK_URL.format(track_id=urllib.parse.quote(track_id, safe=''))}?{params}"

    def _read_json_from_url(self, url: str, access_token: str) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "Mozilla/5.0",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "ignore"))
        if not isinstance(payload, dict):
            raise ValueError("Spotify API returned a non-object payload")
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


def read_ttml_metadata(path: Path) -> AudioMetadata:
    text = path.read_text(encoding="utf-8")
    text, amll_prefix = _ensure_amll_namespace(text)
    metadata_start, metadata_end = _find_metadata_inner_bounds(text)
    metadata = text[metadata_start:metadata_end]
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
    qq_music_candidate: QQMusicCandidate | None = None,
) -> NCMusicSearchResult:
    result = NCMusicSearchResult()
    if not metadata.title:
        result.errors.append("未读取到歌名，跳过网易云音乐搜索")
        return result

    context = _build_ncm_music_search_context(metadata, qq_music_candidate)
    try:
        candidates = client.search_songs(context)
    except Exception as exc:
        result.errors.append(f"网易云音乐搜索失败: {exc}")
        return result

    result.candidates = sorted(
        candidates,
        key=lambda candidate: (-_ncm_music_candidate_score(context, candidate), candidate.source_index),
    )
    if not result.candidates:
        result.errors.append("网易云音乐未找到带歌曲 ID 的候选")
    return result


def collect_spotify_metadata(
    metadata: AudioMetadata,
    client: SpotifyClientProtocol | None,
) -> SpotifySearchResult:
    result = SpotifySearchResult()
    if client is None:
        result.errors.append("缺少 SPOTIFY_CLIENT_ID 或 SPOTIFY_CLIENT_SECRET，跳过 Spotify 搜索")
        return result
    if not metadata.title:
        result.errors.append("未读取到歌名，跳过 Spotify 搜索")
        return result

    try:
        candidates = client.search_tracks(metadata)
    except Exception as exc:
        result.errors.append(f"Spotify 搜索失败: {exc}")
        return result

    for market in _spotify_market_order(candidates):
        market_candidates = [candidate for candidate in candidates if candidate.market == market]
        if not market_candidates:
            continue
        result.candidates_by_market[market] = _dedupe_spotify_candidates(
            sorted(
                market_candidates,
                key=lambda candidate: (-_spotify_candidate_score(metadata, candidate), candidate.source_index),
            )
        )

    result.candidates = sorted(
        _dedupe_spotify_candidates(
            sorted(
                candidates,
                key=lambda candidate: (
                    -_spotify_candidate_score(metadata, candidate),
                    _spotify_market_index(candidate.market),
                    candidate.source_index,
                ),
            )
        ),
        key=lambda candidate: (
            -_spotify_candidate_score(metadata, candidate),
            _spotify_market_index(candidate.market),
            candidate.source_index,
        ),
    )
    result.selected = _spotify_market_best_candidates(result, metadata)
    if not result.candidates:
        result.errors.append("Spotify 未找到带 track id 的候选")
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


def confirm_spotify_candidates(
    pairs: list[PairMetadata],
    dry_run: bool,
    input_func: Callable[[str], str] = input,
    print_func: Callable[..., None] | None = None,
) -> None:
    if print_func is None:
        print_func = _safe_print

    available = [pair for pair in pairs if pair.spotify_metadata.candidates]
    for pair in available:
        pair.spotify_metadata.selected = _spotify_market_best_candidates(pair.spotify_metadata, pair.metadata)

    if dry_run or not available:
        return

    print_func("")
    print_func("Spotify 最佳候选：")
    for pair in available:
        best = _spotify_market_best_candidates(pair.spotify_metadata, pair.metadata)
        print_func(f"  {pair.ttml_path.name}: {_format_spotify_candidate_list(best)}")

    while True:
        answer = input_func("Accept all Spotify best candidates? Type Y to accept, N to choose alternatives: ").strip()
        if answer.casefold() in {"y", "n"}:
            break
        print_func("Please type Y or N.")

    if answer.casefold() == "y":
        return

    for pair in available:
        selected: list[SpotifyTrackCandidate] = []
        print_func("")
        print_func(f"{pair.ttml_path.name} Spotify 候选：")
        market_groups = pair.spotify_metadata.candidates_by_market or _spotify_candidates_grouped_by_market(
            pair.spotify_metadata.candidates
        )
        for market in _spotify_market_order_from_mapping(market_groups):
            options = market_groups.get(market, [])[:5]
            if not options:
                continue
            print_func(f"  {market} Spotify 候选：")
            for index, candidate in enumerate(options, start=1):
                print_func(f"    {index}. {_format_spotify_candidate(candidate)}")
            while True:
                answer = input_func(f"Select {market} 1-5, or press Enter to skip this market: ").strip()
                if not answer:
                    break
                if answer.isdigit() and 1 <= int(answer) <= len(options):
                    selected.append(options[int(answer) - 1])
                    break
                print_func("Invalid selection.")
        pair.spotify_metadata.selected = selected


def update_ttml_metadata(
    path: Path,
    values: dict[str, list[str]],
    dry_run: bool,
    backup_paths: dict[Path, Path] | None = None,
) -> TtmlUpdateResult:
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
        backup_path = _ensure_backup(path, backup_paths)
        result.backup_path = backup_path
        output = text[:metadata_start] + metadata + text[metadata_end:]
        path.write_text(output, encoding="utf-8")

    return result


def normalize_ttml_language(
    path: Path,
    dry_run: bool,
    backup_paths: dict[Path, Path] | None = None,
) -> TtmlLanguageNormalizationResult:
    text = path.read_text(encoding="utf-8")
    root_match = _find_root_tt_tag(text)
    if not root_match:
        raise ValueError("missing <tt> root; refusing to normalize TTML language")

    root_tag = root_match.group(0)
    root_attrs = _parse_xml_attributes(root_tag, root_match.start())
    lang_attr = root_attrs.get("xml:lang")
    if not lang_attr or lang_attr.value != "zh-Hant":
        return TtmlLanguageNormalizationResult()

    result = TtmlLanguageNormalizationResult(language_changed=True)
    output = text[: lang_attr.value_start] + "zh-Hans" + text[lang_attr.value_end :]

    output, result.removed_translations = _remove_zh_hans_replacement_translations(output)
    output, result.removed_transliterations = _remove_pinyin_transliterations(output)
    output = _remove_empty_layer_containers(output, "translations")
    output = _remove_empty_layer_containers(output, "transliterations")

    converted = _convert_body_text_nodes_to_simplified(output)
    if converted != output:
        result.body_text_changed = True
        output = converted

    if result.changed:
        try:
            ET.fromstring(output)
        except ET.ParseError as exc:
            raise ValueError(f"normalized TTML is not valid XML: {exc}") from exc

    if result.changed and not dry_run:
        result.backup_path = _ensure_backup(path, backup_paths)
        path.write_text(output, encoding="utf-8")

    return result


def values_from_metadata(
    metadata: AudioMetadata,
    apple_music_values: dict[str, list[str]] | None = None,
    qq_music_candidate: QQMusicCandidate | None = None,
    ncm_music_candidate: NCMusicCandidate | None = None,
    spotify_candidates: Iterable[SpotifyTrackCandidate] | None = None,
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
    for spotify_candidate in spotify_candidates or []:
        _merge_spotify_metadata(values, metadata, spotify_candidate)
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
    spotify_credentials = load_spotify_credentials()
    spotify_client = SpotifyClient(spotify_credentials) if spotify_credentials.enabled else None
    failures = 0
    backup_paths: dict[Path, Path] = {}
    prepared_pairs: list[PairMetadata] = []
    for work_item in work_items:
        try:
            normalization = normalize_ttml_language(work_item.ttml_path, dry_run=args.dry_run, backup_paths=backup_paths)
            _print_language_normalization_result(work_item.ttml_path, normalization, dry_run=args.dry_run)
        except Exception as exc:
            failures += 1
            _safe_print(f"[error] {work_item.ttml_path.name}: {exc}", file=sys.stderr)
            continue

        try:
            prepared_pairs.append(
                _prepare_work_item(
                    work_item,
                    apple_music_client,
                    qq_music_client,
                    ncm_music_client,
                    spotify_client,
                )
            )
        except Exception as exc:
            failures += 1
            _safe_print(f"[error] {work_item.ttml_path.name}: {exc}", file=sys.stderr)

    confirm_qq_music_candidates(prepared_pairs, dry_run=args.dry_run)
    _collect_ncm_music_metadata_for_pairs(prepared_pairs, ncm_music_client)
    confirm_ncm_music_candidates(prepared_pairs, dry_run=args.dry_run)
    confirm_spotify_candidates(prepared_pairs, dry_run=args.dry_run)

    for pair in prepared_pairs:
        try:
            _process_prepared_pair(pair, dry_run=args.dry_run, backup_paths=backup_paths)
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
    spotify_client: SpotifyClientProtocol | None = None,
) -> PairMetadata:
    metadata = read_audio_metadata(audio_path)
    apple_music_metadata = collect_apple_music_metadata(metadata, apple_music_client)
    qq_music_metadata = collect_qq_music_metadata(metadata, qq_music_client)
    spotify_metadata = collect_spotify_metadata(metadata, spotify_client)
    return PairMetadata(
        audio_path,
        ttml_path,
        metadata,
        apple_music_metadata,
        qq_music_metadata,
        NCMusicSearchResult(),
        spotify_metadata,
    )


def _prepare_work_item(
    work_item: WorkItem,
    apple_music_client: AppleMusicClientProtocol,
    qq_music_client: QQMusicClientProtocol,
    ncm_music_client: NCMusicClientProtocol | None = None,
    spotify_client: SpotifyClientProtocol | None = None,
) -> PairMetadata:
    if work_item.audio_path:
        return _prepare_pair(
            work_item.audio_path,
            work_item.ttml_path,
            apple_music_client,
            qq_music_client,
            ncm_music_client,
            spotify_client,
        )

    metadata = read_ttml_metadata(work_item.ttml_path)
    if not metadata.title:
        raise ValueError("TTML 中未读取到歌名，跳过 QQ 音乐搜索、网易云音乐搜索和 Spotify 搜索")
    return PairMetadata(
        None,
        work_item.ttml_path,
        metadata,
        AppleMusicMetadataResult(),
        collect_qq_music_metadata(metadata, qq_music_client),
        NCMusicSearchResult(),
        collect_spotify_metadata(metadata, spotify_client),
    )


def _process_pair(
    audio_path: Path,
    ttml_path: Path,
    client: AppleMusicClientProtocol,
    dry_run: bool,
    qq_music_client: QQMusicClientProtocol | None = None,
    ncm_music_client: NCMusicClientProtocol | None = None,
    spotify_client: SpotifyClientProtocol | None = None,
) -> None:
    pair = _prepare_pair(
        audio_path,
        ttml_path,
        client,
        qq_music_client or QQMusicClient(),
        ncm_music_client,
        spotify_client,
    )
    confirm_qq_music_candidates([pair], dry_run=dry_run)
    _collect_ncm_music_metadata_for_pairs([pair], ncm_music_client or NCMusicClient())
    confirm_ncm_music_candidates([pair], dry_run=dry_run)
    _process_prepared_pair(pair, dry_run=dry_run)


def _collect_ncm_music_metadata_for_pairs(
    pairs: list[PairMetadata],
    ncm_music_client: NCMusicClientProtocol,
) -> None:
    for pair in pairs:
        pair.ncm_music_metadata = collect_ncm_music_metadata(
            pair.metadata,
            ncm_music_client,
            qq_music_candidate=pair.qq_music_metadata.selected,
        )


def _process_prepared_pair(
    pair: PairMetadata,
    dry_run: bool,
    backup_paths: dict[Path, Path] | None = None,
) -> None:
    values = values_from_metadata(
        pair.metadata,
        pair.apple_music_metadata.values,
        qq_music_candidate=pair.qq_music_metadata.selected,
        ncm_music_candidate=pair.ncm_music_metadata.selected,
        spotify_candidates=pair.spotify_metadata.selected,
    )
    audio_path = pair.audio_path
    ttml_path = pair.ttml_path
    apple_music_metadata = pair.apple_music_metadata
    qq_music_metadata = pair.qq_music_metadata
    ncm_music_metadata = pair.ncm_music_metadata
    spotify_metadata = pair.spotify_metadata
    result = update_ttml_metadata(ttml_path, values, dry_run=dry_run, backup_paths=backup_paths)

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
    _safe_print(
        "  spotifyBest: "
        + (_format_spotify_candidate_list(spotify_metadata.selected) or "-")
    )
    _safe_print(
        "  spotifyId: "
        + (", ".join(_unique_spotify_ids(spotify_metadata.selected)) or "-")
    )
    if spotify_metadata.errors:
        for error in spotify_metadata.errors:
            _safe_print(f"  lookup warning: {error}")
    _print_change_group("added", result.added)
    _print_change_group("replaced", result.replaced)
    _print_change_group("skipped", result.skipped)
    if result.backup_path:
        _safe_print(f"  backup: {result.backup_path}")


def _print_language_normalization_result(
    ttml_path: Path,
    result: TtmlLanguageNormalizationResult,
    dry_run: bool,
) -> None:
    if not result.changed:
        return

    status = "dry-run" if dry_run else "normalized"
    _safe_print(f"[{status}] {ttml_path.name}")
    if result.language_changed:
        _safe_print("  language: zh-Hant -> zh-Hans")
    if result.body_text_changed:
        _safe_print("  lyrics: traditional -> simplified")
    if result.removed_translations:
        _safe_print(f"  removed: zh-Hans replacement translations = {result.removed_translations}")
    if result.removed_transliterations:
        _safe_print(f"  removed: zh-Latn-pinyin transliterations = {result.removed_transliterations}")
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


def _merge_spotify_metadata(
    values: dict[str, list[str]],
    metadata: AudioMetadata,
    candidate: SpotifyTrackCandidate,
) -> None:
    _add_unique_value(values, "spotifyId", candidate.track_id)
    if candidate.isrc and not _same_identifier(candidate.isrc, metadata.isrc):
        _add_unique_value(values, "isrc", candidate.isrc)
    existing_titles = [metadata.title, *values.get("musicName", [])]
    if candidate.title and not any(_same_raw_text(candidate.title, existing) for existing in existing_titles):
        _add_unique_value(values, "musicName", candidate.title)
    existing_artists = [*metadata.artists, *values.get("artists", [])]
    for artist in candidate.artists:
        if not any(_same_raw_text(artist, existing) for existing in existing_artists):
            _add_unique_value(values, "artists", artist)
            existing_artists.append(artist)
    existing_albums = [metadata.album, *values.get("album", [])]
    if candidate.album and not any(_same_raw_text(candidate.album, existing) for existing in existing_albums):
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


def _parse_ncm_artist_candidates(payload: dict[str, Any]) -> list[_NCMusicArtistCandidate]:
    artists = _nested_get(payload, "result", "artists")
    if not isinstance(artists, list):
        return []

    candidates: list[_NCMusicArtistCandidate] = []
    for index, artist in enumerate(artists):
        if not isinstance(artist, dict):
            continue
        artist_id = _stringify_tag_value(artist.get("id"))
        if not artist_id:
            continue
        candidates.append(
            _NCMusicArtistCandidate(
                artist_id=artist_id,
                name=_stringify_tag_value(artist.get("name")),
                aliases=_ncm_artist_aliases(artist),
                source_index=index,
            )
        )
    return candidates


def _parse_ncm_artist_album_candidates(payload: dict[str, Any]) -> list[_NCMusicAlbumCandidate]:
    albums = payload.get("hotAlbums")
    if not isinstance(albums, list):
        albums = _nested_get(payload, "result", "albums")
    if not isinstance(albums, list):
        return []

    candidates: list[_NCMusicAlbumCandidate] = []
    for index, album in enumerate(albums):
        if not isinstance(album, dict):
            continue
        album_id = _stringify_tag_value(album.get("id"))
        if not album_id:
            continue
        candidates.append(
            _NCMusicAlbumCandidate(
                album_id=album_id,
                name=_stringify_tag_value(album.get("name") or album.get("title")),
                source_index=index,
            )
        )
    return candidates


def _parse_ncm_album_song_candidates(payload: dict[str, Any]) -> list[NCMusicCandidate]:
    songs = payload.get("songs")
    if not isinstance(songs, list):
        songs = _nested_get(payload, "result", "songs")
    if not isinstance(songs, list):
        songs = _nested_get(payload, "album", "songs")
    if not isinstance(songs, list):
        return []
    return _parse_ncm_music_candidates({"result": {"songs": songs}})


def _parse_spotify_candidates(payload: dict[str, Any], market: str) -> list[SpotifyTrackCandidate]:
    tracks = _nested_get(payload, "tracks", "items")
    if not isinstance(tracks, list):
        return []

    candidates: list[SpotifyTrackCandidate] = []
    for index, track in enumerate(tracks):
        if not isinstance(track, dict):
            continue
        candidate = _spotify_track_candidate_from_track(track, market, index, "search")
        if candidate:
            candidates.append(candidate)
    return candidates


def _parse_spotify_artist_candidates(payload: dict[str, Any]) -> list[_SpotifyArtistCandidate]:
    artists = _nested_get(payload, "artists", "items")
    if not isinstance(artists, list):
        return []

    candidates: list[_SpotifyArtistCandidate] = []
    for index, artist in enumerate(artists):
        if not isinstance(artist, dict):
            continue
        artist_id = _stringify_tag_value(artist.get("id"))
        if not artist_id:
            continue
        candidates.append(
            _SpotifyArtistCandidate(
                artist_id=artist_id,
                name=_stringify_tag_value(artist.get("name")),
                source_index=index,
            )
        )
    return candidates


def _parse_spotify_artist_album_candidates(payload: dict[str, Any]) -> list[_SpotifyAlbumCandidate]:
    albums = payload.get("items")
    if not isinstance(albums, list):
        return []

    candidates: list[_SpotifyAlbumCandidate] = []
    for index, album in enumerate(albums):
        if not isinstance(album, dict):
            continue
        album_id = _stringify_tag_value(album.get("id"))
        if not album_id:
            continue
        candidates.append(
            _SpotifyAlbumCandidate(
                album_id=album_id,
                name=_stringify_tag_value(album.get("name")),
                release_date=_normalize_release_date(album.get("release_date")),
                release_date_precision=_stringify_tag_value(album.get("release_date_precision")),
                source_index=index,
            )
        )
    return candidates


def _parse_spotify_album_track_candidates(payload: dict[str, Any], market: str) -> list[SpotifyTrackCandidate]:
    tracks = _nested_get(payload, "tracks", "items")
    if not isinstance(tracks, list):
        return []

    album_id = _stringify_tag_value(payload.get("id"))
    album_name = _stringify_tag_value(payload.get("name"))
    album_artists = _spotify_artists(payload.get("artists"))
    release_date = _normalize_release_date(payload.get("release_date"))
    release_date_precision = _stringify_tag_value(payload.get("release_date_precision"))
    candidates: list[SpotifyTrackCandidate] = []
    for index, track in enumerate(tracks):
        if not isinstance(track, dict):
            continue
        candidate = _spotify_track_candidate_from_track(
            track,
            market,
            index,
            "artist-album",
            album_id=album_id,
            album_name=album_name,
            album_artists=album_artists,
            release_date=release_date,
            release_date_precision=release_date_precision,
        )
        if candidate:
            candidates.append(candidate)
    return candidates


def _ncm_artist_aliases(artist: dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    for key in ("alias", "alia", "trans"):
        value = artist.get(key)
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


def _spotify_artists(value: Any) -> list[str]:
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


def _spotify_track_candidate_from_track(
    track: dict[str, Any],
    market: str,
    source_index: int,
    match_source: str,
    album_id: str | None = None,
    album_name: str | None = None,
    album_artists: list[str] | None = None,
    release_date: str | None = None,
    release_date_precision: str | None = None,
) -> SpotifyTrackCandidate | None:
    track_id = _stringify_tag_value(track.get("id"))
    if not track_id:
        return None

    album = track.get("album")
    parsed_album_name = album_name or _spotify_album(album)
    parsed_album_id = album_id or _spotify_album_id(album)
    parsed_release_date = release_date or _spotify_album_release_date(album)
    parsed_release_date_precision = release_date_precision or _spotify_album_release_date_precision(album)
    artists = _spotify_artists(track.get("artists"))
    if not artists and album_artists:
        artists = list(album_artists)

    return SpotifyTrackCandidate(
        track_id=track_id,
        title=_stringify_tag_value(track.get("name")),
        artists=artists,
        album=parsed_album_name,
        market=market,
        source_index=source_index,
        isrc=_spotify_isrc(track),
        duration_ms=_parse_number(track.get("duration_ms")),
        release_date=parsed_release_date,
        release_date_precision=parsed_release_date_precision,
        album_id=parsed_album_id,
        match_source=match_source,
    )


def _spotify_album(value: Any) -> str | None:
    if isinstance(value, dict):
        return _stringify_tag_value(value.get("name") or value.get("title"))
    return _stringify_tag_value(value)


def _spotify_album_id(value: Any) -> str | None:
    if isinstance(value, dict):
        return _stringify_tag_value(value.get("id"))
    return None


def _spotify_album_release_date(value: Any) -> str | None:
    if isinstance(value, dict):
        return _normalize_release_date(value.get("release_date"))
    return None


def _spotify_album_release_date_precision(value: Any) -> str | None:
    if isinstance(value, dict):
        return _stringify_tag_value(value.get("release_date_precision"))
    return None


def _spotify_isrc(track: dict[str, Any]) -> str | None:
    external_ids = track.get("external_ids")
    if not isinstance(external_ids, dict):
        return None
    return _stringify_tag_value(external_ids.get("isrc"))


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


def _ncm_music_candidate_score(context: NCMusicSearchContext, candidate: NCMusicCandidate) -> int:
    title_score = _ncm_candidate_title_score(context, candidate)
    score = title_score * 100
    for artist in context.artists:
        score += max((_text_match_score(artist, candidate_artist) for candidate_artist in candidate.artists), default=0) * 60
    score += max((_text_match_score(album, candidate.album) for album in context.albums), default=0) * 30
    return score


def _spotify_candidate_score(metadata: AudioMetadata, candidate: SpotifyTrackCandidate) -> int:
    score = 0
    if _same_identifier(metadata.isrc, candidate.isrc):
        score += 1000
    score += _text_match_score(metadata.title, candidate.title) * 100
    for artist in metadata.artists:
        score += max((_text_match_score(artist, candidate_artist) for candidate_artist in candidate.artists), default=0) * 80
    score += _text_match_score(metadata.album, candidate.album) * 40
    return score


def _ncm_candidate_title_score(context: NCMusicSearchContext, candidate: NCMusicCandidate) -> int:
    return max(
        [
            _text_match_score(title, candidate_title)
            for title in context.titles
            for candidate_title in [candidate.title, *candidate.aliases]
        ],
        default=0,
    )


def _build_ncm_music_search_context(
    metadata: AudioMetadata,
    qq_music_candidate: QQMusicCandidate | None = None,
) -> NCMusicSearchContext:
    titles: list[str] = []
    artists: list[str] = []
    albums: list[str] = []

    _add_text_with_simplified_variants(titles, metadata.title)
    for artist in metadata.artists:
        _add_text_with_simplified_variants(artists, artist)
    _add_text_with_simplified_variants(albums, metadata.album)

    if qq_music_candidate:
        _add_text_with_simplified_variants(titles, qq_music_candidate.title)
        _add_text_with_simplified_variants(titles, qq_music_candidate.subtitle)
        for artist in qq_music_candidate.artists:
            _add_text_with_simplified_variants(artists, artist)
        _add_text_with_simplified_variants(albums, qq_music_candidate.album)

    return NCMusicSearchContext(titles=titles, artists=artists, albums=albums)


def _text_with_simplified_variants(value: Any) -> list[str]:
    variants: list[str] = []
    _add_text_with_simplified_variants(variants, value)
    return variants


def _add_text_with_simplified_variants(values: list[str], value: Any) -> None:
    text = _stringify_tag_value(value)
    if not text:
        return
    for variant in (text, _to_simplified_text(text)):
        if variant and variant not in values:
            values.append(variant)


def _ncm_artist_matches(context: NCMusicSearchContext, artist: _NCMusicArtistCandidate) -> bool:
    candidate_names = [artist.name, *artist.aliases]
    return any(_text_match_score(expected, actual) > 0 for expected in context.artists for actual in candidate_names)


def _ncm_album_matches(context: NCMusicSearchContext, album: _NCMusicAlbumCandidate) -> bool:
    return any(_text_match_score(expected, album.name) > 0 for expected in context.albums)


def _spotify_artist_matches(metadata: AudioMetadata, artist: _SpotifyArtistCandidate) -> bool:
    return any(_text_match_score(expected, artist.name) > 0 for expected in metadata.artists)


def _spotify_album_fallback_track_matches(metadata: AudioMetadata, candidate: SpotifyTrackCandidate) -> bool:
    if not (
        metadata.title
        and metadata.release_date
        and metadata.duration_seconds is not None
        and candidate.release_date
        and candidate.duration_ms is not None
    ):
        return False
    if _instrumental_marker_conflicts(metadata.title, candidate.title):
        return False
    if metadata.artists and not any(
        _text_match_score(expected, actual) > 0 for expected in metadata.artists for actual in candidate.artists
    ):
        return False
    if not _release_date_matches(metadata.release_date, candidate.release_date, candidate.release_date_precision):
        return False
    return _duration_close(metadata.duration_seconds, candidate.duration_ms)


def _spotify_candidate_auto_matches(metadata: AudioMetadata, candidate: SpotifyTrackCandidate) -> bool:
    if _instrumental_marker_conflicts(metadata.title, candidate.title):
        return False
    if _same_identifier(metadata.isrc, candidate.isrc):
        return True
    artist_matches = not metadata.artists or any(
        _text_match_score(expected, actual) > 0 for expected in metadata.artists for actual in candidate.artists
    )
    if not artist_matches:
        return False
    if _text_match_score(metadata.title, candidate.title) > 0:
        return True
    return (
        metadata.release_date is not None
        and metadata.duration_seconds is not None
        and _release_date_matches(metadata.release_date, candidate.release_date, candidate.release_date_precision)
        and _duration_close(metadata.duration_seconds, candidate.duration_ms)
    )


def _instrumental_marker_conflicts(expected_title: Any, candidate_title: Any) -> bool:
    return not _has_instrumental_marker(expected_title) and _has_instrumental_marker(candidate_title)


def _has_instrumental_marker(value: Any) -> bool:
    text = _normalize_match_text(value)
    if not text:
        return False
    markers = [
        "instrumental",
        " inst",
        "inst.",
        "off vocal",
        "off-vocal",
        "karaoke",
        "伴奏",
        "纯音乐",
        "純音樂",
        "インスト",
        "カラオケ",
        "반주",
    ]
    padded = f" {text} "
    return any(marker in padded for marker in markers)


def _dedupe_ncm_music_candidates(candidates: Iterable[NCMusicCandidate]) -> list[NCMusicCandidate]:
    unique: list[NCMusicCandidate] = []
    seen_song_ids: set[str] = set()
    for candidate in candidates:
        if candidate.song_id in seen_song_ids:
            continue
        seen_song_ids.add(candidate.song_id)
        unique.append(
            NCMusicCandidate(
                song_id=candidate.song_id,
                title=candidate.title,
                aliases=list(candidate.aliases),
                artists=list(candidate.artists),
                album=candidate.album,
                source_index=len(unique),
            )
        )
    return unique


def _dedupe_spotify_candidates(candidates: Iterable[SpotifyTrackCandidate]) -> list[SpotifyTrackCandidate]:
    unique: list[SpotifyTrackCandidate] = []
    seen_track_ids: set[str] = set()
    for candidate in candidates:
        if candidate.track_id in seen_track_ids:
            continue
        seen_track_ids.add(candidate.track_id)
        unique.append(candidate)
    return unique


def _spotify_candidates_grouped_by_market(
    candidates: Iterable[SpotifyTrackCandidate],
) -> dict[str, list[SpotifyTrackCandidate]]:
    grouped: dict[str, list[SpotifyTrackCandidate]] = {}
    for candidate in candidates:
        if not candidate.market:
            continue
        grouped.setdefault(candidate.market, []).append(candidate)
    return grouped


def _spotify_market_best_candidates(
    result: SpotifySearchResult,
    metadata: AudioMetadata | None = None,
) -> list[SpotifyTrackCandidate]:
    market_groups = result.candidates_by_market or _spotify_candidates_grouped_by_market(result.candidates)
    selected_by_market: dict[str, SpotifyTrackCandidate] = {}
    for market in _spotify_market_order_from_mapping(market_groups):
        candidates = market_groups.get(market, [])
        if not candidates:
            continue
        if metadata is None:
            selected_by_market[market] = candidates[0]
            continue
        for candidate in candidates:
            if _spotify_candidate_auto_matches(metadata, candidate):
                selected_by_market[market] = candidate
                break
    if metadata is not None:
        selected_ids = {candidate.track_id for candidate in selected_by_market.values()}
        for market in _spotify_market_order_from_mapping(market_groups):
            if market in selected_by_market:
                continue
            for candidate in market_groups.get(market, []):
                if candidate.track_id in selected_ids:
                    selected_by_market[market] = candidate
                    break
    return [
        selected_by_market[market]
        for market in _spotify_market_order_from_mapping(market_groups)
        if market in selected_by_market
    ]


def _spotify_market_order_from_mapping(
    market_groups: dict[str, list[SpotifyTrackCandidate]],
) -> list[str]:
    markets = [market for market in DEFAULT_SPOTIFY_MARKETS if market in market_groups]
    for market in market_groups:
        if market not in markets:
            markets.append(market)
    return markets


def _unique_spotify_ids(candidates: Iterable[SpotifyTrackCandidate]) -> list[str]:
    values: list[str] = []
    for candidate in candidates:
        if candidate.track_id not in values:
            values.append(candidate.track_id)
    return values


def _spotify_market_order(candidates: Iterable[SpotifyTrackCandidate]) -> list[str]:
    markets = list(DEFAULT_SPOTIFY_MARKETS)
    for candidate in candidates:
        if candidate.market and candidate.market not in markets:
            markets.append(candidate.market)
    return markets


def _spotify_market_index(market: str) -> int:
    try:
        return DEFAULT_SPOTIFY_MARKETS.index(market)
    except ValueError:
        return len(DEFAULT_SPOTIFY_MARKETS)


def _spotify_search_query(metadata: AudioMetadata) -> str:
    parts: list[str] = []
    if metadata.title:
        parts.append(f"track:{metadata.title}")
    if metadata.artists:
        parts.append(f"artist:{' '.join(metadata.artists)}")
    if metadata.album:
        parts.append(f"album:{metadata.album}")
    return " ".join(parts)


def _spotify_search_queries(metadata: AudioMetadata) -> list[str]:
    queries: list[str] = []
    isrc = _stringify_tag_value(metadata.isrc)
    if isrc:
        queries.append(f"isrc:{isrc}")
    loose_parts = [
        _stringify_tag_value(metadata.title),
        " ".join(metadata.artists) if metadata.artists else None,
        _stringify_tag_value(metadata.album),
    ]
    loose_query = " ".join(part for part in loose_parts if part)
    if loose_query:
        queries.append(loose_query)
    title_query = _stringify_tag_value(metadata.title)
    if title_query and title_query not in queries:
        queries.append(title_query)
    metadata_query = _spotify_search_query(metadata)
    if metadata_query and metadata_query not in queries:
        queries.append(metadata_query)
    return queries


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
    normalized = _to_simplified_text(str(value)).casefold().strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _to_simplified_text(value: str) -> str:
    return OPENCC_T2S.convert(value)


def _same_raw_text(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    return str(left).strip() == str(right).strip()


def _same_identifier(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    return str(left).strip().casefold() == str(right).strip().casefold()


def _nested_get(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _read_dotenv_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in {"SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"}:
            continue
        cleaned = _clean_env_value(value)
        if cleaned:
            values[key] = cleaned
    return values


def _clean_env_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text or None


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


def _format_spotify_candidate(candidate: SpotifyTrackCandidate) -> str:
    title = candidate.title or "-"
    artists = "/".join(candidate.artists) or "-"
    album = candidate.album or "-"
    market = candidate.market or "-"
    return f"{market}: {title} - {artists} - {album} [{candidate.track_id}]"


def _format_spotify_candidate_list(candidates: Iterable[SpotifyTrackCandidate]) -> str:
    return ", ".join(_format_spotify_candidate(candidate) for candidate in candidates)


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


def _find_root_tt_tag(text: str) -> re.Match[str] | None:
    return re.search(r"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?tt)\b[^>]*>", text, flags=re.DOTALL)


def _find_element_inner_bounds(text: str, local_name: str) -> tuple[int, int] | None:
    open_match = re.search(rf"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?{re.escape(local_name)})\b[^>]*>", text, flags=re.DOTALL)
    if not open_match:
        return None
    if open_match.group(0).rstrip().endswith("/>"):
        return None

    tag_name = open_match.group("tag")
    close_match = re.search(rf"</{re.escape(tag_name)}\s*>", text[open_match.end() :], flags=re.DOTALL)
    if not close_match:
        raise ValueError(f"missing </{tag_name}>; refusing to rewrite TTML")
    return open_match.end(), open_match.end() + close_match.start()


def _remove_zh_hans_replacement_translations(text: str) -> tuple[str, int]:
    pattern = re.compile(
        r"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?translation)\b"
        r"(?=[^>]*\btype\s*=\s*(?P<type_quote>[\"'])replacement(?P=type_quote))"
        r"(?=[^>]*\bxml:lang\s*=\s*(?P<lang_quote>[\"'])zh-Hans(?P=lang_quote))"
        r"[^>]*(?:/>|>.*?</(?P=tag)\s*>)",
        flags=re.DOTALL,
    )
    return pattern.subn("", text)


def _remove_pinyin_transliterations(text: str) -> tuple[str, int]:
    pattern = re.compile(
        r"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?transliteration)\b"
        r"(?=[^>]*\bxml:lang\s*=\s*(?P<lang_quote>[\"'])zh-Latn-pinyin(?P=lang_quote))"
        r"[^>]*(?:/>|>.*?</(?P=tag)\s*>)",
        flags=re.DOTALL,
    )
    return pattern.subn("", text)


def _remove_empty_layer_containers(text: str, local_name: str) -> str:
    pattern = re.compile(
        rf"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?{re.escape(local_name)})\b[^>]*>\s*</(?P=tag)\s*>",
        flags=re.DOTALL,
    )
    return pattern.sub("", text)


def _convert_body_text_nodes_to_simplified(text: str) -> str:
    bounds = _find_element_inner_bounds(text, "body")
    if not bounds:
        return text

    start, end = bounds
    body = text[start:end]
    converted = _convert_xml_text_nodes_to_simplified(
        body,
        skip_local_names={"translations", "translation", "transliterations", "transliteration"},
    )
    return text[:start] + converted + text[end:]


def _convert_xml_text_nodes_to_simplified(text: str, skip_local_names: set[str]) -> str:
    pieces = re.split(r"(<[^>]+>)", text)
    stack: list[str] = []
    output: list[str] = []

    for piece in pieces:
        if not piece:
            continue
        if piece.startswith("<"):
            _update_xml_stack(stack, piece)
            output.append(piece)
            continue
        if skip_local_names.isdisjoint(stack):
            output.append(_to_simplified_text(piece))
        else:
            output.append(piece)

    return "".join(output)


def _update_xml_stack(stack: list[str], tag_text: str) -> None:
    if tag_text.startswith(("<!--", "<?", "<!")):
        return

    close_match = re.match(r"</\s*(?P<name>[A-Za-z_][\w.-]*(?::[A-Za-z_][\w.-]*)?)", tag_text)
    if close_match:
        local_name = close_match.group("name").split(":")[-1]
        if stack and stack[-1] == local_name:
            stack.pop()
            return
        for index in range(len(stack) - 1, -1, -1):
            if stack[index] == local_name:
                del stack[index:]
                return
        return

    open_match = re.match(r"<\s*(?P<name>[A-Za-z_][\w.-]*(?::[A-Za-z_][\w.-]*)?)", tag_text)
    if not open_match or tag_text.rstrip().endswith("/>"):
        return
    stack.append(open_match.group("name").split(":")[-1])


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


def _ensure_backup(path: Path, backup_paths: dict[Path, Path] | None = None) -> Path:
    key = _backup_map_key(path)
    if backup_paths is not None and key in backup_paths:
        return backup_paths[key]

    backup_path = _backup_path(path)
    shutil.copy2(path, backup_path)
    if backup_paths is not None:
        backup_paths[key] = backup_path
    return backup_path


def _backup_map_key(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


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


def _normalize_release_date(value: Any) -> str | None:
    text = _stringify_tag_value(value)
    if not text:
        return None
    match = re.search(r"(\d{4})(?:[-./](\d{1,2})(?:[-./](\d{1,2}))?)?", text)
    if not match:
        return None
    year, month, day = match.groups()
    if day and month:
        return f"{year}-{int(month):02d}-{int(day):02d}"
    if month:
        return f"{year}-{int(month):02d}"
    return year


def _release_date_matches(expected: Any, actual: Any, actual_precision: Any = None) -> bool:
    expected_date = _normalize_release_date(expected)
    actual_date = _normalize_release_date(actual)
    if not expected_date or not actual_date:
        return False

    expected_parts = expected_date.split("-")
    actual_parts = actual_date.split("-")
    precision = (_stringify_tag_value(actual_precision) or "").casefold()
    if len(expected_parts) == 3:
        if precision and precision != "day":
            return False
        return len(actual_parts) == 3 and expected_parts == actual_parts
    if len(expected_parts) == 2:
        if precision == "year":
            return False
        return len(actual_parts) >= 2 and expected_parts[:2] == actual_parts[:2]
    return expected_parts[0] == actual_parts[0]


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
