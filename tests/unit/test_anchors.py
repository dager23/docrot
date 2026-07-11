from __future__ import annotations

from pathlib import Path

from docrot.model import Verdict
from docrot.rules.anchors import (
    check_anchor,
    parse_anchors,
    symbol_body_hash,
    update_anchor_line,
)


def test_parse_and_roundtrip(tmp_path: Path) -> None:
    doc = Path("guide.md")
    text = (
        "<!-- docrot:anchor symbol=pkg.mod.func hash=sha256:aabbccdd -->\n"
        "prose\n<!-- /docrot:anchor -->\n"
    )
    anchors = parse_anchors(doc, text)
    assert len(anchors) == 1
    assert anchors[0].symbol == "pkg.mod.func"
    assert anchors[0].hash == "aabbccdd"

    line = text.splitlines()[0]
    updated = update_anchor_line(line, "f" * 64)
    assert "sha256:" + "f" * 64 in updated


def test_hash_ignores_formatting(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("def f(x):\n    return x + 1\n", encoding="utf-8")
    b.write_text("def f(x):\n    # a comment\n    return (x   +   1)\n", encoding="utf-8")
    ha = symbol_body_hash(a, 1, 2)
    hb = symbol_body_hash(b, 1, 3)
    assert ha is not None
    assert ha == hb  # comments and whitespace don't invalidate anchors


def test_hash_changes_on_structural_change(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    a.write_text("def f(x):\n    return x + 1\n", encoding="utf-8")
    before = symbol_body_hash(a, 1, 2)
    a.write_text("def f(x):\n    return x + 2\n", encoding="utf-8")
    after = symbol_body_hash(a, 1, 2)
    assert before != after


def test_check_verdicts() -> None:
    doc = Path("g.md")
    anchors = parse_anchors(doc, "<!-- docrot:anchor symbol=p.f hash=sha256:" + "a" * 16 + " -->\n")
    anchor = anchors[0]
    assert check_anchor(anchor, "a" * 16 + "bbb", True).verdict is Verdict.RESOLVED
    assert check_anchor(anchor, "c" * 20, True).verdict is Verdict.BROKEN
    assert check_anchor(anchor, None, False).verdict is Verdict.BROKEN
    assert check_anchor(anchor, None, True).verdict is Verdict.UNKNOWN
