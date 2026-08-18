# Guarantees

## What "identical to single-node" means, precisely

Two caveats apply to the equivalence contract — both inherent to parallel
execution and both present in single-node DuckDB itself:

- **Floating-point aggregation order.** `SUM`/`AVG` over inexact types
  (DOUBLE/FLOAT) are evaluated in a different association order across
  fragments, so results can differ in the last bits. Single-node DuckDB has
  the same property between runs: its multi-threaded aggregation already
  makes FP summation order nondeterministic. Exact types (integers,
  DECIMAL) are exactly equal.
- **Queries that are nondeterministic anyway.** `LIMIT` without `ORDER BY`
  returns an arbitrary row subset, and ties in `ORDER BY ... LIMIT k` break
  arbitrarily — in any engine. The split returns one of the valid answers,
  not necessarily the same one as a given single-node run (planning emits a
  warning for the unordered-LIMIT case). Add a tiebreaker column for full
  determinism.

Queries with deterministic semantics and exact types produce identical
results — that is the tested contract.

## The gather bottleneck

Know your GROUP BY cardinality. Partial results scale with the **number of
groups**, not the input size. `GROUP BY region` gathers a handful of rows
per fragment no matter how many terabytes were scanned; `GROUP BY user_id`
over 500M users makes every partial huge, and the central gather becomes
the problem that shuffles exist to solve — which splitql deliberately does
not solve. Rule of thumb: split when the partials are small relative to the
scan.

Roadmap mitigation: **tree reduction**. Decomposable aggregates re-reduce —
the reduce query is itself eligible SQL over the partials — so partials can
be combined in fan-in stages instead of one central gather. That relieves
coordinator bandwidth; it still is not a shuffle.

## Correctness story

Distributed planning has a free oracle: the same query on a single node.
The test suite exploits it everywhere — a fixed battery of query shapes
(NULL-heavy aggregates included) plus seeded property-based random queries,
each executed both ways and compared. If fragments + reduce ever diverge
from single-node DuckDB, that's a bug, full stop.
