"""Public result type: the whole library is a pure function into this."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .sources import DataFile


@dataclass
class Plan:
    """Result of planning a query split.

    When ``eligible`` is False, ``reason`` says why and the caller must run
    the original query on a single node. When True, run each fragment
    anywhere, concatenate their results into a relation named after
    ``partials_table`` (column-compatible by construction), and run
    ``reduce`` over it to get the final result.
    """

    eligible: bool
    fragments: list[str] = field(default_factory=list)
    reduce: str | None = None
    reason: str | None = None
    partials_table: str = "partials"
    warnings: list[str] = field(default_factory=list)
    query: str | None = None
    fragment_files: list[list["DataFile"]] = field(default_factory=list)

    @property
    def workers(self) -> int:
        return len(self.fragments)

    def to_dict(self) -> dict:
        return {
            "eligible": self.eligible,
            "fragments": self.fragments,
            "reduce": self.reduce,
            "reason": self.reason,
            "partials_table": self.partials_table,
            "warnings": self.warnings,
            "query": self.query,
            "fragment_files": [
                [{"path": f.path, "size_bytes": f.size_bytes} for f in group]
                for group in self.fragment_files
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    def to_dot(self) -> str:
        from .viz import to_dot

        return to_dot(self)

    def to_html(self) -> str:
        from .viz import to_html

        return to_html(self)


def ineligible(reason: str) -> Plan:
    return Plan(eligible=False, reason=reason)
