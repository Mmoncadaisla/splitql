"""Every eligible query must produce EXACTLY the single-node result.

The oracle is trivially available (DuckDB over all files at once), which is
the whole testing story of this library: if fragments + reduce ever diverge
from the oracle, that's a correctness bug, full stop.
"""

import pytest

from conftest import assert_same, oracle, run_split

UNORDERED = [
    "SELECT count(*) FROM sales",
    "SELECT sum(x) AS s, min(y) AS mn, max(y) AS mx, avg(x) AS a FROM sales",
    "SELECT sum(x) / count(*) AS ratio FROM sales",
    "SELECT sum(x) AS a, sum(x) + count(*) AS b FROM sales",
    "SELECT region, count(*) AS n, sum(x) AS total FROM sales GROUP BY region",
    "SELECT region, avg(x) AS m FROM sales WHERE y > 5 GROUP BY region",
    "SELECT y % 3 AS bucket, sum(x) AS s FROM sales GROUP BY y % 3",
    "SELECT upper(region) AS r, count(*) AS n FROM sales GROUP BY r",
    "SELECT y % 2 AS Bucket, count(*) AS n FROM sales GROUP BY bucket",
    "SELECT id, x FROM sales WHERE x > 100",
    "SELECT DISTINCT region FROM sales",
    "SELECT count(*) AS n, sum(x) AS s, avg(x) AS a FROM sales WHERE x > 1e9",
    "SELECT s.region, count(*) AS n FROM sales s GROUP BY s.region",
    "SELECT min(d) AS first_day, max(d) AS last_day FROM sales",
    # NULL semantics — where naive partial aggregation usually breaks
    "SELECT count(nx) AS n FROM sales",
    "SELECT count(*) AS all_rows, count(nx) AS non_null FROM sales",
    "SELECT avg(nx) AS a, sum(nx) AS s, min(nx) AS mn, max(nx) AS mx FROM sales",
    "SELECT region, count(nx) AS n, avg(nx) AS a FROM sales GROUP BY region",
    # expressions inside and around aggregates
    "SELECT sum(x + y) AS s FROM sales",
    "SELECT sum(CASE WHEN region = 'north' THEN x ELSE 0 END) AS north_x FROM sales",
    "SELECT max(x) - min(x) AS spread FROM sales",
    "SELECT avg(x) * 2 + 1 AS scaled FROM sales",
    "SELECT round(sum(x), 2) AS r FROM sales",
    # richer predicates
    "SELECT count(*) FROM sales WHERE region IN ('north', 'south')",
    "SELECT count(*) FROM sales WHERE x BETWEEN 20 AND 90",
    "SELECT count(*) FROM sales WHERE region LIKE 'n%'",
    "SELECT count(*) FROM sales WHERE nx IS NULL",
    "SELECT count(*) FROM sales WHERE y NOT IN (1, 2) AND x > 30",
    "SELECT sum(x) AS s FROM sales WHERE region = 'east' OR y = 7",
    # multiple / computed group keys
    "SELECT region, y, sum(x) AS s FROM sales GROUP BY region, y",
    "SELECT region, y % 2 AS parity, avg(x) AS m FROM sales GROUP BY region, y % 2",
    "SELECT strftime(d, '%Y-%m') AS month, count(*) AS n FROM sales GROUP BY month",
    # GROUP BY without aggregates — global dedup of groups
    "SELECT region FROM sales GROUP BY region",
    "SELECT y % 2 AS parity FROM sales GROUP BY y % 2",
    "SELECT region, y FROM sales GROUP BY region, y",
    # DISTINCT over aggregated output — dedup of equal aggregates
    "SELECT DISTINCT count(*) AS n FROM sales GROUP BY region",
    # plain scans and distinct
    "SELECT region FROM sales WHERE x > 140",
    "SELECT DISTINCT region, y FROM sales WHERE y > 10",
    "SELECT id, x * 2 AS x2 FROM sales WHERE x * 2 > 280",
]

ORDERED = [
    "SELECT region, count(*) AS n FROM sales GROUP BY region ORDER BY region",
    "SELECT region, avg(x) AS m FROM sales WHERE y > 5 GROUP BY region ORDER BY m DESC",
    "SELECT id, x FROM sales ORDER BY x DESC, id LIMIT 5",
    "SELECT s.region, count(*) AS n FROM sales s GROUP BY s.region ORDER BY s.region",
    "SELECT region, count(*) AS n FROM sales GROUP BY region ORDER BY 2 DESC, 1 LIMIT 2",
    "SELECT region, sum(x) AS s FROM sales GROUP BY region ORDER BY s DESC LIMIT 3",
    "SELECT DISTINCT y FROM sales ORDER BY y",
    "SELECT id FROM sales WHERE y = 3 ORDER BY id LIMIT 10",
    "SELECT strftime(d, '%Y-%m') AS month, count(*) AS n FROM sales GROUP BY month ORDER BY month",
]


@pytest.mark.parametrize("sql", UNORDERED)
@pytest.mark.parametrize("workers", [1, 2, 4])
def test_matches_oracle_unordered(sql, workers, dataset):
    _, got = run_split(sql, dataset, workers=workers)
    assert_same(got, oracle(sql, dataset))


@pytest.mark.parametrize("sql", ORDERED)
@pytest.mark.parametrize("workers", [1, 3])
def test_matches_oracle_ordered(sql, workers, dataset):
    _, got = run_split(sql, dataset, workers=workers)
    assert_same(got, oracle(sql, dataset), ordered=True)


def test_fragments_are_runnable_independently(dataset):
    p, _ = run_split("SELECT region, sum(x) AS s FROM sales GROUP BY region", dataset)
    assert p.workers == 2
    assert all("read_parquet" in f.lower() for f in p.fragments)
    assert p.reduce is not None and "partials" in p.reduce
