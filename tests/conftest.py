from decimal import Decimal

import duckdb
import pyarrow as pa
import pytest

from splitql import plan


@pytest.fixture(scope="session")
def dataset(tmp_path_factory):
    """Four parquet files partitioned by id % 4, ~10k rows total."""
    d = tmp_path_factory.mktemp("data")
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE t AS
        SELECT i AS id,
               ['north', 'south', 'east', 'west'][1 + i % 4] AS region,
               (i * 37 % 1000) / 7.0 AS x,
               i % 13 AS y,
               CASE WHEN i % 7 = 0 THEN NULL ELSE (i % 100) / 3.0 END AS nx,
               DATE '2026-01-01' + INTERVAL (i % 90) DAY AS d
        FROM range(0, 10000) r(i)
        """
    )
    files = []
    for part in range(4):
        p = str(d / f"part{part}.parquet")
        con.execute(f"COPY (SELECT * FROM t WHERE id % 4 = {part}) TO '{p}' (FORMAT parquet)")
        files.append(p)
    return files


def oracle(sql: str, files: list[str]):
    """Single-node ground truth: the same query over all files at once."""
    con = duckdb.connect()
    file_list = ", ".join(f"'{f}'" for f in files)
    con.execute(f"CREATE VIEW sales AS SELECT * FROM read_parquet([{file_list}])")
    return con.execute(sql).fetchall()


def run_split(sql: str, files: list[str], workers: int = 2, **kwargs):
    """Execute a plan the way any runtime would: fragments on isolated
    connections, partials concatenated, reduce over the combination."""
    p = plan(sql, files=files, workers=workers, **kwargs)
    assert p.eligible, f"unexpectedly ineligible: {p.reason}"
    partials = pa.concat_tables(
        [duckdb.connect().execute(frag).to_arrow_table() for frag in p.fragments]
    )
    con = duckdb.connect()
    con.register(p.partials_table, partials)
    return p, con.execute(p.reduce).fetchall()


def _norm(value):
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float):
        return round(value, 6)
    return value


def assert_same(got, expected, ordered=False):
    g = [tuple(_norm(v) for v in row) for row in got]
    e = [tuple(_norm(v) for v in row) for row in expected]
    if not ordered:
        g, e = sorted(g, key=repr), sorted(e, key=repr)
    assert g == e, f"split result differs from single-node oracle:\n{g}\nvs\n{e}"
