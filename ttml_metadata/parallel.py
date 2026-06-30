from __future__ import annotations

import concurrent.futures
from collections.abc import Callable, Sequence
from typing import TypeVar, cast

T = TypeVar("T")
R = TypeVar("R")
_MISSING = object()


def run_ordered_parallel(
    items: Sequence[T],
    worker: Callable[[T], R],
    *,
    max_workers: int,
) -> list[R]:
    if max_workers < 1:
        raise ValueError("max_workers must be at least 1")
    if not items:
        return []

    worker_count = min(max_workers, len(items))
    if worker_count <= 1:
        return [worker(item) for item in items]

    results: list[object] = [_MISSING] * len(items)
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(worker, item): index
            for index, item in enumerate(items)
        }
        for future in concurrent.futures.as_completed(futures):
            results[futures[future]] = future.result()

    return cast(list[R], results)
