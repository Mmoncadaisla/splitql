"""Zone-map pruning: drop files whose column min/max ranges cannot satisfy
the WHERE clause.

Strictly conservative in the safe direction: pruning a file that could
match would be silent data loss, so ANY uncertainty — unknown predicate
shape, missing stats, incomparable types, NOT branches — keeps the file.
Keeping a file is always correct (its rows just fail the filter at scan
time); dropping one is only allowed on proof.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlglot import exp

from .sources import ColumnStats, DataFile


def prune_files(
    files: list[DataFile], where: exp.Where | None
) -> tuple[list[DataFile], list[DataFile]]:
    """Return (kept, pruned). Files without stats are always kept."""
    if where is None:
        return list(files), []
    kept, pruned = [], []
    for f in files:
        if f.stats and _may_match(where.this, f) is False:
            pruned.append(f)
        else:
            kept.append(f)
    return kept, pruned


def _may_match(node: exp.Expression, f: DataFile) -> bool:
    """True = the file may contain matching rows (or we cannot tell)."""
    if isinstance(node, exp.Paren):
        return _may_match(node.this, f)
    if isinstance(node, exp.And):
        return _may_match(node.this, f) and _may_match(node.expression, f)
    if isinstance(node, exp.Or):
        return _may_match(node.this, f) or _may_match(node.expression, f)
    if isinstance(node, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
        return _comparison_may_match(node, f)
    if isinstance(node, exp.Between):
        return _between_may_match(node, f)
    if isinstance(node, exp.In):
        return _in_may_match(node, f)
    if isinstance(node, exp.Is):
        return _is_null_may_match(node, f)
    return True  # NOT, functions, anything else: cannot prove, keep


def _stats_for(node: exp.Expression, f: DataFile) -> ColumnStats | None:
    if isinstance(node, exp.Column) and f.stats:
        return f.stats.get(node.name)
    return None


def _literal_value(node: exp.Expression):
    if isinstance(node, exp.Literal):
        if node.is_string:
            return node.name
        try:
            n = float(node.name)
            return int(n) if n.is_integer() and "." not in node.name else n
        except ValueError:
            return None
    if isinstance(node, exp.Neg):
        v = _literal_value(node.this)
        return -v if isinstance(v, (int, float)) else None
    if isinstance(node, exp.Cast) and isinstance(node.this, exp.Literal):
        text = node.this.name
        kind = node.to.this if node.to else None
        try:
            if kind == exp.DataType.Type.DATE:
                return date.fromisoformat(text)
            if kind in (exp.DataType.Type.TIMESTAMP, exp.DataType.Type.TIMESTAMPTZ):
                return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _cmp(a, b):
    """-1/0/1, or None when the pair is not comparable."""
    if isinstance(a, date) and not isinstance(a, datetime) and isinstance(b, str):
        try:
            b = date.fromisoformat(b)
        except ValueError:
            return None
    if isinstance(b, date) and not isinstance(b, datetime) and isinstance(a, str):
        try:
            a = date.fromisoformat(a)
        except ValueError:
            return None
    try:
        if a == b:
            return 0
        if a < b:
            return -1
        if a > b:
            return 1
    except TypeError:
        return None
    return None


def _range_may_satisfy(op: type, stats: ColumnStats, v) -> bool:
    lo, hi = stats.min_value, stats.max_value
    if lo is None or hi is None or v is None:
        return True
    c_lo, c_hi = _cmp(lo, v), _cmp(hi, v)
    if c_lo is None or c_hi is None:
        return True
    if op is exp.EQ:
        return c_lo <= 0 <= c_hi
    if op is exp.NEQ:
        return not (c_lo == 0 and c_hi == 0)
    if op is exp.GT:
        return c_hi > 0
    if op is exp.GTE:
        return c_hi >= 0
    if op is exp.LT:
        return c_lo < 0
    if op is exp.LTE:
        return c_lo <= 0
    return True


_FLIPPED = {exp.GT: exp.LT, exp.GTE: exp.LTE, exp.LT: exp.GT, exp.LTE: exp.GTE}


def _comparison_may_match(node: exp.Expression, f: DataFile) -> bool:
    op = type(node)
    stats = _stats_for(node.this, f)
    if stats is not None:
        return _range_may_satisfy(op, stats, _literal_value(node.expression))
    stats = _stats_for(node.expression, f)
    if stats is not None:  # literal <op> col — flip the operator
        return _range_may_satisfy(_FLIPPED.get(op, op), stats, _literal_value(node.this))
    return True


def _between_may_match(node: exp.Between, f: DataFile) -> bool:
    stats = _stats_for(node.this, f)
    if stats is None:
        return True
    lo, hi = _literal_value(node.args.get("low")), _literal_value(node.args.get("high"))
    return _range_may_satisfy(exp.GTE, stats, lo) and _range_may_satisfy(
        exp.LTE, stats, hi
    )


def _in_may_match(node: exp.In, f: DataFile) -> bool:
    stats = _stats_for(node.this, f)
    if stats is None or not node.expressions:
        return True
    values = [_literal_value(e) for e in node.expressions]
    if any(v is None for v in values):
        return True
    return any(_range_may_satisfy(exp.EQ, stats, v) for v in values)


def _is_null_may_match(node: exp.Is, f: DataFile) -> bool:
    if not isinstance(node.expression, exp.Null):
        return True
    stats = _stats_for(node.this, f)
    if stats is None or stats.null_count is None:
        return True
    return stats.null_count > 0
