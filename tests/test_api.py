import json

import pytest

from splitql import (
    DataFile,
    DuckLakeSource,
    ParquetSource,
    group_files,
    plan,
    recommend_workers,
)

SQL = "SELECT count(*) FROM sales"


def test_file_groups_passthrough():
    p = plan(SQL, file_groups=[["a.parquet", "b.parquet"], ["c.parquet"]])
    assert p.eligible and p.workers == 2
    assert "a.parquet" in p.fragments[0] and "b.parquet" in p.fragments[0]
    assert "c.parquet" in p.fragments[1]


def test_exactly_one_partition_input():
    with pytest.raises(ValueError):
        plan(SQL, files=["a.parquet"], file_groups=[["a.parquet"]])
    with pytest.raises(ValueError):
        plan(SQL)
    # API misuse must not be masked by an ineligible or unparseable query
    with pytest.raises(ValueError):
        plan("this is not sql (", files=["a.parquet"], file_groups=[["a.parquet"]])
    with pytest.raises(ValueError):
        plan("SELECT 1")


def test_empty_source_is_ineligible():
    assert not plan(SQL, files=[]).eligible


def test_empty_group_is_api_misuse():
    with pytest.raises(ValueError):
        plan(SQL, file_groups=[["a.parquet"], []])


def test_path_escaping():
    p = plan(SQL, files=["we'ird.parquet"], workers=1)
    assert p.eligible
    assert "we''ird.parquet" in p.fragments[0]


def test_partials_table_is_configurable():
    p = plan(SQL, files=["a.parquet"], workers=1, partials_table="scratch.pieces")
    assert "scratch.pieces" in p.reduce


def test_unordered_limit_warns():
    p = plan("SELECT id FROM sales LIMIT 5", files=["a.parquet"], workers=1)
    assert p.eligible and any("LIMIT without ORDER BY" in w for w in p.warnings)
    ordered = plan(
        "SELECT id FROM sales ORDER BY id LIMIT 5", files=["a.parquet"], workers=1
    )
    assert ordered.eligible and not ordered.warnings


def test_json_roundtrip():
    p = plan(SQL, files=["a.parquet"], workers=1)
    data = json.loads(p.to_json())
    assert data["eligible"] is True
    assert len(data["fragments"]) == 1


def test_ducklake_delete_files_block_split():
    rows = [
        {"data_file": "f1.parquet", "data_file_size_bytes": 100, "delete_file": None},
        {"data_file": "f2.parquet", "data_file_size_bytes": 100, "delete_file": "d.parquet"},
    ]
    src = DuckLakeSource.from_list_files(rows)
    p = plan(SQL, source=src)
    assert not p.eligible and "delete" in p.reason


def test_ducklake_inlined_data_is_fail_closed():
    rows = [{"data_file": "f1.parquet", "data_file_size_bytes": 100, "delete_file": None}]
    unknown = plan(SQL, source=DuckLakeSource.from_list_files(rows))
    assert not unknown.eligible and "unknown" in unknown.reason
    checked = plan(
        SQL, source=DuckLakeSource.from_list_files(rows, has_inlined_data=False)
    )
    assert checked.eligible and not checked.warnings
    inlined = plan(
        SQL, source=DuckLakeSource.from_list_files(rows, has_inlined_data=True)
    )
    assert not inlined.eligible and "inlined" in inlined.reason


def test_ducklake_positional_rows():
    rows = [("f1.parquet", 100, 4, None, None, None, None, None)]
    src = DuckLakeSource.from_list_files(rows)
    assert src.files == [DataFile("f1.parquet", 100)]
    assert not src.has_delete_files


def test_recommend_workers():
    files = [DataFile(f"f{i}.parquet", 512 * 1024 * 1024) for i in range(10)]
    assert recommend_workers(files) == 10
    assert recommend_workers(files, max_workers=4) == 4
    assert recommend_workers(files, worker_memory_bytes=8 * 1024**3) == 3
    with pytest.raises(ValueError):
        recommend_workers([DataFile("f.parquet")])


def test_group_files_balances_by_size():
    files = [DataFile(f"f{i}", s) for i, s in enumerate([10, 9, 1, 1, 1])]
    groups = group_files(files, 2)
    sums = sorted(sum(f.size_bytes for f in g) for g in groups)
    assert sums == [11, 11]


def test_workers_recommended_when_sizes_known():
    src = ParquetSource([DataFile(f"f{i}.parquet", 512 * 1024 * 1024) for i in range(6)])
    p = plan(SQL, source=src, max_workers=3)
    assert p.eligible and p.workers == 3


def test_target_fragment_bytes_drives_fanout():
    files = [DataFile(f"f{i}.parquet", 1024 * 1024) for i in range(6)]  # 6 MB total
    assert plan(SQL, files=files).workers == 1  # default 512MB target: one is enough
    assert plan(SQL, files=files, target_fragment_bytes=2 * 1024 * 1024).workers == 3
