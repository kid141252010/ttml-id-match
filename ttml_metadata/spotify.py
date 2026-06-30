from __future__ import annotations

import base64
import json
import os
import threading
import urllib.parse
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable

from .config import load_config_value, load_positive_int_config
from .console import _safe_print, _color_text
from .models import (
    DEFAULT_SPOTIFY_MARKETS,
    SPOTIFY_ARTIST_ALBUM_LIMIT,
    SPOTIFY_ARTIST_ALBUM_PAGE_LIMIT,
    SPOTIFY_ARTIST_SEARCH_LIMIT,
    SPOTIFY_CANDIDATE_TARGET,
    SPOTIFY_SEARCH_LIMIT,
    SPOTIFY_STRONG_MATCH_SCORE,
    AudioMetadata,
    PairMetadata,
    SpotifyClientProtocol,
    SpotifyCredentials,
    SpotifySearchResult,
    SpotifyTrackCandidate,
    _SpotifyAlbumCandidate,
    _SpotifyArtistCandidate,
)
from .network import proxy_url_for_source, urlopen_with_retry
from .parallel import run_ordered_parallel
from .text_utils import (
    _add_unique_value,
    _duration_close,
    _instrumental_marker_conflicts,
    _nested_get,
    _normalize_release_date,
    _parse_number,
    _release_date_matches,
    _same_identifier,
    _same_raw_text,
    _stringify_tag_value,
    _text_match_score,
    split_artists,
)

def load_spotify_credentials(
    env_path: Path | None = None,
    environ: dict[str, str] | None = None,
) -> SpotifyCredentials:
    env_path = env_path or Path(".env")
    environment = environ if environ is not None else os.environ

    client_id = load_config_value("SPOTIFY_CLIENT_ID", env_path=env_path, environ=environment)
    client_secret = load_config_value("SPOTIFY_CLIENT_SECRET", env_path=env_path, environ=environment)
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
        proxy_url: str | None = None,
    ):
        self.credentials = credentials
        self.timeout = timeout
        self.proxy_url = proxy_url if proxy_url is not None else proxy_url_for_source("spotify")
        self.markets = list(markets or DEFAULT_SPOTIFY_MARKETS)
        self._read_json = read_json or self._read_json_from_url
        self._access_token: str | None = None
        self._token_lock = threading.Lock()

    def search_tracks(self, metadata: AudioMetadata) -> list[SpotifyTrackCandidate]:
        if not metadata.title:
            return []
        access_token = self._get_access_token()
        errors: list[str] = []

        def search_market(market: str) -> tuple[list[SpotifyTrackCandidate], list[str]]:
            market_errors: list[str] = []
            market_candidates = self._search_market_tracks(metadata, access_token, market, 0, market_errors)
            if self._should_search_artist_albums(metadata, market_candidates):
                market_candidates.extend(
                    self._search_artist_album_tracks(
                        metadata,
                        access_token,
                        market,
                        len(market_candidates),
                    )
                )
            return _dedupe_spotify_candidates(market_candidates), market_errors

        market_results = run_ordered_parallel(
            self.markets,
            search_market,
            max_workers=load_positive_int_config("TTML_SPOTIFY_MARKET_WORKERS", default=2),
        )

        candidates: list[SpotifyTrackCandidate] = []
        for market_candidates, market_errors in market_results:
            errors.extend(market_errors)
            candidates.extend(
                replace(candidate, source_index=len(candidates) + index)
                for index, candidate in enumerate(market_candidates)
            )
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
        with self._token_lock:
            if self._access_token:
                return self._access_token
            request = self._build_token_request()
            with urlopen_with_retry(request, timeout=self.timeout, proxy_url=self.proxy_url) as response:
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
        with urlopen_with_retry(request, timeout=self.timeout, proxy_url=self.proxy_url) as response:
            payload = json.loads(response.read().decode("utf-8", "ignore"))
        if not isinstance(payload, dict):
            raise ValueError("Spotify API returned a non-object payload")
        return payload


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

    use_color = (print_func is _safe_print) and (input_func is input)

    print_func("")
    header_text = "Spotify 最佳候选："
    if use_color:
        header_text = _color_text(header_text, "header")
    print_func(header_text)

    for pair in available:
        best = _spotify_market_best_candidates(pair.spotify_metadata, pair.metadata)
        if not best:
            cand_str = _color_text("-", "unchanged") if use_color else "-"
            print_func(f"  {pair.ttml_path.name}: {cand_str}")
        else:
            print_func(f"  {pair.ttml_path.name}:")
            for candidate in best:
                cand_str = _format_spotify_candidate(candidate)
                if use_color:
                    cand_str = _color_text(cand_str, "highlight")
                print_func(f"    - {cand_str}")

    while True:
        prompt_text = "Accept all Spotify best candidates? Type Y to accept, N to choose alternatives: "
        if use_color:
            prompt_text = _color_text(prompt_text, "prompt")
        answer = input_func(prompt_text).strip()
        if answer.casefold() in {"y", "n"}:
            break
        print_func("Please type Y or N.")

    if answer.casefold() == "y":
        return

    for pair in available:
        selected: list[SpotifyTrackCandidate] = []
        print_func("")
        cand_title = f"{pair.ttml_path.name} Spotify 候选："
        if use_color:
            cand_title = _color_text(cand_title, "info")
        print_func(cand_title)

        market_groups = pair.spotify_metadata.candidates_by_market or _spotify_candidates_grouped_by_market(
            pair.spotify_metadata.candidates
        )
        for market in _spotify_market_order_from_mapping(market_groups):
            options = market_groups.get(market, [])[:5]
            if not options:
                continue
            market_title = f"  {market} Spotify 候选："
            if use_color:
                market_title = _color_text(market_title, "info")
            print_func(market_title)

            for index, candidate in enumerate(options, start=1):
                idx_str = f"    {index}."
                if use_color:
                    idx_str = _color_text(idx_str, "info")
                print_func(f"{idx_str} {_format_spotify_candidate(candidate)}")
            while True:
                sel_prompt = f"Select {market} 1-5, or press Enter to skip this market: "
                if use_color:
                    sel_prompt = _color_text(sel_prompt, "prompt")
                answer = input_func(sel_prompt).strip()
                if not answer:
                    break
                if answer.isdigit() and 1 <= int(answer) <= len(options):
                    selected.append(options[int(answer) - 1])
                    break
                print_func("Invalid selection.")
        pair.spotify_metadata.selected = selected


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


def _spotify_candidate_score(metadata: AudioMetadata, candidate: SpotifyTrackCandidate) -> int:
    score = 0
    if _same_identifier(metadata.isrc, candidate.isrc):
        score += 1000
    score += _text_match_score(metadata.title, candidate.title) * 100
    for artist in metadata.artists:
        score += max((_text_match_score(artist, candidate_artist) for candidate_artist in candidate.artists), default=0) * 80
    score += _text_match_score(metadata.album, candidate.album) * 40
    return score


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


def _format_spotify_candidate(candidate: SpotifyTrackCandidate) -> str:
    title = candidate.title or "-"
    artists = "/".join(candidate.artists) or "-"
    album = candidate.album or "-"
    market = candidate.market or "-"
    return f"{market}: {title} - {artists} - {album} [{candidate.track_id}]"


def _format_spotify_candidate_list(candidates: Iterable[SpotifyTrackCandidate]) -> str:
    return ", ".join(_format_spotify_candidate(candidate) for candidate in candidates)
