"""Worker-count recommendation and size-balanced file grouping.

Pure arithmetic over caller-provided numbers — the library never inspects
the environment.
"""

from __future__ import annotations

import math

from .sources import DataFile

# Compressed Parquet expands roughly 3-5x once decoded; keeping a fragment's
# compressed input around a quarter of worker memory leaves DuckDB headroom
# for the aggregation state on top of the scan.
DEFAULT_TARGET_FRAGMENT_BYTES = 512 * 1024 * 1024
MEMORY_TO_TARGET_DIVISOR = 4


def recommend_fragments(
    files: list[DataFile],
    *,
    worker_memory_bytes: int | None = None,
    target_fragment_bytes: int | None = None,
    max_fragments: int | None = None,
) -> int:
    """How many fragments this file set wants.

    ``ceil(total_bytes / target)``, clamped to [1, number of files] (file
    granularity is the floor) and to ``max_fragments``. The target is either
    explicit or derived from worker memory (memory / 4).
    Requires every file size to be known.
    """
    if any(f.size_bytes is None for f in files):
        raise ValueError(
            "recommend_fragments needs size_bytes on every file; "
            "pass fragments explicitly instead"
        )
    total = sum(f.size_bytes for f in files)
    target = target_fragment_bytes
    if target is None and worker_memory_bytes is not None:
        target = max(worker_memory_bytes // MEMORY_TO_TARGET_DIVISOR, 1)
    if target is None:
        target = DEFAULT_TARGET_FRAGMENT_BYTES
    n = max(1, math.ceil(total / target))
    n = min(n, len(files))
    if max_fragments is not None:
        n = min(n, max_fragments)
    return n


def group_files(files: list[DataFile], fragments: int) -> list[list[DataFile]]:
    """Split files into at most ``fragments`` groups. With sizes known, use
    LPT greedy balancing (largest file to the currently lightest group);
    otherwise round-robin. Empty groups are dropped."""
    fragments = max(1, min(fragments, len(files)))
    groups: list[list[DataFile]] = [[] for _ in range(fragments)]
    if all(f.size_bytes is not None for f in files):
        loads = [0] * fragments
        for f in sorted(files, key=lambda f: f.size_bytes, reverse=True):
            i = loads.index(min(loads))
            groups[i].append(f)
            loads[i] += f.size_bytes
    else:
        for i, f in enumerate(files):
            groups[i % fragments].append(f)
    return [g for g in groups if g]
