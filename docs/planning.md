# Fragments and pruning

## Fragment count

splitql plans **fragments** (chunks of work), not workers — how many
execute at once is the caller's business (sequentially, 10 threads, 100
Lambdas: same plan). Explicit `fragments=N` always wins. With file sizes
available (DuckLake always has them), splitql can recommend instead:

```python
plan(sql, source=src)                                # ceil(total / 512MB), capped by #files
plan(sql, source=src, worker_memory_bytes=8 * 2**30) # target = executing machine's memory / 4
plan(sql, source=src, max_fragments=16)
```

Grouping balances by size (LPT greedy) when sizes are known, round-robin
otherwise.

## Zone-map pruning

Files carrying per-column min/max stats are pruned against the WHERE clause
before grouping — fewer fragments, fewer workers, less I/O:

```python
from datetime import date

from splitql import plan, DataFile, ColumnStats

files = [
    DataFile("jan.parquet", 900_000_000,
             stats={"d": ColumnStats(date(2026, 1, 1), date(2026, 1, 31))}),
    DataFile("jun.parquet", 800_000_000,
             stats={"d": ColumnStats(date(2026, 6, 1), date(2026, 6, 30))}),
]
p = plan("SELECT count(*) FROM t WHERE d > DATE '2026-05-01'", files=files)
p.pruned_files   # ['jan.parquet']
```

Pruning is conservative in the safe direction: files without stats, columns
without stats, and any predicate shape it cannot prove (NOT, expressions,
non-literal comparisons) are kept — keeping a file is always correct, its
rows just fail the filter at scan time. Supported proofs: `=`, `!=`, `<`,
`<=`, `>`, `>=`, `BETWEEN`, `IN (literals)`, `IS NULL` (via `null_count`),
with `AND`/`OR` composition, over numbers, strings and dates. If everything
prunes, one fragment survives so global aggregates still return their
zero-rows answer (`COUNT` = 0).

## Getting stats

Without leaving DuckDB — per-file min/max from Parquet footers:

```sql
-- footer stats are VARCHAR: cast INSIDE the aggregates (to the column's
-- real type), or MIN/MAX order row groups lexicographically ('10' < '2')
-- and the wrong bounds make pruning silently drop matching files
SELECT file_name, path_in_schema AS column,
       MIN(CAST(stats_min_value AS DOUBLE)) AS min_value,
       MAX(CAST(stats_max_value AS DOUBLE)) AS max_value
FROM parquet_metadata(['s3://lake/sales/*.parquet'])
GROUP BY 1, 2
```

For DuckLake, the catalog keeps the same information in its
`ducklake_file_column_stats` table.
