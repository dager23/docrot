from __future__ import annotations

import textwrap
from pathlib import Path

from docrot.extract.markdown import extract_markdown

DOC = Path("README.md")


def extract(text: str):
    return extract_markdown(DOC, textwrap.dedent(text), in_agent_file=False)


def test_inline_spans_with_positions() -> None:
    out = extract(
        """\
        # Title

        Use `pkg.mod.func` to start.
        """
    )
    assert len(out.inline_spans) == 1
    span = out.inline_spans[0]
    assert span.text == "pkg.mod.func"
    assert span.line == 3
    assert span.col == 6


def test_fenced_python_blocks_not_scanned_for_symbols() -> None:
    out = extract(
        """\
        ```python
        from pkg import gone_symbol
        `looks.like.inline` inside a fence
        ```
        """
    )
    assert out.inline_spans == []


def test_shell_fences_become_commands() -> None:
    out = extract(
        """\
        ```bash
        $ make test
        tox -e lint
        # a comment
        ```
        """
    )
    cmds = [c.text for c in out.command_lines]
    assert cmds == ["make test", "tox -e lint"]


def test_console_output_lines_skipped() -> None:
    out = extract(
        """\
        ```console
        $ pytest tests/test_app.py
            2 passed in 0.01s
        ```
        """
    )
    assert [c.text for c in out.command_lines] == ["pytest tests/test_app.py"]


def test_relative_links_collected_urls_ignored() -> None:
    out = extract(
        """\
        See [the guide](docs/guide.md) and [site](https://example.com)
        and [section](#anchor).
        """
    )
    assert [x.text for x in out.link_targets] == ["docs/guide.md"]


def test_untagged_fences_are_not_commands() -> None:
    out = extract(
        """\
        ```
        make not-a-real-claim
        ```
        """
    )
    assert out.command_lines == []


def test_heading_context_tracked() -> None:
    out = extract(
        """\
        # Setup

        ## Install

        Run `make install` now.
        """
    )
    # inline `make install` stays an inline span; the classifier routes it
    # to COMMAND later. Heading context must reflect the enclosing section.
    assert len(out.inline_spans) == 1
    assert out.inline_spans[0].context.heading_path == ("Setup", "Install")


def test_multiple_spans_one_line_distinct_columns() -> None:
    out = extract("Call `foo.bar` then `foo.baz` here.\n")
    cols = [s.col for s in out.inline_spans]
    assert cols == sorted(cols)
    assert len(set(cols)) == 2
