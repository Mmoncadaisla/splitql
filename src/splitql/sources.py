"""Partition sources: where the file list (and its correctness caveats) come from.

The library never opens a connection — sources are plain data the caller
fills in. For DuckLake, the caller runs ``ducklake_list_files`` against an
attached lake and feeds the rows here; the source then knows enough to
refuse splits that raw ``read_parquet`` scans would answer incorrectly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnStats:
    """Per-file min/max for one column (zone map). Values must be Python
    comparables matching the column type (numbers, strings, date/datetime)."""

    min_value: object = None
    max_value: object = None
    null_count: int | None = None


@dataclass(frozen=True)
class DataFile:
    path: str
    size_bytes: int | None = None
    stats: dict[str, ColumnStats] | None = None
    row_count: int | None = None


class ParquetSource:
    """A plain list of Parquet files (sizes optional, used for balancing
    and worker recommendation)."""

    def __init__(self, files: Sequence[str | DataFile]):
        self.files = [
            f if isinstance(f, DataFile) else DataFile(path=str(f)) for f in files
        ]

    def blocking_reason(self) -> str | None:
        if not self.files:
            return "source has no files"
        return None

    def warnings(self) -> list[str]:
        return []


class DuckLakeSource:
    """Files of a DuckLake table, as reported by ``ducklake_list_files``.

    Feed it the rows of::

        FROM ducklake_list_files('<catalog>', '<table>')

    Splitting is refused when any file has a merge-on-read delete file —
    a raw ``read_parquet`` scan would resurrect deleted rows. Data
    inlining (small writes stored as catalog rows, on by default in
    DuckLake) is invisible to the file list: rows still inlined would be
    silently missed, so the gate is fail-closed — splitting requires an
    explicit ``has_inlined_data=False`` assertion (check via
    ``DATA_INLINING_ROW_LIMIT 0`` on the writer, or after
    ``ducklake_flush_inlined_data``). ``True`` and ``None`` (unknown)
    both refuse the split.
    """

    def __init__(
        self,
        files: Sequence[str | DataFile],
        *,
        has_delete_files: bool = False,
        has_inlined_data: bool | None = None,
    ):
        self.files = [
            f if isinstance(f, DataFile) else DataFile(path=str(f)) for f in files
        ]
        self.has_delete_files = has_delete_files
        self.has_inlined_data = has_inlined_data

    @classmethod
    def from_list_files(
        cls, rows: Sequence, *, has_inlined_data: bool | None = None
    ) -> "DuckLakeSource":
        """Build from ``ducklake_list_files`` rows: mappings (column name ->
        value) or positional sequences (data_file, data_file_size_bytes,
        ..., delete_file at index 4 — the documented column order)."""
        files: list[DataFile] = []
        has_deletes = False
        for row in rows:
            if isinstance(row, Mapping):
                path = row["data_file"]
                size = row.get("data_file_size_bytes")
                delete = row.get("delete_file")
            else:
                path = row[0]
                size = row[1] if len(row) > 1 else None
                delete = row[4] if len(row) > 4 else None
            files.append(DataFile(path=str(path), size_bytes=size))
            if delete is not None:
                has_deletes = True
        return cls(
            files, has_delete_files=has_deletes, has_inlined_data=has_inlined_data
        )

    def blocking_reason(self) -> str | None:
        if not self.files:
            return "source has no files"
        if self.has_delete_files:
            return (
                "table has merge-on-read delete files; a raw parquet scan "
                "would resurrect deleted rows"
            )
        if self.has_inlined_data:
            return (
                "table has inlined data in the catalog; a raw parquet scan "
                "would miss those rows"
            )
        if self.has_inlined_data is None:
            return (
                "inlined data status unknown; rows still inlined in the "
                "DuckLake catalog would be silently missed — pass "
                "has_inlined_data=False after checking (DATA_INLINING_ROW_LIMIT 0 "
                "or ducklake_flush_inlined_data)"
            )
        return None

    def warnings(self) -> list[str]:
        return []
