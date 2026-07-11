from __future__ import annotations

from docrot.extract.suppress import parse_suppressions


def test_same_line_ignore() -> None:
    s = parse_suppressions("bad `x.y` ref <!-- docrot: ignore -->\n")
    assert s.matches(1, "PY002")


def test_line_above_ignore() -> None:
    s = parse_suppressions("<!-- docrot: ignore -->\nbad `x.y` ref\n")
    assert s.matches(2, "PY002")
    assert not s.matches(3, "PY002")


def test_rule_filtered() -> None:
    s = parse_suppressions("ref <!-- docrot: ignore[PY002] -->\n")
    assert s.matches(1, "PY002")
    assert not s.matches(1, "PATH001")


def test_off_on_region() -> None:
    text = "a\n<!-- docrot: off -->\nb\nc\n<!-- docrot: on -->\nd\n"
    s = parse_suppressions(text)
    assert not s.matches(1, "PY002")
    assert s.matches(3, "PY002")
    assert s.matches(4, "PY002")
    assert not s.matches(6, "PY002")


def test_unclosed_off_runs_to_eof() -> None:
    s = parse_suppressions("a\n<!-- docrot: off -->\nb\nc\n")
    assert s.matches(4, "PY002")
