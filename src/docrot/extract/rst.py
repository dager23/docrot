"""reStructuredText extraction (v1: pattern-level, not a docutils AST).

Covers the two high-precision constructs:

* ``inline literals``
* explicit roles — :py:func:`pkg.mod.f`, :class:`~pkg.X`, :file:`path`

Indented blocks (literal blocks, doctests, nested directives) are masked the
crude-but-safe way: any line indented 3+ spaces is skipped. Roles carry
explicit author intent, so rst extraction runs at high precision by design;
full docutils parsing is a post-v1 upgrade.
"""

from __future__ import annotations

import re
from pathlib import Path

from docrot.extract.markdown import Extracted
from docrot.model import RawSpan, SpanContext

_ROLE = re.compile(r":(?P<role>[a-zA-Z][\w.:-]*):`(?P<body>[^`<\n]+?)(?:\s*<[^>]*>)?`")
_LITERAL = re.compile(r"(?<!`)``(?P<body>[^`\n]+)``(?!`)")
_INDENTED = re.compile(r"^(?:\s{3,}|\t)")

#: Roles whose body names a Python object.
_SYMBOL_ROLES = {
    "py:func",
    "py:meth",
    "py:class",
    "py:mod",
    "py:attr",
    "py:obj",
    "py:data",
    "py:exc",
    "func",
    "meth",
    "class",
    "mod",
    "attr",
    "obj",
    "data",
    "exc",
    "currentmodule",
}
_FILE_ROLES = {"file"}


def extract_rst(doc: Path, text: str, in_agent_file: bool) -> Extracted:
    inline_spans: list[RawSpan] = []
    lines = text.splitlines()
    in_directive_gap = False

    for lineno0, line in enumerate(lines):
        if _INDENTED.match(line):
            continue
        if not line.strip():
            in_directive_gap = False
            continue
        if line.lstrip().startswith(".."):  # directive/comment line
            in_directive_gap = True
            continue
        if in_directive_gap:
            continue
        ctx = SpanContext((), in_agent_file)

        consumed: list[tuple[int, int]] = []
        for m in _ROLE.finditer(line):
            body = m.group("body").strip().lstrip("~!")
            role = m.group("role").lower()
            if role in _SYMBOL_ROLES or role in _FILE_ROLES:
                inline_spans.append(RawSpan(doc, lineno0 + 1, m.start("body") + 1, body, ctx))
            consumed.append((m.start(), m.end()))

        for m in _LITERAL.finditer(line):
            if any(s <= m.start() < e for s, e in consumed):
                continue
            body = m.group("body").strip()
            if body:
                inline_spans.append(RawSpan(doc, lineno0 + 1, m.start("body") + 1, body, ctx))

    return Extracted(inline_spans, [], [])
