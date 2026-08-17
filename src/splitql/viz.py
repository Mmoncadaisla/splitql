"""Render a Plan as an execution graph: Graphviz DOT or a self-contained
interactive HTML page (no external assets, works offline, light/dark aware).

The visual vocabulary is per-worker cards — files, bytes, share bar,
expandable SQL — flowing scan -> partials -> reduce -> result.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .ir import Plan


def _human_bytes(n: int | None) -> str:
    if n is None:
        return "size unknown"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{n} B"


def to_dot(plan: Plan) -> str:
    """Graphviz DOT of the planned execution."""
    if not plan.eligible:
        reason = (plan.reason or "ineligible").replace('"', "'")
        return f'digraph splitql {{ ineligible [label="{reason}", shape=box]; }}'
    lines = [
        "digraph splitql {",
        "  rankdir=LR;",
        '  node [shape=box, fontname="Helvetica"];',
        f'  partials [label="{plan.partials_table}\\n(concat of {plan.workers} partials)"];',
        '  reduce [label="reduce"];',
        '  result [label="result", shape=oval];',
    ]
    for i, files in enumerate(plan.fragment_files or [[]] * plan.workers):
        total = sum(f.size_bytes or 0 for f in files) if files else None
        label = f"fragment {i}\\n{len(files)} files"
        if total:
            label += f" · {_human_bytes(total)}"
        lines.append(f'  f{i} [label="{label}"];')
        lines.append(f"  f{i} -> partials;")
    lines += ["  partials -> reduce;", "  reduce -> result;", "}"]
    return "\n".join(lines)


def to_html(plan: Plan) -> str:
    """Self-contained interactive HTML page for the planned execution."""
    if not plan.eligible:
        body = (
            '<div class="card ineligible"><h2>Not splittable</h2>'
            f"<p>{html.escape(plan.reason or 'unknown reason')}</p>"
            "<p>Run the original query single-node.</p></div>"
        )
        return _PAGE.replace("__STATS__", "").replace("__GRAPH__", body)

    groups = plan.fragment_files or [[] for _ in plan.fragments]
    sizes = [sum(f.size_bytes or 0 for f in g) for g in groups]
    total_bytes = sum(sizes)
    total_files = sum(len(g) for g in groups)
    max_size = max(sizes) if any(sizes) else 0

    cards = []
    for i, (sql, group) in enumerate(zip(plan.fragments, groups)):
        share = f"{100 * sizes[i] / total_bytes:.0f}%" if total_bytes else "?"
        bar_w = f"{100 * sizes[i] / max_size:.0f}%" if max_size else "0%"
        file_list = "".join(
            f"<li><code>{html.escape(f.path)}</code>"
            f" <span class=dim>{_human_bytes(f.size_bytes)}</span></li>"
            for f in group
        )
        cards.append(
            f"""<div class="card fragment" data-node="f{i}">
  <div class=cardhead><span class=badge>fragment {i}</span>
    <span class=dim>{len(group)} files · {_human_bytes(sizes[i]) if sizes[i] else 'size unknown'} · {share} of scan</span></div>
  <div class=bar><div class=fill style="width:{bar_w}"></div></div>
  <details><summary>SQL</summary><pre>{html.escape(sql)}</pre>
    <button class=copy data-sql="{html.escape(sql, quote=True)}">copy</button></details>
  <details><summary>files</summary><ul class=files>{file_list or '<li class=dim>none</li>'}</ul></details>
</div>"""
        )

    query_block = (
        f"<details open><summary>input query</summary><pre>{html.escape(plan.query)}</pre></details>"
        if plan.query
        else ""
    )
    warnings = "".join(
        f'<div class="warn">⚠ {html.escape(w)}</div>' for w in plan.warnings
    )
    stats = (
        f"{query_block}{warnings}"
        f'<div class=statsrow><span class=stat><b>{plan.workers}</b> fragments</span>'
        f"<span class=stat><b>{total_files}</b> files</span>"
        + (
            f"<span class=stat><b>{len(plan.pruned_files)}</b> files pruned by stats</span>"
            if plan.pruned_files
            else ""
        )
        + f"<span class=stat><b>{_human_bytes(total_bytes) if total_bytes else '?'}</b> scanned</span>"
        f"<span class=stat>partials table <b><code>{html.escape(plan.partials_table)}</code></b></span></div>"
    )
    graph = f"""<div class=flow>
  <div class=stage><h3>scan + partial</h3>{''.join(cards)}</div>
  <div class=arrow>→</div>
  <div class=stage><h3>gather</h3>
    <div class="card partials"><span class=badge>{html.escape(plan.partials_table)}</span>
      <p class=dim>concatenation of {plan.workers} fragment results<br>(any transport: Arrow, files, UNION ALL)</p></div></div>
  <div class=arrow>→</div>
  <div class=stage><h3>reduce</h3>
    <div class="card reduce"><span class=badge>reduce</span>
      <details open><summary>SQL</summary><pre>{html.escape(plan.reduce or '')}</pre>
      <button class=copy data-sql="{html.escape(plan.reduce or '', quote=True)}">copy</button></details></div></div>
  <div class=arrow>→</div>
  <div class=stage><h3>result</h3><div class="card result">✓ identical to single-node</div></div>
</div>"""
    return _PAGE.replace("__STATS__", stats).replace("__GRAPH__", graph)


_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>splitql plan</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root { --bg:#f7f7f5; --card:#ffffff; --ink:#1a1a18; --dim:#6b6b66; --line:#d9d9d4;
        --accent:#0b6e4f; --accent-soft:#e3f0ea; --warn-bg:#fdf3d7; --warn-ink:#7a5b00; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#161614; --card:#20201d; --ink:#e8e8e3; --dim:#98988f; --line:#3a3a35;
          --accent:#4dbd94; --accent-soft:#1d3229; --warn-bg:#332a10; --warn-ink:#e3c56b; } }
* { box-sizing:border-box; }
body { margin:0; padding:24px; background:var(--bg); color:var(--ink);
       font:14px/1.5 -apple-system, "Segoe UI", sans-serif; }
h1 { font-size:18px; margin:0 0 4px; } h3 { font-size:12px; text-transform:uppercase;
     letter-spacing:.06em; color:var(--dim); margin:0 0 8px; }
.dim { color:var(--dim); font-size:12px; }
.statsrow { display:flex; gap:16px; flex-wrap:wrap; margin:12px 0 20px; }
.stat { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:6px 12px; }
.flow { display:flex; align-items:flex-start; gap:8px; overflow-x:auto; padding-bottom:12px; }
.stage { min-width:230px; max-width:380px; flex:1; }
.arrow { align-self:center; color:var(--dim); font-size:20px; padding:0 2px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
        padding:10px 12px; margin-bottom:10px; }
.card.fragment:hover, .card.reduce:hover { border-color:var(--accent); }
.cardhead { display:flex; flex-direction:column; gap:2px; margin-bottom:6px; }
.badge { display:inline-block; background:var(--accent-soft); color:var(--accent);
         border-radius:6px; padding:1px 8px; font-size:12px; font-weight:600; width:fit-content; }
.bar { height:4px; background:var(--line); border-radius:2px; margin:4px 0 8px; }
.fill { height:100%; background:var(--accent); border-radius:2px; }
pre { background:var(--bg); border:1px solid var(--line); border-radius:6px;
      padding:8px; overflow-x:auto; font-size:12px; white-space:pre-wrap; }
details summary { cursor:pointer; color:var(--dim); font-size:12px; }
ul.files { margin:6px 0; padding-left:18px; font-size:12px; }
button.copy { font-size:11px; border:1px solid var(--line); background:var(--card);
              color:var(--dim); border-radius:6px; padding:2px 8px; cursor:pointer; }
button.copy:hover { color:var(--accent); border-color:var(--accent); }
.warn { background:var(--warn-bg); color:var(--warn-ink); border-radius:8px;
        padding:6px 12px; margin:8px 0; font-size:13px; }
.card.result { border-color:var(--accent); color:var(--accent); font-weight:600; }
.card.ineligible { border-color:#c0392b; }
</style></head>
<body>
<h1>splitql · planned execution</h1>
__STATS__
__GRAPH__
<script>
document.querySelectorAll("button.copy").forEach(b => b.addEventListener("click", () => {
  navigator.clipboard.writeText(b.dataset.sql).then(() => {
    b.textContent = "copied"; setTimeout(() => b.textContent = "copy", 1200);
  });
}));
</script>
</body></html>
"""
