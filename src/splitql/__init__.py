"""splitql — split a SQL query into per-partition fragments plus a reduce query.

Pure planning, no runtime: you bring the execution (threads, Ray, Lambdas,
ssh — anything that can run SQL and concatenate results).
"""

from .ir import Plan
from .planner import plan
from .sizing import group_files, recommend_workers
from .sources import ColumnStats, DataFile, DuckLakeSource, ParquetSource

__all__ = [
    "plan",
    "Plan",
    "DataFile",
    "ColumnStats",
    "ParquetSource",
    "DuckLakeSource",
    "recommend_workers",
    "group_files",
]

__version__ = "0.1.0"
