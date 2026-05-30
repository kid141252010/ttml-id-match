from __future__ import annotations

import concurrent.futures
import json
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterable

from .console import _safe_print
from .models import (
    DEFAULT_NCM_API_BASES,
    NCM_ARTIST_ALBUM_LIMIT,
    NCM_ARTIST_SEARCH_LIMIT,
    NCM_SEARCH_LIMIT,
    AudioMetadata,
    NCMusicCandidate,
    NCMusicClientProtocol,
    NCMusicSearchContext,
    NCMusicSearchResult,
    PairMetadata,
    QQMusicCandidate,
    _NCMusicAlbumCandidate,
    _NCMusicArtistCandidate,
)
from .network import urlopen_with_retry
from .text_utils import (
    _add_text_with_simplified_variants,
    _add_unique_value,
    _nested_get,
    _same_raw_text,
    _stringify_tag_value,
    _text_match_score,
    _text_with_simplified_variants,
    split_artists,
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
        with urlopen_with_retry(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "ignore"))
        if not isinstance(payload, dict):
            raise ValueError("NCM API returned a non-object payload")
        return payload


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


def _ncm_music_candidate_score(context: NCMusicSearchContext, candidate: NCMusicCandidate) -> int:
    title_score = _ncm_candidate_title_score(context, candidate)
    score = title_score * 100
    for artist in context.artists:
        score += max((_text_match_score(artist, candidate_artist) for candidate_artist in candidate.artists), default=0) * 60
    score += max((_text_match_score(album, candidate.album) for album in context.albums), default=0) * 30
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


def _ncm_artist_matches(context: NCMusicSearchContext, artist: _NCMusicArtistCandidate) -> bool:
    candidate_names = [artist.name, *artist.aliases]
    return any(_text_match_score(expected, actual) > 0 for expected in context.artists for actual in candidate_names)


def _ncm_album_matches(context: NCMusicSearchContext, album: _NCMusicAlbumCandidate) -> bool:
    return any(_text_match_score(expected, album.name) > 0 for expected in context.albums)


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


def _format_ncm_music_candidate(candidate: NCMusicCandidate) -> str:
    title = candidate.title or "-"
    aliases = f" ({'; '.join(candidate.aliases)})" if candidate.aliases else ""
    artists = "/".join(candidate.artists) or "-"
    album = candidate.album or "-"
    return f"{title}{aliases} - {artists} - {album} [{candidate.song_id}]"
