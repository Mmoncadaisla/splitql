# splitql

**Split one SQL query into N per-partition fragments plus a reduce query. Pure planning, no runtime.**

Every distributed SQL engine contains this piece — the coordinator that turns
one query plus partition metadata into per-worker queries and a merge step —
but always welded to that engine's runtime. splitql extracts it as a pure
function: you bring the execution (threads, Ray, Lambdas, Kubernetes jobs,
ssh), it brings the planning.

```
input:   SQL + parquet files (or a DuckLake table)
output:  { eligible, fragments: [sql, ...], reduce: sql }
```

Run each fragment anywhere, concatenate their results into a relation, run
`reduce` over it. The final result is **identical to single-node execution**
— that is the contract, and the whole test suite is a comparison against a
single-node DuckDB oracle (including property-based random queries).

## Install

```bash
pip install splitql   # depends only on sqlglot
```

## Quickstart

```python
from splitql import plan

p = plan(
    "SELECT region, sum(amount) AS total FROM sales "
    "WHERE d >= DATE '2026-01-01' GROUP BY region",
    files=["s3://lake/sales/a.parquet", "s3://lake/sales/b.parquet",
           "s3://lake/sales/c.parquet", "s3://lake/sales/d.parquet"],
    workers=2,
)

p.fragments
# ['SELECT region AS g0, SUM(amount) AS a0 FROM READ_PARQUET([...a..., ...b...]) AS sales WHERE ... GROUP BY region',
#  'SELECT region AS g0, SUM(amount) AS a0 FROM READ_PARQUET([...c..., ...d...]) AS sales WHERE ... GROUP BY region']
p.reduce
# 'SELECT g0 AS "region", SUM(a0) AS "total" FROM partials GROUP BY g0'
```

Execution is yours. The minimal in-process runner:

```python
import duckdb, pyarrow as pa

partials = pa.concat_tables(
    [duckdb.connect().execute(f).to_arrow_table() for f in p.fragments]
)
con = duckdb.connect()
con.register(p.partials_table, partials)
result = con.execute(p.reduce).fetchall()
```

Anything about the query that prevents a correct split returns
`eligible=False` with a reason — never an exception, never a wrong split.
Run the original query single-node in that case:

```python
p = plan("SELECT a FROM t JOIN u ON ...", files=[...])
p.eligible   # False
p.reason     # 'joins are not supported'
```

## How aggregates are split

Standard MPP two-phase algebra, applied by rewriting the sqlglot AST:

| original | fragment (partial) | reduce (final) |
|---|---|---|
| `SUM(x)` | `SUM(x) AS a0` | `SUM(a0)` |
| `COUNT(x)` / `COUNT(*)` | `COUNT(...) AS a0` | `SUM(a0)` |
| `MIN(x)` / `MAX(x)` | `MIN/MAX(x) AS a0` | `MIN/MAX(a0)` |
| `AVG(x)` | `SUM(x) AS a0_s, COUNT(x) AS a0_c` | `SUM(a0_s) / SUM(a0_c)` |

GROUP BY keys travel as generated columns (`g0..gk`), aggregates as
(`a0..an`), so the partials relation never collides with user columns.
Expressions around aggregates (`max(x) - min(x)`, `avg(x) * 2 + 1`) are
rebuilt in the reduce. `ORDER BY` + `LIMIT` on plain scans becomes
per-fragment top-k plus a global top-k.

## What is (and isn't) eligible

Eligibility is a **whitelist**: single-table scans, filters, projections,
DISTINCT, GROUP BY (including expressions and aliases), the five aggregates
above, ORDER BY output columns/positions, LIMIT.

Deliberately rejected in v0.1 (returned as `reason`, never guessed at):
joins, subqueries, CTEs, window functions, HAVING, QUALIFY, OFFSET,
`COUNT(DISTINCT ...)`, `FILTER (WHERE ...)`, positional GROUP BY, and any
aggregate outside the whitelist. A conservative `False` is always available
as the single-node fallback, so splitql can never make your results wrong —
only your fast path narrower.

## Partition sources

### Plain Parquet

```python
from splitql import plan, ParquetSource, DataFile

plan(sql, files=["a.parquet", "b.parquet"])                  # shorthand
plan(sql, source=ParquetSource([DataFile("a.parquet", size_bytes=512_000_000)]))
plan(sql, file_groups=[["a.parquet"], ["b.parquet"]])        # pre-grouped, verbatim
```

### DuckLake

Feed it the rows of [`ducklake_list_files`](https://ducklake.select/docs/stable/duckdb/metadata/list_files):

```python
from splitql import plan, DuckLakeSource

rows = con.execute("FROM ducklake_list_files('lake', 'sales')").fetchall()
p = plan(sql, source=DuckLakeSource.from_list_files(rows, has_inlined_data=False))
```

The source enforces DuckLake's correctness caveats instead of hoping:

- **delete files present** → not eligible (a raw parquet scan would
  resurrect deleted rows);
- **inlined data** (on by default in DuckLake!) is invisible to the file
  list → the gate is fail-closed: splitting requires an explicit
  `has_inlined_data=False` assertion (check via `DATA_INLINING_ROW_LIMIT 0`
  or after flushing inlined data); `True` and `None` (unknown) both refuse.

## Worker count

Explicit `workers=N` always wins. With file sizes available (DuckLake always
has them), splitql can recommend instead:

```python
plan(sql, source=src)                                # ceil(total / 512MB), capped by #files
plan(sql, source=src, worker_memory_bytes=8 * 2**30) # target = memory / 4
plan(sql, source=src, max_workers=16)
```

Grouping balances by size (LPT greedy) when sizes are known, round-robin
otherwise.

## Visualize the plan

```python
open("plan.html", "w").write(p.to_html())  # self-contained interactive page
print(p.to_dot())                          # Graphviz
```

The HTML page shows the full execution graph — per-fragment cards with file
lists, byte shares and expandable SQL, flowing scan → partials → reduce →
result — with no external assets (works offline, light/dark aware).

`p.to_json()` gives the whole plan as a JSON envelope for non-Python callers.

## Correctness story

Distributed planning has a free oracle: the same query on a single node.
The test suite exploits it everywhere — a fixed battery of query shapes
(NULL-heavy aggregates included) plus seeded property-based random queries,
each executed both ways and compared. If fragments + reduce ever diverge
from single-node DuckDB, that's a bug, full stop.

## Roadmap

- `HAVING` (rewrites cleanly into the reduce)
- `COUNT(DISTINCT ...)` via exact re-aggregation or HLL sketches
- Iceberg / Delta partition sources (same metadata shape as DuckLake)
- Partition pruning from catalog stats (today: prune before calling)
- Dialect transpilation of fragments via sqlglot

## License

Apache-2.0
