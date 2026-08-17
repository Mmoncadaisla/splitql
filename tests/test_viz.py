from splitql import DataFile, plan

SQL = "SELECT region, sum(x) AS s FROM sales GROUP BY region"
FILES = [DataFile("a.parquet", 100), DataFile("b.parquet", 300)]


def test_html_graph():
    p = plan(SQL, files=FILES, workers=2)
    h = p.to_html()
    assert "<!doctype html>" in h
    assert "fragment 0" in h and "fragment 1" in h
    assert "a.parquet" in h and "b.parquet" in h
    assert "reduce" in h and "partials" in h
    assert "<script" in h  # interactive bits ship inline


def test_html_escapes_sql():
    p = plan("SELECT count(*) FROM sales WHERE region < 'x'", files=FILES, workers=1)
    assert "&lt;" in p.to_html()


def test_html_ineligible():
    p = plan("SELECT 1", files=FILES)
    assert "Not splittable" in p.to_html()


def test_dot_graph():
    p = plan(SQL, files=FILES, workers=2)
    d = p.to_dot()
    assert d.startswith("digraph")
    assert "f0 -> partials" in d and "f1 -> partials" in d
    assert "partials -> reduce" in d and "reduce -> result" in d


def test_dot_ineligible():
    d = plan("SELECT 1", files=FILES).to_dot()
    assert "digraph" in d and "ineligible" in d
