# Quickstart

## Install

```bash
pip install splitql   # depends only on sqlglot
```

## Plan a query

```python
from splitql import plan

p = plan(
    "SELECT region, sum(amount) AS total FROM sales "
    "WHERE d >= DATE '2026-01-01' GROUP BY region",
    files=["s3://lake/sales/a.parquet", "s3://lake/sales/b.parquet",
           "s3://lake/sales/c.parquet", "s3://lake/sales/d.parquet"],
    fragments=2,
)

p.fragments
# round-robin without file sizes; size-balanced (LPT) when sizes are known
# ['SELECT region AS g0, SUM(amount) AS a0 FROM READ_PARQUET([...a..., ...c...]) AS sales WHERE ... GROUP BY region',
#  'SELECT region AS g0, SUM(amount) AS a0 FROM READ_PARQUET([...b..., ...d...]) AS sales WHERE ... GROUP BY region']
p.reduce
# 'SELECT g0 AS "region", SUM(a0) AS "total" FROM partials GROUP BY g0'
```

## Run the fragments

Execution is yours. The minimal in-process runner (`pip install duckdb pyarrow`
— splitql itself never imports them):

```python
import duckdb, pyarrow as pa

partials = pa.concat_tables(
    [duckdb.connect().execute(f).to_arrow_table() for f in p.fragments]
)
con = duckdb.connect()
con.register(p.partials_table, partials)
result = con.execute(p.reduce).fetchall()
```

## The single-node fallback

Anything about the query that prevents a correct split returns
`eligible=False` with a reason — never an exception, never a wrong split.
Run the original query single-node in that case:

```python
p = plan("SELECT a FROM t JOIN u ON ...", files=[...])
p.eligible   # False
p.reason     # 'joins are not supported'
```
