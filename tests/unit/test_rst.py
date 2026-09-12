"""reStructuredText extraction: roles and inline literals, not code blocks."""

from __future__ import annotations

import textwrap
from pathlib import Path

from docrot.extract.rst import extract_rst

DOC = Path("docs/api.rst")


def targets(text: str) -> list[str]:
    return [s.text for s in extract_rst(DOC, textwrap.dedent(text), False).inline_spans]


def test_python_roles_are_extracted() -> None:
    assert targets("See :py:func:`pkg.mod.run` for details.\n") == ["pkg.mod.run"]


def test_short_roles_are_extracted() -> None:
    assert targets("Use :meth:`pkg.Klass.go` now.\n") == ["pkg.Klass.go"]


def test_tilde_prefix_is_stripped() -> None:
    assert targets("See :class:`~pkg.mod.Klass`.\n") == ["pkg.mod.Klass"]


def test_explicit_title_target_is_used() -> None:
    # :ref:`friendly text <actual.target>` — the target is what matters
    assert targets("See :meth:`the method <pkg.Klass.go>`.\n") == ["pkg.Klass.go"]


def test_inline_literals_are_extracted() -> None:
    assert targets("Set ``pkg.CONSTANT`` before start.\n") == ["pkg.CONSTANT"]


def test_indented_literal_blocks_are_skipped() -> None:
    assert (
        targets(
            """\
            Example::

                from pkg import ``not_a_reference``
                value = pkg.internal_thing
            """
        )
        == []
    )


def test_directive_bodies_are_skipped() -> None:
    assert (
        targets(
            """\
            .. autoclass:: pkg.mod.Klass
               :members:
            """
        )
        == []
    )


def test_line_numbers_are_one_based() -> None:
    spans = extract_rst(DOC, "first line\n\nsee :func:`pkg.go` here\n", False).inline_spans
    assert spans[0].line == 3


def test_column_points_at_the_target() -> None:
    line = "see :func:`pkg.go` here"
    span = extract_rst(DOC, line + "\n", False).inline_spans[0]
    assert line[span.col - 1 :].startswith("pkg.go")


def test_unknown_roles_are_ignored() -> None:
    assert targets("See :download:`some/file.zip` here.\n") == []


def test_rst_yields_no_commands_or_links() -> None:
    out = extract_rst(DOC, "Run ``make test`` now.\n", False)
    assert out.command_lines == []
    assert out.link_targets == []
