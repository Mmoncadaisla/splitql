# Roadmap

- `HAVING` (rewrites cleanly into the reduce)
- `COUNT(DISTINCT ...)` via exact re-aggregation or HLL sketches
- Iceberg / Delta partition sources (same metadata shape as DuckLake)
- Non-file scan sources (e.g. Zarr chunk ranges via the `zarr` DuckDB
  community extension — the partition unit becomes a chunk-grid slice
  instead of a file list)
- Dialect transpilation of fragments via sqlglot
- Tree reduction for high-cardinality gathers
