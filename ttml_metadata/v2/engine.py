from __future__ import annotations

import concurrent.futures
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import fields, is_dataclass
from typing import Any

from ttml_metadata.models import AudioMetadata
from ttml_metadata.text_utils import _add_unique_value, split_artists

from .domain import MatchContext, MatchResult, Selection, SourceAdapter, SourceResult
from .registry import SourceRegistry


class UnknownCandidateError(ValueError):
    pass


class MatchingEngine:
    def __init__(
        self,
        adapters: Iterable[SourceAdapter] | SourceRegistry,
        *,
        max_workers: int = 3,
        source_limits: dict[str, int] | None = None,
        cache_size: int = 256,
        cache_ttl_seconds: float = 300.0,
    ):
        self._registry = adapters if isinstance(adapters, SourceRegistry) else SourceRegistry(adapters)
        self._adapters = self._registry.adapters
        self._by_key = self._registry.by_key
        self._max_workers = max(1, max_workers)
        self._global_budget = threading.BoundedSemaphore(self._max_workers)
        configured_limits = source_limits or {}
        unknown_limits = set(configured_limits) - set(self._by_key)
        if unknown_limits:
            raise ValueError(f"source limits reference unknown sources: {sorted(unknown_limits)}")
        self._source_semaphores = {
            adapter.key: threading.BoundedSemaphore(
                _positive_limit(configured_limits.get(adapter.key, self._max_workers), adapter.key)
            )
            for adapter in self._adapters
        }
        self._cache_size = max(0, cache_size)
        self._cache_ttl_seconds = max(0.0, cache_ttl_seconds)
        self._cache: OrderedDict[tuple[Any, ...], tuple[float, SourceResult]] = OrderedDict()
        self._cache_lock = threading.RLock()

    def match(self, metadata: AudioMetadata) -> MatchResult:
        return self.match_many((metadata,))[0]

    def match_many(self, metadata_items: Iterable[AudioMetadata]) -> list[MatchResult]:
        metadata_list = list(metadata_items)
        states = [
            {"completed": {}, "remaining": list(self._adapters)}
            for _metadata in metadata_list
        ]
        if not states:
            return []

        with concurrent.futures.ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            while any(state["remaining"] for state in states):
                task_groups: OrderedDict[
                    tuple[Any, ...],
                    list[tuple[int, SourceAdapter, dict[str, SourceResult]]],
                ] = OrderedDict()
                made_progress = False

                for index, (metadata, state) in enumerate(zip(metadata_list, states)):
                    completed = state["completed"]
                    remaining = state["remaining"]
                    ready = [
                        adapter
                        for adapter in remaining
                        if set(adapter.dependencies) <= completed.keys()
                    ]
                    for adapter in ready:
                        cache_key = self._cache_key(adapter, metadata, completed)
                        cached = self._get_cached(cache_key)
                        if cached is not None:
                            completed[adapter.key] = cached
                            remaining.remove(adapter)
                            made_progress = True
                            continue
                        task_groups.setdefault(cache_key, []).append(
                            (
                                index,
                                adapter,
                                {
                                    configured.key: completed[configured.key]
                                    for configured in self._adapters
                                    if configured.key in completed
                                },
                            )
                        )

                futures = {
                    executor.submit(
                        self._search_limited,
                        group[0][1],
                        metadata_list[group[0][0]],
                        group[0][2],
                    ): (cache_key, group)
                    for cache_key, group in task_groups.items()
                }
                for future in concurrent.futures.as_completed(futures):
                    cache_key, group = futures[future]
                    result = future.result()
                    if not result.warnings:
                        self._put_cached(cache_key, result)
                    for index, adapter, _context in group:
                        states[index]["completed"][adapter.key] = result
                        states[index]["remaining"].remove(adapter)
                    made_progress = True

                if not made_progress:
                    unresolved = [
                        adapter.key
                        for state in states
                        for adapter in state["remaining"]
                    ]
                    raise ValueError(f"source dependency cycle: {', '.join(unresolved)}")

        return [
            MatchResult(
                metadata=metadata,
                sources={
                    adapter.key: state["completed"][adapter.key]
                    for adapter in self._adapters
                },
            )
            for metadata, state in zip(metadata_list, states)
        ]

    def default_selection(self, pair_id: str, result: MatchResult) -> Selection:
        return Selection(
            pair_id=pair_id,
            sources={key: source.recommended_ids for key, source in result.sources.items()},
        )

    def metadata_values(self, result: MatchResult, selection: Selection) -> dict[str, list[str]]:
        values: dict[str, list[str]] = {}
        metadata = result.metadata
        if metadata.title:
            _add_unique_value(values, "musicName", metadata.title)
        for artist in split_artists(metadata.artists):
            _add_unique_value(values, "artists", artist)
        if metadata.album:
            _add_unique_value(values, "album", metadata.album)

        for source_key, selected_ids in selection.sources.items():
            source_result = result.sources.get(source_key)
            adapter = self._by_key.get(source_key)
            if source_result is None or adapter is None:
                raise UnknownCandidateError(f"unknown source: {source_key}")
            known_ids = {candidate.id for candidate in source_result.candidates}
            unknown = set(selected_ids) - known_ids
            if unknown:
                raise UnknownCandidateError(
                    f"unknown candidates for {source_key}: {', '.join(sorted(unknown))}"
                )
            contribution = adapter.metadata_values(metadata, source_result, selected_ids)
            for key, proposed_values in contribution.items():
                for value in proposed_values:
                    _add_unique_value(values, key, value)

        if metadata.isrc:
            _add_unique_value(values, "isrc", metadata.isrc)
        return values

    @staticmethod
    def _search(
        adapter: SourceAdapter,
        metadata: AudioMetadata,
        completed: dict[str, SourceResult],
    ) -> SourceResult:
        try:
            result = adapter.search(MatchContext(metadata=metadata, results=completed))
        except Exception as exc:
            return SourceResult(source=adapter.key, warnings=(str(exc),))
        if result.source != adapter.key:
            raise ValueError(f"source adapter {adapter.key} returned result for {result.source}")
        _validate_source_result(adapter.key, result)
        return result

    def _search_limited(
        self,
        adapter: SourceAdapter,
        metadata: AudioMetadata,
        completed: dict[str, SourceResult],
    ) -> SourceResult:
        source_budget = self._source_semaphores[adapter.key]
        with source_budget:
            with self._global_budget:
                return self._search(adapter, metadata, completed)

    def _cache_key(
        self,
        adapter: SourceAdapter,
        metadata: AudioMetadata,
        completed: dict[str, SourceResult],
    ) -> tuple[Any, ...]:
        dependency_signature = tuple(
            (dependency, _freeze_value(completed[dependency]))
            for dependency in sorted(adapter.dependencies)
        )
        return (
            adapter.key,
            *(_freeze_value(getattr(metadata, field.name)) for field in fields(metadata)),
            dependency_signature,
        )

    def _get_cached(self, key: tuple[Any, ...]) -> SourceResult | None:
        if not self._cache_size:
            return None
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is None:
                return None
            created_at, result = cached
            if now - created_at > self._cache_ttl_seconds:
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return result

    def _put_cached(self, key: tuple[Any, ...], result: SourceResult) -> None:
        if not self._cache_size:
            return
        with self._cache_lock:
            self._cache[key] = (time.monotonic(), result)
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)

def _positive_limit(value: int, source: str) -> int:
    if value < 1:
        raise ValueError(f"source limit for {source} must be at least 1")
    return value


def _freeze_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return tuple(
            (field.name, _freeze_value(getattr(value, field.name)))
            for field in fields(value)
        )
    if isinstance(value, dict):
        return tuple(sorted((str(key), _freeze_value(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_freeze_value(item) for item in value)
    return value


def _validate_source_result(source: str, result: SourceResult) -> None:
    candidate_ids = [candidate.id for candidate in result.candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError(f"source adapter {source} returned duplicate candidate ids")
    for candidate in result.candidates:
        if candidate.source != source:
            raise ValueError(
                f"source adapter {source} returned candidate for {candidate.source}"
            )
        if candidate.rank < 1:
            raise ValueError(f"source adapter {source} returned an invalid rank")
    known_ids = set(candidate_ids)
    referenced_ids = set(result.recommended_ids)
    for group_ids in result.groups.values():
        referenced_ids.update(group_ids)
    unknown = referenced_ids - known_ids
    if unknown:
        raise ValueError(
            f"source adapter {source} referenced unknown candidates: {sorted(unknown)}"
        )
