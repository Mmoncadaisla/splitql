"""Property-based oracle testing: seeded random eligible queries must match
single-node execution exactly. Every seed is deterministic and replayable —
a failing seed IS the bug report."""

import random

import pytest

from conftest import assert_same, oracle, run_split

NUM_COLS = ["x", "y", "nx"]
KEYS = ["region", "y % 4", "strftime(d, '%m')", "y"]
PREDS = [
    "x > 70",
    "y BETWEEN 2 AND 9",
    "region IN ('north', 'east')",
    "nx IS NOT NULL",
    "x < 130",
    "id % 3 = 0",
    "region LIKE '%th'",
    "d >= DATE '2026-02-01'",
]
AGG_FNS = ["sum", "count", "min", "max", "avg"]


def gen_query(rng: random.Random) -> str:
    n_aggs = rng.randint(1, 3)
    outputs = [
        f"{rng.choice(AGG_FNS)}({rng.choice(NUM_COLS)}) AS agg{i}"
        for i in range(n_aggs)
    ]
    if rng.random() < 0.3:
        outputs.append("count(*) AS cnt")
    keys = rng.sample(KEYS, rng.randint(0, 2))
    key_out = [f"{k} AS k{i}" for i, k in enumerate(keys)]
    sql = "SELECT " + ", ".join(key_out + outputs) + " FROM sales"
    if rng.random() < 0.6:
        sql += " WHERE " + " AND ".join(rng.sample(PREDS, rng.randint(1, 2)))
    if keys:
        sql += " GROUP BY " + ", ".join(f"k{i}" for i in range(len(keys)))
    return sql


@pytest.mark.parametrize("seed", range(60))
def test_random_aggregation_matches_oracle(seed, dataset):
    rng = random.Random(seed)
    sql = gen_query(rng)
    workers = rng.choice([1, 2, 3, 4])
    _, got = run_split(sql, dataset, workers=workers)
    assert_same(got, oracle(sql, dataset)), sql
