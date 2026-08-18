# Partition sources

## Plain Parquet

```python
from splitql import plan, ParquetSource, DataFile

plan(sql, files=["a.parquet", "b.parquet"])                  # shorthand
plan(sql, source=ParquetSource([DataFile("a.parquet", size_bytes=512_000_000)]))
plan(sql, file_groups=[["a.parquet"], ["b.parquet"]])        # pre-grouped, verbatim:
                                                             # no pruning, no rebalancing
```

## DuckLake

Feed it the rows of [`ducklake_list_files`](https://ducklake.select/docs/stable/duckdb/metadata/list_files):

```python
from splitql import plan, DuckLakeSource

rows = con.execute("FROM ducklake_list_files('lake', 'sales')").fetchall()
p = plan(sql, source=DuckLakeSource.from_list_files(rows, has_inlined_data=False))
```

!!! tip "Pin the snapshot"

    Derive the file list from one snapshot so every fragment executes
    against the same logical version of the table, even while writers keep
    committing:

    ```sql
    FROM ducklake_list_files('lake', 'sales', snapshot_version => 42)
    ```

The source enforces DuckLake's correctness caveats instead of hoping:

- **delete files present** → not eligible (a raw parquet scan would
  resurrect deleted rows);
- **inlined data** (on by default in DuckLake!) is invisible to the file
  list → the gate is fail-closed: splitting requires an explicit
  `has_inlined_data=False` assertion (check via `DATA_INLINING_ROW_LIMIT 0`
  or after flushing inlined data); `True` and `None` (unknown) both refuse;
- **schema evolution**: DuckLake fragment scans use `union_by_name = TRUE`,
  but that unifies only within each fragment's file group — a worker whose
  group holds only old-generation files still cannot bind a newer column,
  and renames/drops need the catalog's column mapping that raw parquet
  scans cannot apply. Unlike inlined data this fails loudly (binder or
  concatenation error), so `has_schema_evolution=None` (unknown) plans
  with a warning, `True` refuses, `False` means you checked.
