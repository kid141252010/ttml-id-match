from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from types import MappingProxyType

from .domain import SourceAdapter, SourceKey


class SourceRegistry:
    """Validated, stable registry for source adapters and their dependency DAG."""

    def __init__(self, adapters: Iterable[SourceAdapter]):
        self._adapters = tuple(adapters)
        keys = [adapter.key for adapter in self._adapters]
        if len(keys) != len(set(keys)):
            raise ValueError("source adapter keys must be unique")
        by_key = {adapter.key: adapter for adapter in self._adapters}
        known = set(by_key)
        for adapter in self._adapters:
            missing = set(adapter.dependencies) - known
            if missing:
                raise ValueError(
                    f"source {adapter.key} has unknown dependencies: {sorted(missing)}"
                )
        self._by_key: Mapping[SourceKey, SourceAdapter] = MappingProxyType(by_key)
        self._dependency_waves = self._build_dependency_waves()

    @property
    def adapters(self) -> tuple[SourceAdapter, ...]:
        return self._adapters

    @property
    def by_key(self) -> Mapping[SourceKey, SourceAdapter]:
        return self._by_key

    @property
    def dependency_waves(self) -> tuple[tuple[SourceAdapter, ...], ...]:
        return self._dependency_waves

    def __getitem__(self, key: SourceKey) -> SourceAdapter:
        return self._by_key[key]

    def __iter__(self) -> Iterator[SourceAdapter]:
        return iter(self._adapters)

    def __len__(self) -> int:
        return len(self._adapters)

    def _build_dependency_waves(self) -> tuple[tuple[SourceAdapter, ...], ...]:
        completed: set[SourceKey] = set()
        remaining = list(self._adapters)
        waves: list[tuple[SourceAdapter, ...]] = []
        while remaining:
            ready = tuple(
                adapter
                for adapter in remaining
                if set(adapter.dependencies) <= completed
            )
            if not ready:
                raise ValueError(
                    "source dependency cycle: "
                    + ", ".join(adapter.key for adapter in remaining)
                )
            waves.append(ready)
            completed.update(adapter.key for adapter in ready)
            remaining = [adapter for adapter in remaining if adapter not in ready]
        return tuple(waves)
