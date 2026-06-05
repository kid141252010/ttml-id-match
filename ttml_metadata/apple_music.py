from __future__ import annotations

import html
import json
import re
import threading
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterable

from .console import _safe_print, _color_text
from .models import (
    APPLE_MUSIC_ARTIST_ALBUM_LIMIT,
    APPLE_MUSIC_ARTIST_ALBUM_PAGE_LIMIT,
    APPLE_MUSIC_ARTIST_SEARCH_LIMIT,
    APPLE_MUSIC_SEARCH_LIMIT,
    DEFAULT_STORES,
    AppleMusicClientProtocol,
    AppleMusicMetadataResult,
    AppleMusicTrackCandidate,
    AppleMusicTrackMatch,
    AudioMetadata,
    PairMetadata,
    _AppleMusicAlbumCandidate,
    _AppleMusicArtistCandidate,
)
from .network import urlopen_with_retry
from .text_utils import (
    _add_unique_list_value,
    _add_unique_value,
    _duration_close,
    _id_from_url,
    _instrumental_marker_conflicts,
    _instrumental_titles_match,
    _iso8601_duration_to_millis,
    _nested_get,
    _normalize_release_date,
    _normalize_title,
    _parse_number,
    _release_date_distance,
    _release_date_matches,
    _same_identifier,
    _same_raw_text,
    _stringify_tag_value,
    _text_match_score,
    _walk_json,
    split_artists,
)

class AppleMusicClient:
    def __init__(self, timeout: int = 20):
        self.timeout = timeout
        self._token: str | None = None
        self._page_cache: dict[tuple[str, str], str] = {}
        self._track_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._json_cache: dict[str, dict[str, Any]] = {}
        self._cache_lock = threading.RLock()

    def fetch_album_tracks(self, store: str, album_id: str) -> list[dict[str, Any]]:
        cache_key = (store, album_id)
        with self._cache_lock:
            cached = self._track_cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            tracks = self._fetch_album_tracks_from_amp_api(store, album_id)
        except Exception:
            tracks = self._fetch_album_tracks_from_json_ld(store, album_id)

        with self._cache_lock:
            return self._track_cache.setdefault(cache_key, tracks)

    def search_songs(self, store: str, metadata: AudioMetadata) -> list[AppleMusicTrackCandidate]:
        query = _apple_music_search_query(metadata)
        if not query:
            return []
        payload = self._read_catalog_json(
            store,
            self._build_search_url(store, query, "songs", APPLE_MUSIC_SEARCH_LIMIT),
        )
        songs = _nested_get(payload, "results", "songs", "data")
        if not isinstance(songs, list):
            return []
        candidates: list[AppleMusicTrackCandidate] = []
        for index, track in enumerate(songs):
            if not isinstance(track, dict):
                continue
            candidate = _apple_music_candidate_from_track(track, store, index, "search")
            if candidate:
                candidates.append(candidate)
        return candidates

    def search_artists(self, store: str, query: str) -> list[_AppleMusicArtistCandidate]:
        if not query:
            return []
        payload = self._read_catalog_json(
            store,
            self._build_search_url(store, query, "artists", APPLE_MUSIC_ARTIST_SEARCH_LIMIT),
        )
        artists = _nested_get(payload, "results", "artists", "data")
        if not isinstance(artists, list):
            return []
        candidates: list[_AppleMusicArtistCandidate] = []
        for index, artist in enumerate(artists):
            if not isinstance(artist, dict):
                continue
            artist_id = _stringify_tag_value(artist.get("id"))
            if not artist_id:
                continue
            candidates.append(
                _AppleMusicArtistCandidate(
                    artist_id,
                    _stringify_tag_value(_nested_get(artist, "attributes", "name")),
                    index,
                )
            )
        return candidates

    def fetch_artist_albums(self, store: str, artist_id: str) -> tuple[list[_AppleMusicAlbumCandidate], list[str]]:
        albums: list[_AppleMusicAlbumCandidate] = []
        warnings: list[str] = []
        for page_index in range(APPLE_MUSIC_ARTIST_ALBUM_PAGE_LIMIT):
            offset = page_index * APPLE_MUSIC_ARTIST_ALBUM_LIMIT
            payload = self._read_catalog_json(
                store,
                self._build_artist_albums_url(store, artist_id, offset),
            )
            data = payload.get("data")
            if not isinstance(data, list):
                return albums, warnings
            page_albums = _parse_apple_music_artist_album_candidates(data, len(albums))
            if not page_albums:
                return albums, warnings
            albums.extend(page_albums)

            total = _parse_number(_nested_get(payload, "meta", "total"))
            if total is not None and offset + APPLE_MUSIC_ARTIST_ALBUM_LIMIT >= total:
                return albums, warnings
            if not payload.get("next") and total is None:
                return albums, warnings

        warnings.append(f"{store}: artist {artist_id} albums truncated after {len(albums)} albums")
        return albums, warnings

    def _fetch_album_tracks_from_amp_api(self, store: str, album_id: str) -> list[dict[str, Any]]:
        url = f"https://amp-api.music.apple.com/v1/catalog/{store}/albums/{album_id}"
        payload = self._read_catalog_json(store, url, album_id=album_id)
        album = payload["data"][0]
        album_attributes = album.get("attributes", {})
        album_name = album_attributes.get("name")
        album_artist = album_attributes.get("artistName")
        release_date = album_attributes.get("releaseDate")
        tracks = album.get("relationships", {}).get("tracks", {}).get("data", [])
        return [
            self._track_from_amp_api_track(track, album_name, album_id, album_artist, release_date)
            for track in tracks
            if track.get("type") == "songs"
        ]

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

    def _get_bearer_token(self, store: str, album_id: str | None = None) -> str:
        with self._cache_lock:
            if self._token:
                return self._token
            page = self._get_album_page(store, album_id) if album_id else self._get_search_page(store)
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
        with self._cache_lock:
            cached = self._page_cache.get(cache_key)
        if cached is not None:
            return cached
        page = self._read_text(f"https://music.apple.com/{store}/album/{album_id}")
        with self._cache_lock:
            return self._page_cache.setdefault(cache_key, page)

    def _get_search_page(self, store: str) -> str:
        cache_key = (store, "__search__")
        with self._cache_lock:
            cached = self._page_cache.get(cache_key)
        if cached is not None:
            return cached
        page = self._read_text(f"https://music.apple.com/{store}/search")
        with self._cache_lock:
            return self._page_cache.setdefault(cache_key, page)

    def _read_catalog_json(self, store: str, url: str, album_id: str | None = None) -> dict[str, Any]:
        with self._cache_lock:
            cached = self._json_cache.get(url)
        if cached is not None:
            return cached
        token = self._get_bearer_token(store, album_id)
        data = self._read_text(
            url,
            {
                "Authorization": f"Bearer {token}",
                "Origin": "https://music.apple.com",
                "Referer": "https://music.apple.com/",
            },
        )
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError("Apple Music API returned a non-object payload")
        with self._cache_lock:
            return self._json_cache.setdefault(url, payload)

    def _build_search_url(self, store: str, query: str, types: str, limit: int) -> str:
        params = urllib.parse.urlencode(
            {
                "term": query,
                "types": types,
                "limit": limit,
            }
        )
        return f"https://amp-api.music.apple.com/v1/catalog/{store}/search?{params}"

    def _build_artist_albums_url(self, store: str, artist_id: str, offset: int = 0) -> str:
        params = urllib.parse.urlencode(
            {
                "include": "tracks",
                "limit": APPLE_MUSIC_ARTIST_ALBUM_LIMIT,
                "offset": offset,
            }
        )
        return (
            f"https://amp-api.music.apple.com/v1/catalog/{store}/artists/"
            f"{urllib.parse.quote(artist_id, safe='')}/albums?{params}"
        )

    def _read_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        request_headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/json,*/*",
        }
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(url, headers=request_headers)
        with urlopen_with_retry(request, timeout=self.timeout) as response:
            return response.read().decode("utf-8", "ignore")

    @staticmethod
    def _track_from_amp_api_track(
        track: dict[str, Any],
        album_name: Any = None,
        album_id: Any = None,
        album_artist: Any = None,
        release_date: Any = None,
    ) -> dict[str, Any]:
        attributes = track.get("attributes", {})
        return {
            "id": str(track.get("id") or ""),
            "name": attributes.get("name"),
            "artistName": attributes.get("artistName") or album_artist,
            "albumName": attributes.get("albumName") or album_name,
            "albumId": album_id,
            "isrc": attributes.get("isrc"),
            "discNumber": attributes.get("discNumber"),
            "trackNumber": attributes.get("trackNumber"),
            "durationInMillis": attributes.get("durationInMillis"),
            "releaseDate": attributes.get("releaseDate") or release_date,
        }


def collect_apple_music_metadata(
    metadata: AudioMetadata,
    client: AppleMusicClientProtocol,
    stores: list[str] | None = None,
) -> AppleMusicMetadataResult:
    result = AppleMusicMetadataResult()
    store_order = _apple_music_store_order(stores)

    if not metadata.title and not metadata.playlist_id:
        _sync_apple_music_result_values(result, metadata)
        if not result.values:
            result.sources.append("missing-apple-music-id")
            result.errors.append("音频中未读取到 Apple Music 歌曲 ID 或专辑 ID")
        return result

    all_candidates: list[AppleMusicTrackCandidate] = []
    for store in store_order:
        store_candidates: list[AppleMusicTrackCandidate] = []
        album_errors: list[str] = []

        if metadata.playlist_id:
            match = _match_album_store(metadata, client, store, metadata.playlist_id, album_errors)
            if match.track:
                candidate = _apple_music_candidate_from_flat_track(
                    match.track,
                    store,
                    len(store_candidates),
                    match.source,
                )
                if candidate:
                    store_candidates.append(candidate)

        if metadata.title:
            try:
                store_candidates.extend(
                    _normalize_apple_music_candidates(
                        client.search_songs(store, metadata),
                        store,
                        len(store_candidates),
                        "search",
                    )
                )
            except Exception as exc:
                result.errors.append(f"{store}: Apple Music 搜索失败: {exc}")

        if _apple_music_should_search_artist_albums(metadata, store_candidates):
            store_candidates.extend(
                _search_apple_music_artist_album_candidates(
                    metadata,
                    client,
                    store,
                    len(store_candidates),
                    result.errors,
                )
            )

        sorted_store_candidates = _dedupe_apple_music_candidates(
            sorted(
                store_candidates,
                key=lambda candidate: (
                    -_apple_music_candidate_score(metadata, candidate),
                    _apple_music_source_priority(candidate.match_source),
                    candidate.source_index,
                ),
            )
        )
        if sorted_store_candidates:
            result.candidates_by_storefront[store] = sorted_store_candidates
            all_candidates.extend(sorted_store_candidates)
        else:
            result.errors.extend(album_errors)

    result.candidates = sorted(
        all_candidates,
        key=lambda candidate: (
            -_apple_music_candidate_score(metadata, candidate),
            _apple_music_storefront_index(candidate.storefront),
            _apple_music_source_priority(candidate.match_source),
            candidate.source_index,
        ),
    )
    result.selected = _apple_music_storefront_best_candidates(result, metadata)
    _sync_apple_music_result_values(result, metadata)
    if not result.values and not result.candidates:
        result.sources.append("not-found")
        result.errors.append("Apple Music 未找到带歌曲 ID 的候选")
    return result


def confirm_apple_music_candidates(
    pairs: list[PairMetadata],
    dry_run: bool,
    input_func: Callable[[str], str] = input,
    print_func: Callable[..., None] | None = None,
) -> None:
    if print_func is None:
        print_func = _safe_print

    available = [pair for pair in pairs if pair.apple_music_metadata.candidates]
    for pair in available:
        pair.apple_music_metadata.selected = _apple_music_storefront_best_candidates(
            pair.apple_music_metadata,
            pair.metadata,
        )
        _sync_apple_music_result_values(pair.apple_music_metadata, pair.metadata)

    if dry_run or not available:
        return

    use_color = (print_func is _safe_print) and (input_func is input)

    print_func("")
    header_text = "Apple Music 最佳候选："
    if use_color:
        header_text = _color_text(header_text, "header")
    print_func(header_text)

    for pair in available:
        best = _apple_music_storefront_top_candidates(pair.apple_music_metadata)
        if not best:
            cand_str = _color_text("-", "unchanged") if use_color else "-"
            print_func(f"  {pair.ttml_path.name}: {cand_str}")
        else:
            print_func(f"  {pair.ttml_path.name}:")
            for candidate in best:
                cand_str = _format_apple_music_candidate(candidate)
                if use_color:
                    cand_str = _color_text(cand_str, "highlight")
                print_func(f"    - {cand_str}")

    while True:
        prompt_text = "Accept all Apple Music best candidates? Type Y to accept, N to choose alternatives: "
        if use_color:
            prompt_text = _color_text(prompt_text, "prompt")
        answer = input_func(prompt_text).strip()
        if answer.casefold() in {"y", "n"}:
            break
        print_func("Please type Y or N.")

    if answer.casefold() == "y":
        return

    for pair in available:
        selected: list[AppleMusicTrackCandidate] = []
        print_func("")
        cand_title = f"{pair.ttml_path.name} Apple Music 候选："
        if use_color:
            cand_title = _color_text(cand_title, "info")
        print_func(cand_title)

        storefront_groups = (
            pair.apple_music_metadata.candidates_by_storefront
            or _apple_music_candidates_grouped_by_storefront(pair.apple_music_metadata.candidates)
        )
        for storefront in _apple_music_store_order_from_mapping(storefront_groups):
            options = storefront_groups.get(storefront, [])[:5]
            if not options:
                continue
            label = storefront.upper()
            store_title = f"  {label} Apple Music 候选："
            if use_color:
                store_title = _color_text(store_title, "info")
            print_func(store_title)

            for index, candidate in enumerate(options, start=1):
                idx_str = f"    {index}."
                if use_color:
                    idx_str = _color_text(idx_str, "info")
                print_func(f"{idx_str} {_format_apple_music_candidate(candidate)}")
            while True:
                sel_prompt = f"Select {label} 1-5, or press Enter to skip this storefront: "
                if use_color:
                    sel_prompt = _color_text(sel_prompt, "prompt")
                answer = input_func(sel_prompt).strip()
                if not answer:
                    break
                if answer.isdigit() and 1 <= int(answer) <= len(options):
                    selected.append(options[int(answer) - 1])
                    break
                print_func("Invalid selection.")
        pair.apple_music_metadata.selected = selected
        _sync_apple_music_result_values(pair.apple_music_metadata, pair.metadata)


def _merge_track_metadata(values: dict[str, list[str]], track: dict[str, Any]) -> None:
    _add_unique_value(values, "musicName", _stringify_tag_value(track.get("name")))
    for artist in split_artists([track.get("artistName")]):
        _add_unique_value(values, "artists", artist)
    _add_unique_value(values, "album", _stringify_tag_value(track.get("albumName")))
    _add_unique_value(values, "appleMusicId", _track_id(track))
    _add_unique_value(values, "isrc", _stringify_tag_value(track.get("isrc")))


def _merge_apple_music_metadata(
    values: dict[str, list[str]],
    metadata: AudioMetadata,
    candidate: AppleMusicTrackCandidate,
) -> None:
    _add_unique_value(values, "appleMusicId", candidate.track_id)
    _add_unique_value(values, "isrc", candidate.isrc)
    _add_unique_value(values, "musicName", candidate.title)
    existing_artists = list(values.get("artists", []))
    for artist in candidate.artists:
        if not any(_same_raw_text(artist, existing) for existing in existing_artists):
            _add_unique_value(values, "artists", artist)
            existing_artists.append(artist)
    existing_albums = list(values.get("album", []))
    if candidate.album and not any(_same_raw_text(candidate.album, existing) for existing in existing_albums):
        _add_unique_value(values, "album", candidate.album)


def _sync_apple_music_result_values(result: AppleMusicMetadataResult, metadata: AudioMetadata) -> None:
    values: dict[str, list[str]] = {}
    sources: list[str] = []
    if is_valid_apple_music_song_id(metadata.catalog_id):
        _add_unique_value(values, "appleMusicId", str(metadata.catalog_id))
        _add_unique_list_value(sources, "catalog")
    for candidate in result.selected:
        _merge_apple_music_metadata(values, metadata, candidate)
        _add_unique_list_value(sources, _apple_music_candidate_source(candidate))
    result.values = values
    result.sources = sources


def _apple_music_search_query(metadata: AudioMetadata) -> str:
    parts = [
        _stringify_tag_value(metadata.title),
        " ".join(metadata.artists) if metadata.artists else None,
        _stringify_tag_value(metadata.album),
        _stringify_tag_value(metadata.isrc),
    ]
    return " ".join(part for part in parts if part)


def _apple_music_candidate_from_track(
    track: dict[str, Any],
    storefront: str,
    source_index: int,
    match_source: str,
    album_id: str | None = None,
    album_name: str | None = None,
    album_artist: str | None = None,
    release_date: str | None = None,
) -> AppleMusicTrackCandidate | None:
    flattened = AppleMusicClient._track_from_amp_api_track(track, album_name, album_id, album_artist, release_date)
    return _apple_music_candidate_from_flat_track(flattened, storefront, source_index, match_source)


def _apple_music_candidate_from_flat_track(
    track: dict[str, Any],
    storefront: str,
    source_index: int,
    match_source: str,
) -> AppleMusicTrackCandidate | None:
    track_id = _track_id(track)
    if not track_id:
        return None
    return AppleMusicTrackCandidate(
        track_id=track_id,
        title=_stringify_tag_value(track.get("name")),
        artists=split_artists([track.get("artistName")]),
        album=_stringify_tag_value(track.get("albumName")),
        storefront=storefront,
        source_index=source_index,
        isrc=_stringify_tag_value(track.get("isrc")),
        release_date=_normalize_release_date(track.get("releaseDate")),
        duration_ms=_parse_number(track.get("durationInMillis")),
        match_source=match_source,
    )


def _parse_apple_music_artist_album_candidates(
    albums: Iterable[Any],
    source_offset: int = 0,
) -> list[_AppleMusicAlbumCandidate]:
    candidates: list[_AppleMusicAlbumCandidate] = []
    for index, album in enumerate(albums):
        if not isinstance(album, dict):
            continue
        album_id = _stringify_tag_value(album.get("id"))
        if not album_id:
            continue
        attributes = album.get("attributes") if isinstance(album.get("attributes"), dict) else {}
        candidates.append(
            _AppleMusicAlbumCandidate(
                album_id,
                _stringify_tag_value(attributes.get("name")),
                _normalize_release_date(attributes.get("releaseDate")),
                source_offset + index,
            )
        )
    return candidates


def _normalize_apple_music_candidates(
    candidates: Iterable[AppleMusicTrackCandidate],
    storefront: str,
    source_offset: int,
    default_source: str,
) -> list[AppleMusicTrackCandidate]:
    normalized: list[AppleMusicTrackCandidate] = []
    for index, candidate in enumerate(candidates):
        if not candidate.track_id:
            continue
        normalized.append(
            AppleMusicTrackCandidate(
                track_id=candidate.track_id,
                title=candidate.title,
                artists=list(candidate.artists),
                album=candidate.album,
                storefront=candidate.storefront or storefront,
                source_index=source_offset + index,
                isrc=candidate.isrc,
                release_date=candidate.release_date,
                duration_ms=candidate.duration_ms,
                match_source=candidate.match_source or default_source,
            )
        )
    return normalized


def _search_apple_music_artist_album_candidates(
    metadata: AudioMetadata,
    client: AppleMusicClientProtocol,
    storefront: str,
    source_offset: int,
    errors: list[str],
) -> list[AppleMusicTrackCandidate]:
    candidates: list[AppleMusicTrackCandidate] = []
    seen_artist_ids: set[str] = set()
    for artist_name in split_artists(metadata.artists):
        try:
            artists = client.search_artists(storefront, artist_name)
        except Exception as exc:
            errors.append(f"{storefront}: Apple Music 歌手搜索失败: {exc}")
            continue
        for artist in artists:
            if artist.artist_id in seen_artist_ids or not _apple_music_artist_matches(metadata, artist):
                continue
            seen_artist_ids.add(artist.artist_id)
            try:
                albums, warnings = client.fetch_artist_albums(storefront, artist.artist_id)
            except Exception as exc:
                errors.append(f"{storefront}: Apple Music 歌手专辑读取失败: {exc}")
                continue
            errors.extend(warnings)
            for album in _sort_apple_music_albums_for_fallback(metadata, albums):
                if not _release_date_matches(metadata.release_date, album.release_date, "day"):
                    continue
                try:
                    tracks = client.fetch_album_tracks(storefront, album.album_id)
                except Exception as exc:
                    errors.append(f"{storefront}: Apple Music fallback 专辑 {album.album_id} 读取失败: {exc}")
                    continue
                for track in tracks:
                    candidate = _apple_music_candidate_from_flat_track(
                        track,
                        storefront,
                        source_offset + len(candidates),
                        "artist-album",
                    )
                    if candidate and _apple_music_album_fallback_track_matches(metadata, candidate):
                        candidates.append(candidate)
                if candidates:
                    return _dedupe_apple_music_candidates(candidates)
    return _dedupe_apple_music_candidates(candidates)


def _sort_apple_music_albums_for_fallback(
    metadata: AudioMetadata,
    albums: Iterable[_AppleMusicAlbumCandidate],
) -> list[_AppleMusicAlbumCandidate]:
    return sorted(
        albums,
        key=lambda album: (
            _release_date_distance(metadata.release_date, album.release_date),
            album.source_index,
        ),
    )


def _apple_music_artist_matches(metadata: AudioMetadata, artist: _AppleMusicArtistCandidate) -> bool:
    return any(_text_match_score(expected, artist.name) > 0 for expected in metadata.artists)


def _apple_music_candidate_score(metadata: AudioMetadata, candidate: AppleMusicTrackCandidate) -> int:
    if _instrumental_marker_conflicts(metadata.title, candidate.title):
        return -10_000 + candidate.source_index
    score = 0
    if _same_identifier(metadata.isrc, candidate.isrc):
        score += 500
    score += _text_match_score(metadata.title, candidate.title) * 100
    for artist in metadata.artists:
        score += max((_text_match_score(artist, candidate_artist) for candidate_artist in candidate.artists), default=0) * 80
    score += _text_match_score(metadata.album, candidate.album) * 40
    if _release_date_matches(metadata.release_date, candidate.release_date, "day"):
        score += 30
    if metadata.duration_seconds is not None and _duration_close(
        metadata.duration_seconds,
        candidate.duration_ms,
        tolerance_seconds=1.0,
    ):
        score += 30
    return score


def _apple_music_should_search_artist_albums(
    metadata: AudioMetadata,
    candidates: list[AppleMusicTrackCandidate],
) -> bool:
    if not (
        metadata.title
        and metadata.artists
        and metadata.release_date
        and metadata.duration_seconds is not None
    ):
        return False
    return not any(_apple_music_candidate_auto_matches(metadata, candidate) for candidate in candidates)


def _apple_music_album_fallback_track_matches(
    metadata: AudioMetadata,
    candidate: AppleMusicTrackCandidate,
) -> bool:
    if not (
        metadata.release_date
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
    if not _release_date_matches(metadata.release_date, candidate.release_date, "day"):
        return False
    return _duration_close(metadata.duration_seconds, candidate.duration_ms, tolerance_seconds=1.0)


def _apple_music_candidate_auto_matches(
    metadata: AudioMetadata,
    candidate: AppleMusicTrackCandidate,
) -> bool:
    if _instrumental_marker_conflicts(metadata.title, candidate.title):
        return False
    if _same_identifier(metadata.isrc, candidate.isrc):
        return True
    if (candidate.match_source or "").startswith("album:"):
        return True
    artist_matches = not metadata.artists or any(
        _text_match_score(expected, actual) > 0 for expected in metadata.artists for actual in candidate.artists
    )
    if not artist_matches:
        return False
    if _instrumental_titles_match(metadata.title, candidate.title):
        return True
    if _text_match_score(metadata.title, candidate.title) > 0:
        return True
    return (
        metadata.release_date is not None
        and metadata.duration_seconds is not None
        and _release_date_matches(metadata.release_date, candidate.release_date, "day")
        and _duration_close(metadata.duration_seconds, candidate.duration_ms, tolerance_seconds=1.0)
    )


def _dedupe_apple_music_candidates(
    candidates: Iterable[AppleMusicTrackCandidate],
) -> list[AppleMusicTrackCandidate]:
    seen: set[tuple[str, str]] = set()
    unique: list[AppleMusicTrackCandidate] = []
    for candidate in candidates:
        key = (candidate.storefront, candidate.track_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _apple_music_storefront_best_candidates(
    result: AppleMusicMetadataResult,
    metadata: AudioMetadata,
) -> list[AppleMusicTrackCandidate]:
    selected: list[AppleMusicTrackCandidate] = []
    groups = result.candidates_by_storefront or _apple_music_candidates_grouped_by_storefront(result.candidates)
    selected_ids_by_store: set[tuple[str, str]] = set()
    for storefront in _apple_music_store_order(groups.keys()):
        candidates = groups.get(storefront, [])
        for candidate in candidates:
            if _apple_music_candidate_auto_matches(metadata, candidate):
                key = (candidate.storefront, candidate.track_id)
                if key not in selected_ids_by_store:
                    selected.append(candidate)
                    selected_ids_by_store.add(key)
                break
    return selected


def _apple_music_storefront_top_candidates(
    result: AppleMusicMetadataResult,
) -> list[AppleMusicTrackCandidate]:
    groups = result.candidates_by_storefront or _apple_music_candidates_grouped_by_storefront(result.candidates)
    best: list[AppleMusicTrackCandidate] = []
    for storefront in _apple_music_store_order(groups.keys()):
        candidates = groups.get(storefront, [])
        if candidates:
            best.append(candidates[0])
    return best


def _apple_music_candidates_grouped_by_storefront(
    candidates: Iterable[AppleMusicTrackCandidate],
) -> dict[str, list[AppleMusicTrackCandidate]]:
    grouped: dict[str, list[AppleMusicTrackCandidate]] = {}
    for candidate in candidates:
        if not candidate.storefront:
            continue
        grouped.setdefault(candidate.storefront, []).append(candidate)
    return grouped


def _apple_music_store_order(stores: Iterable[str] | None = None) -> list[str]:
    ordered: list[str] = []
    for store in stores if stores is not None else DEFAULT_STORES:
        normalized = str(store).strip().lower()
        if normalized and normalized not in ordered:
            ordered.append(normalized)
    return ordered


def _apple_music_storefront_index(storefront: str) -> int:
    try:
        return DEFAULT_STORES.index(storefront.lower())
    except ValueError:
        return len(DEFAULT_STORES)


def _apple_music_source_priority(match_source: str) -> int:
    source = (match_source or "").casefold()
    if source.startswith("album"):
        return 0
    if source == "search":
        return 1
    if source == "artist-album":
        return 2
    return 3


def _apple_music_store_order_from_mapping(
    groups: dict[str, list[AppleMusicTrackCandidate]],
) -> list[str]:
    return _apple_music_store_order(groups.keys())


def _unique_apple_music_ids(candidates: Iterable[AppleMusicTrackCandidate]) -> list[str]:
    values: list[str] = []
    for candidate in candidates:
        if candidate.track_id not in values:
            values.append(candidate.track_id)
    return values


def _apple_music_candidate_source(candidate: AppleMusicTrackCandidate) -> str:
    source = candidate.match_source or "search"
    if source.startswith("album:"):
        return source
    return f"{source}:{candidate.storefront}"


def _format_apple_music_candidate(candidate: AppleMusicTrackCandidate) -> str:
    title = candidate.title or "-"
    artists = "/".join(candidate.artists) or "-"
    album = candidate.album or "-"
    storefront = candidate.storefront.upper() if candidate.storefront else "-"
    return f"{storefront}: {title} - {artists} - {album} [{candidate.track_id}]"


def _format_apple_music_candidate_list(candidates: Iterable[AppleMusicTrackCandidate]) -> str:
    return ", ".join(_format_apple_music_candidate(candidate) for candidate in candidates)


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


def is_valid_apple_music_song_id(value: str | None) -> bool:
    if not value:
        return False
    value = str(value).strip()
    return value.isdigit() and int(value) >= 100000


def _track_id(track: dict[str, Any]) -> str | None:
    value = str(track.get("id") or "").strip()
    return value or None
