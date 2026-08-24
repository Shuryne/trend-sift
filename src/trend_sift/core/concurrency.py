"""Small concurrency helpers for bounded, order-preserving I/O work."""

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


def map_concurrently(
    fn: Callable[[T], R],
    items: Iterable[T],
    *,
    max_workers: int,
) -> list[R]:
    """Apply ``fn`` concurrently while preserving input order in the result."""
    if max_workers < 1:
        raise ValueError("max_workers 必须至少为 1")

    work = items if isinstance(items, list) else list(items)
    if not work:
        return []
    if max_workers == 1:
        return [fn(item) for item in work]

    with ThreadPoolExecutor(max_workers=min(max_workers, len(work))) as executor:
        return list(executor.map(fn, work))
