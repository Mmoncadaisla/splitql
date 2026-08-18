# Eligibility

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
`COUNT(DISTINCT ...)`, `FILTER (WHERE ...)`, `DISTINCT ON`, percentage and
`WITH TIES` limits, `USING SAMPLE`, `COLLATE`, table aliases with column
lists, volatile functions, positional GROUP BY, and any aggregate outside
the whitelist.

!!! note "Refusal is a feature"

    A conservative `False` is always available as the single-node fallback,
    so splitql can never make your results wrong — only your fast path
    narrower.
