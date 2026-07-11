"""Anchors pack (opt-in): bind prose to a symbol's implementation hash.

    <!-- docrot:anchor symbol=pkg.mod.func hash=sha256:... -->
    ...explained prose...
    <!-- /docrot:anchor -->

The hash covers the AST-normalized symbol body (``ast.dump`` of the parsed
definition), so formatting and comment churn never invalidate an anchor —
only structural change does. Absent anchors assert nothing (the lesson from
the Serena review: optional metadata must have a neutral failure mode).
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from docrot.model import (
    Confidence,
    RawSpan,
    Reference,
    RefKind,
    Resolution,
    SpanContext,
    Verdict,
)

_ANCHOR = re.compile(
    r"<!--\s*docrot:anchor\s+symbol=(?P<symbol>[\w.]+)"
    r"(?:\s+hash=sha256:(?P<hash>[0-9a-f]{8,64}))?\s*-->"
)


@dataclass(frozen=True)
class Anchor:
    doc: Path
    line: int
    symbol: str
    hash: str | None  # None = freshly added, `docrot anchors update` fills it

    def to_reference(self) -> Reference:
        span = RawSpan(self.doc, self.line, 1, self.symbol, SpanContext())
        return Reference(span, RefKind.ANCHOR, self.symbol)


def parse_anchors(doc: Path, text: str) -> list[Anchor]:
    anchors = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in _ANCHOR.finditer(line):
            anchors.append(Anchor(doc, lineno, m.group("symbol"), m.group("hash")))
    return anchors


def _normalized_body_hash(source: str) -> str | None:
    """Hash of the AST-normalized first definition in `source`."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
    return None


def symbol_body_hash(filepath: Path, lineno: int, endlineno: int) -> str | None:
    """Hash the definition spanning [lineno, endlineno] in a source file."""
    try:
        lines = filepath.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    chunk = lines[lineno - 1 : endlineno]
    if not chunk:
        return None
    # dedent so ast.parse accepts nested definitions
    indent = len(chunk[0]) - len(chunk[0].lstrip())
    src = "\n".join(line[indent:] if len(line) >= indent else line for line in chunk)
    return _normalized_body_hash(src)


def check_anchor(
    anchor: Anchor,
    current_hash: str | None,
    resolvable: bool,
) -> Resolution:
    ref = anchor.to_reference()
    if not resolvable:
        return Resolution(ref, Verdict.BROKEN, boundary="anchor:target-missing")
    if anchor.hash is None or current_hash is None:
        return Resolution(ref, Verdict.UNKNOWN, boundary="anchor:no-hash")
    if current_hash.startswith(anchor.hash) or anchor.hash.startswith(
        current_hash[: len(anchor.hash)]
    ):
        return Resolution(ref, Verdict.RESOLVED, resolved_as=anchor.symbol)
    return Resolution(ref, Verdict.BROKEN, boundary="anchor:hash-mismatch")


def update_anchor_line(line: str, new_hash: str) -> str:
    """Rewrite the hash attribute in an anchor comment line."""

    def sub(m: re.Match[str]) -> str:
        return f"<!-- docrot:anchor symbol={m.group('symbol')} hash=sha256:{new_hash} -->"

    return _ANCHOR.sub(sub, line)


CONFIDENCE = Confidence.HIGH
