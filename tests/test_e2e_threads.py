"""End-to-end concurrency: fragments executed by a thread pool, one DuckDB
connection per fragment (the real deployment shape: one connection per
remote worker), partials deliberately shuffled before the reduce —
completion order must never change the result."""

import random
from concurrent.futures import ThreadPoolExecutor

import duckdb
import pyarrow as pa
import pytest

from conftest import assert_same, oracle
from splitql import plan

UNORDERED = [
    "SELECT count(*) FROM sales",
    "SELECT region, count(*) AS n, sum(x) AS total, avg(x) AS m FROM sales GROUP BY region",
    "SELECT y % 3 AS bucket, avg(nx) AS a, count(nx) AS n FROM sales WHERE x > 30 GROUP BY y % 3",
    "SELECT id, x FROM sales WHERE x > 120",
    "SELECT region FROM sales GROUP BY region",
]

ORDERED = [
    "SELECT region, sum(x) AS s FROM sales GROUP BY region ORDER BY s DESC LIMIT 3",
    "SELECT id, x FROM sales ORDER BY x DESC, id LIMIT 7",
]


def run_threaded(p, shuffle_seed: int):
    def one_fragment(sql: str):
        return duckdb.connect().execute(sql).to_arrow_table()

    with ThreadPoolExecutor(max_workers=p.fragment_count) as pool:
        tables = list(pool.map(one_fragment, p.fragments))
    random.Random(shuffle_seed).shuffle(tables)
    con = duckdb.connect()
    con.register(p.partials_table, pa.concat_tables(tables))
    return con.execute(p.reduce).fetchall()


@pytest.mark.parametrize("i,sql", list(enumerate(UNORDERED)))
def test_threaded_execution_matches_single_node(i, sql, dataset):
    p = plan(sql, files=dataset, fragments=4)
    assert p.eligible, p.reason
    for seed in (i, i + 100):  # two partial orders, same result
        assert_same(run_threaded(p, seed), oracle(sql, dataset))


@pytest.mark.parametrize("i,sql", list(enumerate(ORDERED)))
def test_threaded_ordered_matches_single_node(i, sql, dataset):
    p = plan(sql, files=dataset, fragments=4)
    assert p.eligible, p.reason
    for seed in (i, i + 100):
        assert_same(run_threaded(p, seed), oracle(sql, dataset), ordered=True)
