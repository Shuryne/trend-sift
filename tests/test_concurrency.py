import threading

import pytest

from trend_sift.core.concurrency import map_concurrently


def test_map_concurrently_bounds_workers_and_preserves_order() -> None:
    lock = threading.Lock()
    active = 0
    peak = 0
    first_batch_ready = threading.Barrier(3)

    def work(item: int) -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        if item < first_batch_ready.parties:
            first_batch_ready.wait(timeout=1)
        with lock:
            active -= 1
        return item * 2

    result = map_concurrently(work, range(8), max_workers=3)

    assert result == [item * 2 for item in range(8)]
    assert peak == 3


def test_map_concurrently_serial_fallback() -> None:
    caller_thread = threading.get_ident()
    worker_threads: list[int] = []

    def work(item: int) -> int:
        worker_threads.append(threading.get_ident())
        return item

    assert map_concurrently(work, [1, 2], max_workers=1) == [1, 2]
    assert worker_threads == [caller_thread, caller_thread]


def test_map_concurrently_handles_empty_input() -> None:
    assert map_concurrently(str, [], max_workers=5) == []


def test_map_concurrently_rejects_zero_workers() -> None:
    with pytest.raises(ValueError, match="至少为 1"):
        map_concurrently(str, [1], max_workers=0)
