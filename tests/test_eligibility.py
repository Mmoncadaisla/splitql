"""Ineligible queries must come back with eligible=False and a reason —
never an exception, never a wrong split."""

import pytest

from splitql import plan

FILES = ["a.parquet", "b.parquet"]

REJECTED = [
    ("SELECT * FROM a JOIN b ON a.id = b.id", "join"),
    ("WITH c AS (SELECT 1) SELECT * FROM c", "CTE"),
    ("SELECT * FROM sales WHERE id IN (SELECT id FROM other)", "subquer"),
    ("SELECT id, row_number() OVER (ORDER BY id) FROM sales", "window"),
    ("SELECT region, count(*) FROM sales GROUP BY region HAVING count(*) > 1", "HAVING"),
    ("SELECT id FROM sales LIMIT 10 OFFSET 5", "OFFSET"),
    ("SELECT count(DISTINCT region) FROM sales", "DISTINCT aggregate"),
    ("SELECT region, count(*) FROM sales GROUP BY 1", "positional"),
    ("SELECT *, count(*) FROM sales", "SELECT *"),
    ("SELECT id FROM sales ORDER BY id + 1", "ORDER BY"),
    ("SELECT median(x) FROM sales", "aggregate"),
    ("SELECT sum(x) FILTER (WHERE y > 5) FROM sales", "FILTER"),
    ("SELECT count(*) FROM sales WHERE random() < 0.5", "volatile"),
    ("SELECT now() AS t, count(*) FROM sales", "volatile"),
    ("SELECT id FROM sales WHERE d < current_date", "volatile"),
    ("SELECT uuid() AS u, id FROM sales", "volatile"),
    ("SELECT current_localtimestamp() AS t, id FROM sales", "volatile"),
    ("SELECT get_current_time() AS t, id FROM sales", "volatile"),
    ("SELECT id FROM sales WHERE d < localtimestamp", "volatile"),
    ("SELECT 1", "FROM"),
    ("SELECT id FROM sales UNION SELECT id FROM sales", "SELECT"),
    ("SELECT region, sum(x), y FROM sales GROUP BY region", "GROUP BY"),
    ("SELECT sum(x) FROM sales ORDER BY region", "not in the SELECT output"),
    ("this is not sql at all (", "parse error"),
    ("SELECT DISTINCT ON (region) region, x FROM sales ORDER BY region, x", "DISTINCT ON"),
    ("SELECT id FROM sales LIMIT 10%", "percentage"),
    ("SELECT id FROM sales ORDER BY x LIMIT 10 WITH TIES", "WITH TIES"),
    ("SELECT count(*) FROM sales USING SAMPLE 10 ROWS", "SAMPLE"),
    ("SELECT region FROM sales GROUP BY region COLLATE NOCASE", "COLLATE"),
    ("SELECT renamed FROM sales AS s(renamed)", "column lists"),
]


@pytest.mark.parametrize("sql,reason_fragment", REJECTED)
def test_rejected(sql, reason_fragment):
    p = plan(sql, files=FILES, workers=2)
    assert not p.eligible
    assert p.reduce is None and p.fragments == []
    assert reason_fragment.lower() in (p.reason or "").lower(), p.reason
    assert p.query == sql  # the JSON envelope alone must allow the fallback
