"""Public entry point: plan(sql, ...) -> Plan."""

from __future__ import annotations

from collections.abc import Sequence

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from .eligibility import ineligibility_reason
from .ir import Plan, ineligible
from .pruning import prune_files
from .rewrite import split
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

    Files carrying ``stats`` (per-column min/max zone maps) are pruned
    against the WHERE clause before grouping; files without stats are
    always scanned.

    Worker count: explicit ``workers`` wins; otherwise it is recommended
    from file sizes when the source knows them (optionally bounded by
    ``worker_memory_bytes`` and ``max_workers``); otherwise one fragment
    per file, capped by ``max_workers``.

    Returns an ineligible Plan (never raises) for anything about the QUERY
    that prevents splitting; raises ValueError only for API misuse.
    """
    try:
        statement = sqlglot.parse_one(sql, read=dialect)
    except ParseError as e:
        return ineligible(f"parse error: {e}", query=sql)

    reason = ineligibility_reason(statement)
    if reason is not None:
        return ineligible(reason, query=sql)

    result = split(statement, dialect)
    if result.reason is not None:
        return ineligible(result.reason, query=sql)

    resolved = _resolve_groups(
        source,
        files,
        file_groups,
        workers,
        worker_memory_bytes,
        max_workers,
        statement.args.get("where"),
    )
    if isinstance(resolved, Plan):
        resolved.query = sql
        return resolved
    groups, warnings, pruned = resolved

    if statement.args.get("limit") and not statement.args.get("order"):
        warnings = warnings + [
            "LIMIT without ORDER BY selects arbitrary rows; the split may "
            "return a different (equally valid) subset than a single-node run"
        ]

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
        pruned_files=[f.path for f in pruned],
    )


def _resolve_groups(
    source: Source | None,
    files: Sequence[str | DataFile] | None,
    file_groups: Sequence[Sequence[str | DataFile]] | None,
    workers: int | None,
    worker_memory_bytes: int | None,
    max_workers: int | None,
    where: exp.Where | None,
) -> tuple[list[list[DataFile]], list[str], list[DataFile]] | Plan:
    given = [x is not None for x in (source, files, file_groups)]
    if sum(given) != 1:
        raise ValueError("pass exactly one of source=, files=, or file_groups=")

    if file_groups is not None:
        raw_groups = [
            [f if isinstance(f, DataFile) else DataFile(path=str(f)) for f in g]
            for g in file_groups
            if g
        ]
        if not raw_groups:
            return ineligible("source has no files")
        groups, pruned = [], []
        for g in raw_groups:
            kept_g, pruned_g = prune_files(g, where)
            pruned.extend(pruned_g)
            if kept_g:
                groups.append(kept_g)
        groups, pruned = _never_empty(groups, pruned)
        return groups, [], pruned

    src = source if source is not None else ParquetSource(files)
    blocked = src.blocking_reason()
    if blocked is not None:
        return ineligible(blocked)

    kept, pruned = prune_files(src.files, where)
    groups_of_one = [[f] for f in kept]
    groups_of_one, pruned = _never_empty(groups_of_one, pruned)
    kept = [f for g in groups_of_one for f in g]

    if workers is None:
        if all(f.size_bytes is not None for f in kept):
            workers = recommend_workers(
                kept,
                worker_memory_bytes=worker_memory_bytes,
                max_workers=max_workers,
            )
        else:
            workers = len(kept) if max_workers is None else max_workers
    return group_files(kept, workers), src.warnings(), pruned


def _never_empty(
    groups: list[list[DataFile]], pruned: list[DataFile]
) -> tuple[list[list[DataFile]], list[DataFile]]:
    """Pruning everything must still leave one fragment: global aggregates
    need a row source to produce their zero-rows answer (COUNT() = 0), and
    scanning a non-matching file is always correct — its rows just fail
    the WHERE."""
    if groups:
        return groups, pruned
    sized = [f for f in pruned if f.size_bytes is not None]
    keep = min(sized, key=lambda f: f.size_bytes) if sized else pruned[0]
    return [[keep]], [f for f in pruned if f is not keep]


def _scan_node(group: list[DataFile], alias: str, dialect: str) -> exp.Expression:
    """Build ``read_parquet(['f1', ...]) AS alias`` by parsing a snippet, so
    no assumptions are made about sqlglot's node classes for table functions."""
    paths = ", ".join(
        exp.Literal.string(f.path).sql(dialect=dialect) for f in group
    )
    alias_sql = exp.to_identifier(alias).sql(dialect=dialect)
    snippet = f"SELECT * FROM read_parquet([{paths}]) AS {alias_sql}"
    return sqlglot.parse_one(snippet, read=dialect).args["from_"].this
