"""Whitelist eligibility: anything not provably splittable is rejected.

The contract is conservative by construction — a False here is never a bug,
it is the library saying "run this single-node". Only queries whose
distributed execution is known to be equivalent to single-node execution
pass.
"""

from __future__ import annotations

from sqlglot import exp

SUPPORTED_AGGS = (exp.Sum, exp.Count, exp.Min, exp.Max, exp.Avg)

# Nondeterministic (or evaluation-time-dependent) functions: each fragment
# would observe its own value, diverging from any single evaluation.
VOLATILE_NODES = (
    exp.Rand,
    exp.Uuid,
    exp.CurrentTimestamp,
    exp.CurrentDate,
    exp.CurrentTime,
    exp.Localtime,
    exp.Localtimestamp,
)
VOLATILE_NAMES = {
    "now",
    "get_current_timestamp",
    "get_current_time",
    "random",
    "uuid",
    "today",
    "current_localtime",
    "current_localtimestamp",
    "transaction_timestamp",
}


def ineligibility_reason(select: exp.Expression) -> str | None:
    """Return why this statement cannot be split, or None if it can."""
    if not isinstance(select, exp.Select):
        return "only plain SELECT statements are supported"
    if select.args.get("with_"):
        return "CTEs are not supported"
    if select.args.get("joins"):
        return "joins are not supported"
    if select.args.get("having"):
        return "HAVING is not supported"
    if select.args.get("qualify"):
        return "QUALIFY is not supported"
    if select.args.get("offset"):
        return "OFFSET is not supported"

    distinct = select.args.get("distinct")
    if distinct is not None and distinct.args.get("on"):
        return "DISTINCT ON is not supported"

    limit = select.args.get("limit")
    if limit is not None:
        opts = limit.args.get("limit_options")
        if opts is not None and (opts.args.get("percent") or opts.args.get("with_ties")):
            return "percentage and WITH TIES limits are not supported"

    from_ = select.args.get("from_")
    if from_ is None:
        return "query has no FROM clause"
    source = from_.this
    if not isinstance(source, exp.Table) or not isinstance(source.this, exp.Identifier):
        return "FROM must be a single plain table"
    table_alias = source.args.get("alias")
    if table_alias is not None and table_alias.columns:
        return "table aliases with column lists are not supported"
    if select.find(exp.TableSample):
        return "USING SAMPLE is not supported"
    if select.find(exp.Collate):
        return "COLLATE is not supported"

    for node in select.walk():
        if node is select:
            continue
        if isinstance(node, (exp.Select, exp.Subquery)):
            return "subqueries are not supported"
        if isinstance(node, exp.Window):
            return "window functions are not supported"

    if select.find(exp.Filter):
        return "FILTER clauses on aggregates are not supported"

    for node in select.find_all(exp.Func):
        if isinstance(node, VOLATILE_NODES) or (
            isinstance(node, exp.Anonymous) and node.name.lower() in VOLATILE_NAMES
        ):
            name = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
            return f"volatile function {name} is not supported"

    aggs = list(select.find_all(exp.AggFunc))
    for agg in aggs:
        if not isinstance(agg, SUPPORTED_AGGS):
            return f"aggregate {agg.sql_name()} is not supported"
        if agg.find(exp.Distinct):
            return "DISTINCT aggregates are not supported"

    group = select.args.get("group")
    if group is not None:
        if not group.expressions:
            return "GROUP BY ALL is not supported"
        for g in group.expressions:
            if isinstance(g, exp.Literal):
                return "positional GROUP BY is not supported"

    if aggs and any(isinstance(e, exp.Star) for e in select.expressions):
        return "SELECT * cannot be combined with aggregates"

    return _order_by_reason(select, has_aggs=bool(aggs))


def _order_by_reason(select: exp.Select, has_aggs: bool) -> str | None:
    order = select.args.get("order")
    if order is None:
        return None

    has_star = any(isinstance(e, exp.Star) for e in select.expressions)
    output_names = {e.alias_or_name for e in select.expressions if e.alias_or_name}

    for ordered in order.expressions:
        key = ordered.this
        if isinstance(key, exp.Literal):
            continue  # positional — output order is preserved in the reduce
        if not isinstance(key, exp.Column):
            return "ORDER BY expressions must be output columns or positions"
        if has_star and not has_aggs:
            continue  # every source column survives into the partials
        if key.name not in output_names:
            return f"ORDER BY column {key.name!r} is not in the SELECT output"
    return None
