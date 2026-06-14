from __future__ import annotations

import concurrent.futures
import threading
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import Any, TypeVar

from .apple_music import collect_apple_music_metadata
from .audio import read_audio_metadata
from .models import (
    AppleMusicClientProtocol,
    AppleMusicTrackCandidate,
    AudioMetadata,
    NCMusicCandidate,
    NCMusicClientProtocol,
    NCMusicSearchContext,
    NCMusicSearchResult,
    PairMetadata,
    QQMusicCandidate,
    QQMusicClientProtocol,
    SpotifyClientProtocol,
    SpotifyTrackCandidate,
    WorkItem,
)
from .ncm_music import collect_ncm_music_metadata
from .qq_music import collect_qq_music_metadata
from .spotify import collect_spotify_metadata
from .ttml import read_ttml_metadata

T = TypeVar("T")


@dataclass(frozen=True)
class SearchClients:
    apple_music: AppleMusicClientProtocol
    qq_music: QQMusicClientProtocol
    ncm_music: NCMusicClientProtocol
    spotify: SpotifyClientProtocol | None = None


class BatchSearchCache:
    def __init__(self):
        self._lock = threading.RLock()
        self._values: dict[tuple[str, Hashable], Any] = {}
        self._inflight: dict[tuple[str, Hashable], threading.Event] = {}

    def get_or_compute(self, namespace: str, key: Hashable, compute: Callable[[], T]) -> T:
        cache_key = (namespace, key)
        with self._lock:
            if cache_key in self._values:
                return _copy_cached_value(self._values[cache_key])
            event = self._inflight.get(cache_key)
            if event is None:
                event = threading.Event()
                self._inflight[cache_key] = event
                owner = True
            else:
                owner = False

        if owner:
            try:
                value = compute()
                with self._lock:
                    self._values[cache_key] = _copy_cached_value(value)
                return value
            finally:
                with self._lock:
                    self._inflight.pop(cache_key, None)
                    event.set()

        event.wait()
        with self._lock:
            if cache_key in self._values:
                return _copy_cached_value(self._values[cache_key])
        return self.get_or_compute(namespace, key, compute)


def prepare_work_items(
    work_items: list[WorkItem],
    clients: SearchClients,
    *,
    max_workers: int = 3,
    cache: BatchSearchCache | None = None,
) -> tuple[list[PairMetadata], list[tuple[WorkItem, Exception]]]:
    if not work_items:
        return [], []

    cached_clients = clients_with_cache(clients, cache or BatchSearchCache())
    worker_count = max(1, min(max_workers, len(work_items)))
    prepared: list[PairMetadata | None] = [None] * len(work_items)
    failures: list[tuple[WorkItem, Exception]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(_prepare_work_item, work_item, cached_clients): index
            for index, work_item in enumerate(work_items)
        }
        for future in concurrent.futures.as_completed(futures):
            index = futures[future]
            try:
                prepared[index] = future.result()
            except Exception as exc:
                failures.append((work_items[index], exc))

    return [pair for pair in prepared if pair is not None], failures


def collect_ncm_for_pairs(
    pairs: list[PairMetadata],
    ncm_music_client: NCMusicClientProtocol,
    *,
    max_workers: int = 3,
    cache: BatchSearchCache | None = None,
) -> None:
    if not pairs:
        return

    cached_client = CachedNCMusicClient(ncm_music_client, cache or BatchSearchCache())
    worker_count = max(1, min(max_workers, len(pairs)))

    def collect(pair: PairMetadata) -> NCMusicSearchResult:
        result = collect_ncm_music_metadata(
            pair.metadata,
            cached_client,
            qq_music_candidate=pair.qq_music_metadata.selected,
        )
        if result.candidates and result.selected is None:
            result.selected = result.candidates[0]
        return result

    if worker_count <= 1:
        for pair in pairs:
            pair.ncm_music_metadata = collect(pair)
        return

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(collect, pair): pair for pair in pairs}
        for future in concurrent.futures.as_completed(futures):
            futures[future].ncm_music_metadata = future.result()


def clients_with_cache(clients: SearchClients, cache: BatchSearchCache) -> SearchClients:
    return SearchClients(
        apple_music=CachedAppleMusicClient(clients.apple_music, cache),
        qq_music=CachedQQMusicClient(clients.qq_music, cache),
        ncm_music=CachedNCMusicClient(clients.ncm_music, cache),
        spotify=CachedSpotifyClient(clients.spotify, cache) if clients.spotify is not None else None,
    )


def prepare_one_work_item(work_item: WorkItem, clients: SearchClients) -> PairMetadata:
    return _prepare_work_item(work_item, clients)


def _prepare_work_item(work_item: WorkItem, clients: SearchClients) -> PairMetadata:
    if work_item.audio_path:
        metadata = read_audio_metadata(work_item.audio_path)
    else:
        metadata = read_ttml_metadata(work_item.ttml_path)
        if not metadata.title:
            raise ValueError("TTML 中未读取到歌名，跳过 QQ 音乐搜索、网易云音乐搜索和 Spotify 搜索")

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        apple_future = executor.submit(collect_apple_music_metadata, metadata, clients.apple_music)
        qq_future = executor.submit(collect_qq_music_metadata, metadata, clients.qq_music)
        spotify_future = executor.submit(collect_spotify_metadata, metadata, clients.spotify)
        return PairMetadata(
            work_item.audio_path,
            work_item.ttml_path,
            metadata,
            apple_future.result(),
            qq_future.result(),
            NCMusicSearchResult(),
            spotify_future.result(),
        )


class CachedAppleMusicClient:
    def __init__(self, delegate: AppleMusicClientProtocol, cache: BatchSearchCache):
        self._delegate = delegate
        self._cache = cache

    def fetch_album_tracks(self, store: str, album_id: str) -> list[dict[str, Any]]:
        return self._cache.get_or_compute(
            "apple.fetch_album_tracks",
            (store, album_id),
            lambda: self._delegate.fetch_album_tracks(store, album_id),
        )

    def search_songs(self, store: str, metadata: AudioMetadata) -> list[AppleMusicTrackCandidate]:
        return self._cache.get_or_compute(
            "apple.search_songs",
            (store, _metadata_key(metadata)),
            lambda: self._delegate.search_songs(store, metadata),
        )

    def search_artists(self, store: str, query: str):
        return self._cache.get_or_compute(
            "apple.search_artists",
            (store, query),
            lambda: self._delegate.search_artists(store, query),
        )

    def fetch_artist_albums(self, store: str, artist_id: str):
        return self._cache.get_or_compute(
            "apple.fetch_artist_albums",
            (store, artist_id),
            lambda: self._delegate.fetch_artist_albums(store, artist_id),
        )


class CachedQQMusicClient:
    def __init__(self, delegate: QQMusicClientProtocol, cache: BatchSearchCache):
        self._delegate = delegate
        self._cache = cache

    def search_songs(self, query: str) -> list[QQMusicCandidate]:
        return self._cache.get_or_compute("qq.search_songs", query, lambda: self._delegate.search_songs(query))


class CachedNCMusicClient:
    def __init__(self, delegate: NCMusicClientProtocol, cache: BatchSearchCache):
        self._delegate = delegate
        self._cache = cache

    def search_songs(self, context: NCMusicSearchContext) -> list[NCMusicCandidate]:
        return self._cache.get_or_compute(
            "ncm.search_songs",
            (tuple(context.titles), tuple(context.artists), tuple(context.albums)),
            lambda: self._delegate.search_songs(context),
        )


class CachedSpotifyClient:
    def __init__(self, delegate: SpotifyClientProtocol, cache: BatchSearchCache):
        self._delegate = delegate
        self._cache = cache

    def search_tracks(self, metadata: AudioMetadata) -> list[SpotifyTrackCandidate]:
        return self._cache.get_or_compute(
            "spotify.search_tracks",
            _metadata_key(metadata),
            lambda: self._delegate.search_tracks(metadata),
        )


def _metadata_key(metadata: AudioMetadata) -> tuple[Any, ...]:
    return (
        metadata.title,
        tuple(metadata.artists),
        metadata.album,
        metadata.isrc,
        metadata.catalog_id,
        metadata.playlist_id,
        metadata.track_number,
        metadata.disc_number,
        metadata.duration_seconds,
        metadata.release_date,
    )


def _copy_cached_value(value: T) -> T:
    if isinstance(value, list):
        return list(value)  # type: ignore[return-value]
    if isinstance(value, dict):
        return dict(value)  # type: ignore[return-value]
    if isinstance(value, tuple):
        return tuple(_copy_cached_value(item) for item in value)  # type: ignore[return-value]
    return value
