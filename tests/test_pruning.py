import duckdb
import pytest

from conftest import assert_same, oracle, run_split
from splitql import ColumnStats, DataFile, plan


def f(path, lo, hi, size=10, null_count=None, row_count=None):
    return DataFile(
        path,
        size_bytes=size,
        stats={"x": ColumnStats(lo, hi, null_count)},
        row_count=row_count,
    )


LOW = f("low.parquet", 0, 10)
HIGH = f("high.parquet", 40, 60)
NO_STATS = DataFile("nostats.parquet", size_bytes=10)


def kept_paths(sql, files, **kw):
    p = plan(sql, files=files, workers=len(files), **kw)
    assert p.eligible, p.reason
    return {df.path for g in p.fragment_files for df in g}, set(p.pruned_files)


@pytest.mark.parametrize(
    "predicate,expect_kept",
    [
        ("x > 50", {"high.parquet"}),
        ("x >= 40", {"high.parquet"}),
        ("x < 5", {"low.parquet"}),
        ("50 < x", {"high.parquet"}),  # flipped literal side
        ("x = 45", {"high.parquet"}),
        ("x BETWEEN 2 AND 8", {"low.parquet"}),
        ("x IN (7, 8)", {"low.parquet"}),
        ("x > 50 OR x < 5", {"low.parquet", "high.parquet"}),
        ("x > 20 AND x < 30", set()),  # all pruned -> smallest survives
        ("NOT (x = 5)", {"low.parquet", "high.parquet"}),  # NOT: cannot prove
        ("y > 99", {"low.parquet", "high.parquet"}),  # no stats for y
        ("x + 1 > 50", {"low.parquet", "high.parquet"}),  # expression: keep
    ],
)
def test_zone_map_pruning(predicate, expect_kept):
    kept, pruned = kept_paths(
        f"SELECT count(*) FROM t WHERE {predicate}", [LOW, HIGH]
    )
    if expect_kept:
        assert kept == expect_kept
        assert pruned == {"low.parquet", "high.parquet"} - expect_kept
    else:
        assert len(kept) == 1  # never-empty rule


def test_files_without_stats_are_always_kept():
    kept, pruned = kept_paths("SELECT count(*) FROM t WHERE x > 999", [LOW, NO_STATS])
    assert "nostats.parquet" in kept and pruned == {"low.parquet"}


def test_no_where_no_pruning():
    kept, pruned = kept_paths("SELECT count(*) FROM t", [LOW, HIGH])
    assert kept == {"low.parquet", "high.parquet"} and not pruned


def test_is_null_pruning():
    no_nulls = f("nonulls.parquet", 0, 10, null_count=0)
    with_nulls = f("nulls.parquet", 0, 10, null_count=3)
    kept, pruned = kept_paths(
        "SELECT count(*) FROM t WHERE x IS NULL", [no_nulls, with_nulls]
    )
    assert kept == {"nulls.parquet"} and pruned == {"nonulls.parquet"}


def test_date_literal_pruning():
    from datetime import date

    early = DataFile(
        "early.parquet", 10,
        stats={"d": ColumnStats(date(2026, 1, 1), date(2026, 2, 1))},
    )
    late = DataFile(
        "late.parquet", 10,
        stats={"d": ColumnStats(date(2026, 6, 1), date(2026, 7, 1))},
    )
    kept, pruned = kept_paths(
        "SELECT count(*) FROM t WHERE d > DATE '2026-05-01'", [early, late]
    )
    assert kept == {"late.parquet"} and pruned == {"early.parquet"}


def with_real_stats(files):
    con = duckdb.connect()
    out = []
    for p in files:
        mn, mx, n = con.execute(
            f"SELECT min(x), max(x), count(*) FROM read_parquet('{p}')"
        ).fetchone()
        out.append(
            DataFile(p, stats={"x": ColumnStats(mn, mx)}, row_count=n)
        )
    return out


def test_all_pruned_still_answers_global_aggregates(dataset):
    sql = "SELECT count(*) AS n, sum(x) AS s FROM sales WHERE x > 1e9"
    p, got = run_split(sql, with_real_stats(dataset), workers=4)
    assert p.workers == 1 and len(p.pruned_files) == 3
    assert_same(got, oracle(sql, dataset))


def test_pruning_preserves_oracle_results(dataset):
    sql = "SELECT region, count(*) AS n FROM sales WHERE x > 100 GROUP BY region"
    p, got = run_split(sql, with_real_stats(dataset), workers=3)
    assert_same(got, oracle(sql, dataset))
