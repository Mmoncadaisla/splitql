"""Public entry point: plan(sql, ...) -> Plan."""

from __future__ import annotations

from collections.abc import Sequence

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from .eligibility import ineligibility_reason
from .ir import Plan, ineligible
from .rewrite import PARTIALS_PLACEHOLDER, split
from .sizing import group_files, recommend_workers
from .sources import DataFile, DuckLakeSource, ParquetSource

Source = ParquetSource | DuckLakeSource


def plan(
    sql: str,
    *,
    source: Source | None = None,
    files: Sequence[str | DataFile] | None = None,
    file_groups: Sequence[Sequence[str | DataFile]] | None = None,
    workers: int | None = None,
    worker_memory_bytes: int | None = None,
    max_workers: int | None = None,
    partials_table: str = "partials",
    dialect: str = "duckdb",
) -> Plan:
    """Split ``sql`` into per-partition fragments plus a reduce query.

    Partitions come from exactly one of ``source`` (ParquetSource /
    DuckLakeSource), ``files`` (shorthand for ParquetSource), or
    ``file_groups`` (pre-grouped, one fragment per group).

    Worker count: explicit ``workers`` wins; otherwise it is recommended
    from file sizes when the source knows them (optionally bounded by
    ``worker_memory_bytes`` and ``max_workers``); otherwise one fragment
    per file, capped by ``max_workers``.

    Returns an ineligible Plan (never raises) for anything about the QUERY
    that prevents splitting; raises ValueError only for API misuse.
    """
    groups = _resolve_groups(source, files, file_groups, workers,
                             worker_memory_bytes, max_workers)
    if isinstance(groups, Plan):
        return groups
    groups, warnings = groups

    try:
        statement = sqlglot.parse_one(sql, read=dialect)
    except ParseError as e:
        return ineligible(f"parse error: {e}")

    reason = ineligibility_reason(statement)
    if reason is not None:
        return ineligible(reason)

    result = split(statement, dialect)
    if result.reason is not None:
        return ineligible(result.reason)

    table = statement.args["from_"].this
    alias = table.alias or table.name

    fragments = []
    for group in groups:
        fragment = result.fragment.copy()
        fragment.args["from_"].this.replace(_scan_node(group, alias, dialect))
        fragments.append(fragment.sql(dialect=dialect))

    reduce_ = result.reduce.copy()
    reduce_.args["from_"].this.replace(exp.to_table(partials_table, dialect=dialect))

    return Plan(
        eligible=True,
        fragments=fragments,
        reduce=reduce_.sql(dialect=dialect),
        partials_table=partials_table,
        warnings=warnings,
        query=sql,
        fragment_files=groups,
    )


def _resolve_groups(
    source: Source | None,
    files: Sequence[str | DataFile] | None,
    file_groups: Sequence[Sequence[str | DataFile]] | None,
    workers: int | None,
    worker_memory_bytes: int | None,
    max_workers: int | None,
) -> tuple[list[list[DataFile]], list[str]] | Plan:
    given = [x is not None for x in (source, files, file_groups)]
    if sum(given) != 1:
        raise ValueError("pass exactly one of source=, files=, or file_groups=")

    if file_groups is not None:
        groups = [
            [f if isinstance(f, DataFile) else DataFile(path=str(f)) for f in g]
            for g in file_groups
            if g
        ]
        if not groups:
            return ineligible("source has no files")
        return groups, []

    src = source if source is not None else ParquetSource(files)
    blocked = src.blocking_reason()
    if blocked is not None:
        return ineligible(blocked)

    if workers is None:
        if all(f.size_bytes is not None for f in src.files):
            workers = recommend_workers(
                src.files,
                worker_memory_bytes=worker_memory_bytes,
                max_workers=max_workers,
            )
        else:
            workers = len(src.files) if max_workers is None else max_workers
    return group_files(src.files, workers), src.warnings()


def _scan_node(group: list[DataFile], alias: str, dialect: str) -> exp.Expression:
    """Build ``read_parquet(['f1', ...]) AS alias`` by parsing a snippet, so
    no assumptions are made about sqlglot's node classes for table functions."""
    paths = ", ".join(
        exp.Literal.string(f.path).sql(dialect=dialect) for f in group
    )
    alias_sql = exp.to_identifier(alias).sql(dialect=dialect)
    snippet = f"SELECT * FROM read_parquet([{paths}]) AS {alias_sql}"
    return sqlglot.parse_one(snippet, read=dialect).args["from_"].this
