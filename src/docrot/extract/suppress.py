"""Inline suppression directives.

    <!-- docrot: ignore -->            suppress findings on this/next line
    <!-- docrot: ignore[PY002] -->     rule-filtered variant
    <!-- docrot: off --> ... <!-- docrot: on -->   region suppression

Suppressions are *counted*, never silent: the JSON report carries every
suppressed finding with ``suppressed: true`` (ruff-style accounting).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_DIRECTIVE = re.compile(
    r"<!--\s*docrot:\s*(?P<kind>ignore|off|on)\s*(?:\[(?P<rules>[A-Z0-9, ]+)\])?\s*-->"
)


@dataclass(frozen=True)
class Suppressions:
    line_rules: dict[int, frozenset[str]]  # line -> rules ('*' = all)
    off_regions: tuple[tuple[int, int], ...]  # inclusive line ranges

    def matches(self, line: int, rule: str) -> bool:
        for start, end in self.off_regions:
            if start <= line <= end:
                return True
        # `ignore` on line N covers N and N+1 (comment-above style)
        for covered in (line, line - 1):
            rules = self.line_rules.get(covered)
            if rules is not None and ("*" in rules or rule in rules):
                return True
        return False


def parse_suppressions(text: str) -> Suppressions:
    line_rules: dict[int, frozenset[str]] = {}
    regions: list[tuple[int, int]] = []
    off_since: int | None = None
    total = 0
    for lineno, line in enumerate(text.splitlines(), start=1):
        total = lineno
        for m in _DIRECTIVE.finditer(line):
            kind = m.group("kind")
            if kind == "ignore":
                rules = m.group("rules")
                parsed = (
                    frozenset(r.strip().upper() for r in rules.split(",") if r.strip())
                    if rules
                    else frozenset({"*"})
                )
                existing = line_rules.get(lineno, frozenset())
                line_rules[lineno] = existing | parsed
            elif kind == "off" and off_since is None:
                off_since = lineno
            elif kind == "on" and off_since is not None:
                regions.append((off_since, lineno))
                off_since = None
    if off_since is not None:
        regions.append((off_since, total or off_since))
    return Suppressions(line_rules=line_rules, off_regions=tuple(regions))
