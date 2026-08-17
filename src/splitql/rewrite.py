"""Two-phase rewrite: one eligible SELECT -> (fragment AST, reduce AST).

The fragment still references the original table; the planner substitutes
the per-group scan afterwards. The reduce reads from a placeholder table
(``__PARTIALS__``) that the planner renames, and which must hold the
concatenation of all fragment results.

Aggregate decomposition is the standard MPP algebra:

    SUM(x)   -> SUM(partial sums)
    COUNT(x) -> SUM(partial counts)
    MIN/MAX  -> MIN/MAX of partials
    AVG(x)   -> SUM(partial sums) / SUM(partial counts)

Group keys are materialized as g0..gk and partial aggregates as a0..an, so
the partials relation contains only generated names and can never collide
with user columns.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlglot import exp

PARTIALS_PLACEHOLDER = "__PARTIALS__"


@dataclass
class SplitResult:
    fragment: exp.Select | None = None
    reduce: exp.Select | None = None
    reason: str | None = None


def split(select: exp.Select, dialect: str) -> SplitResult:
    select = select.copy()
    _resolve_group_aliases(select)
    if select.find(exp.AggFunc) is None:
        return _split_scan(select)
    return _split_aggregation(select, dialect)


def _resolve_group_aliases(select: exp.Select) -> None:
    """GROUP BY m, where m aliases a SELECT expression -> group by the expression."""
    group = select.args.get("group")
    if group is None:
        return
    alias_map = {
        e.alias: e.this for e in select.expressions if isinstance(e, exp.Alias)
    }
    for g in list(group.expressions):
        if isinstance(g, exp.Column) and not g.table and g.name in alias_map:
            g.replace(alias_map[g.name].copy())


def _split_scan(select: exp.Select) -> SplitResult:
    """No aggregates: fragments run the query per partition, the reduce
    re-applies DISTINCT / ORDER BY / LIMIT globally. With ORDER BY + LIMIT
    the fragment keeps both (per-partition top-k is a superset of the
    global top-k)."""
    fragment = select.copy()
    limit = select.args.get("limit")
    if limit is None:
        fragment.set("order", None)

    reduce_ = exp.Select().select(exp.Star()).from_(exp.to_table(PARTIALS_PLACEHOLDER))
    if select.args.get("distinct"):
        reduce_.set("distinct", exp.Distinct())
    if select.args.get("order"):
        reduce_.set("order", _dequalify(select.args["order"]))
    if limit is not None:
        reduce_.set("limit", limit.copy())
    return SplitResult(fragment=fragment, reduce=reduce_)


def _dequalify(order: exp.Expression) -> exp.Expression:
    """The reduce reads from the partials relation, so ORDER BY columns must
    drop any original table qualifier (s.region -> region)."""
    order = order.copy()
    for col in order.find_all(exp.Column):
        col.set("table", None)
    return order


@dataclass
class _AggPlan:
    partial_cols: list[exp.Expression] = field(default_factory=list)
    final_for: dict[str, exp.Expression] = field(default_factory=dict)
    generated_names: set[str] = field(default_factory=set)


def _decompose_aggregates(select: exp.Select, dialect: str) -> _AggPlan:
    plan = _AggPlan()
    n = 0
    for agg in select.find_all(exp.AggFunc):
        key = agg.sql(dialect=dialect)
        if key in plan.final_for:
            continue
        name = f"a{n}"
        n += 1
        if isinstance(agg, exp.Avg):
            plan.partial_cols.append(
                exp.alias_(exp.Sum(this=agg.this.copy()), f"{name}_s")
            )
            plan.partial_cols.append(
                exp.alias_(exp.Count(this=agg.this.copy()), f"{name}_c")
            )
            plan.final_for[key] = exp.Div(
                this=exp.Sum(this=exp.column(f"{name}_s")),
                expression=exp.Sum(this=exp.column(f"{name}_c")),
            )
            plan.generated_names.update({f"{name}_s", f"{name}_c"})
        else:
            plan.partial_cols.append(exp.alias_(agg.copy(), name))
            final_cls = exp.Sum if isinstance(agg, (exp.Sum, exp.Count)) else agg.__class__
            plan.final_for[key] = final_cls(this=exp.column(name))
            plan.generated_names.add(name)
    return plan


def _split_aggregation(select: exp.Select, dialect: str) -> SplitResult:
    group = select.args.get("group")
    group_exprs: list[exp.Expression] = []
    group_alias: dict[str, str] = {}
    if group is not None:
        for g in group.expressions:
            sql = g.sql(dialect=dialect)
            if sql not in group_alias:
                group_alias[sql] = f"g{len(group_exprs)}"
                group_exprs.append(g)

    aggs = _decompose_aggregates(select, dialect)

    fragment = select.copy()
    fragment.set("order", None)
    fragment.set("limit", None)
    frag_selects = [
        exp.alias_(g.copy(), group_alias[g.sql(dialect=dialect)]) for g in group_exprs
    ] + aggs.partial_cols
    fragment.set("expressions", frag_selects)
    if group_exprs:
        fragment.set("group", exp.Group(expressions=[g.copy() for g in group_exprs]))

    allowed_cols = aggs.generated_names | set(group_alias.values())

    def _rebuild(node: exp.Expression) -> exp.Expression:
        """Top-down replacement: aggregate nodes and group expressions are
        swapped BEFORE their children are visited, so a grouped column that
        also appears inside an aggregate is not rewritten twice."""
        sql = node.sql(dialect=dialect)
        if isinstance(node, exp.AggFunc) and sql in aggs.final_for:
            return aggs.final_for[sql].copy()
        if sql in group_alias:
            return exp.column(group_alias[sql])
        node = node.copy()
        for key, value in list(node.args.items()):
            if isinstance(value, exp.Expression):
                node.set(key, _rebuild(value))
            elif isinstance(value, list):
                node.set(
                    key,
                    [
                        _rebuild(v) if isinstance(v, exp.Expression) else v
                        for v in value
                    ],
                )
        return node

    reduce_outputs = []
    for e in select.expressions:
        name = e.alias_or_name or e.sql(dialect=dialect)
        inner = e.this if isinstance(e, exp.Alias) else e
        rebuilt = _rebuild(inner)
        leftover = [
            c.name for c in rebuilt.find_all(exp.Column) if c.name not in allowed_cols
        ]
        if leftover:
            return SplitResult(
                reason=(
                    f"SELECT expression {name!r} references {leftover[0]!r}, "
                    "which is neither aggregated nor in GROUP BY"
                )
            )
        reduce_outputs.append(exp.alias_(rebuilt, exp.to_identifier(name, quoted=True)))

    reduce_ = (
        exp.Select().select(*reduce_outputs).from_(exp.to_table(PARTIALS_PLACEHOLDER))
    )
    if group_exprs:
        reduce_.set(
            "group",
            exp.Group(expressions=[exp.column(a) for a in group_alias.values()]),
        )
    if select.args.get("order"):
        reduce_.set("order", _dequalify(select.args["order"]))
    if select.args.get("limit"):
        reduce_.set("limit", select.args["limit"].copy())
    return SplitResult(fragment=fragment, reduce=reduce_)
