# Visualize the plan

```python
open("plan.html", "w").write(p.to_html())  # self-contained interactive page
print(p.to_dot())                          # Graphviz
```

The HTML page shows the full execution graph — per-fragment cards with file
lists, byte shares and expandable SQL, flowing scan → partials → reduce →
result — with no external assets (works offline, light/dark aware).

`p.to_json()` gives the whole plan as a JSON envelope for non-Python callers.
