"""Markdown extraction.

markdown-it-py supplies structure (fenced/indented code regions, links,
headings); inline code spans are then located by regex on the *unmasked*
lines so every span carries an exact line and column. Fenced blocks are
never scanned for symbol references — except shell-flavored fences, whose
lines become command candidates ("run this" is a claim about the repo).
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from pathlib import Path

from markdown_it import MarkdownIt

from docrot.model import RawSpan, SpanContext

SHELL_LANGS = {"bash", "sh", "shell", "console", "zsh", "shell-session"}

_INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)(?P<body>.+?)(?<!`)\1(?!`)")
_URL_LIKE = re.compile(r"^[a-z][a-z0-9+.-]*:")  # http:, mailto:, etc.
_PROMPT = re.compile(r"^\s*(?:\$|%)\s+")


@dataclass(frozen=True)
class Extracted:
    inline_spans: list[RawSpan]
    command_lines: list[RawSpan]  # from shell fences and $-prefixed inline spans
    link_targets: list[RawSpan]  # relative link hrefs


_MD = MarkdownIt("commonmark").enable("table")


class _Headings:
    """Line-indexed heading context: which section is line N in?"""

    def __init__(self) -> None:
        self._starts: list[int] = []
        self._paths: list[tuple[str, ...]] = []
        self._stack: list[tuple[int, str]] = []

    def push(self, line0: int, level: int, title: str) -> None:
        while self._stack and self._stack[-1][0] >= level:
            self._stack.pop()
        self._stack.append((level, title))
        self._starts.append(line0)
        self._paths.append(tuple(t for _, t in self._stack))

    def at(self, line0: int) -> tuple[str, ...]:
        idx = bisect.bisect_right(self._starts, line0) - 1
        return self._paths[idx] if idx >= 0 else ()


def extract_markdown(doc: Path, text: str, in_agent_file: bool) -> Extracted:
    tokens = _MD.parse(text)
    lines = text.splitlines()
    masked = [False] * (len(lines) + 2)

    inline_spans: list[RawSpan] = []
    command_lines: list[RawSpan] = []
    link_targets: list[RawSpan] = []
    headings = _Headings()

    for i, tok in enumerate(tokens):
        if tok.type in ("fence", "code_block") and tok.map:
            start, end = tok.map
            for ln in range(start, min(end, len(lines))):
                masked[ln] = True
            if tok.type == "fence" and tok.info:
                lang = tok.info.strip().split()[0].lower()
                if lang in SHELL_LANGS:
                    ctx = SpanContext(headings.at(start), in_agent_file, fence_lang=lang)
                    body_start = start + 1  # line after the opening fence
                    for off, raw in enumerate(tok.content.splitlines()):
                        cmd = _PROMPT.sub("", raw).strip()
                        if not cmd or cmd.startswith("#") or raw != raw.lstrip():
                            # indented lines inside console blocks are output
                            continue
                        command_lines.append(RawSpan(doc, body_start + off + 1, 1, cmd, ctx))
        elif tok.type == "html_block" and tok.map:
            for ln in range(tok.map[0], min(tok.map[1], len(lines))):
                masked[ln] = True
        elif tok.type == "heading_open" and tok.map:
            title = tokens[i + 1].content if i + 1 < len(tokens) else ""
            headings.push(tok.map[0], int(tok.tag[1]), title)
        elif tok.type == "inline" and tok.children and tok.map:
            for child in tok.children:
                if child.type == "link_open":
                    href = str(child.attrGet("href") or "")
                    target = href.split("#", 1)[0]
                    if target and not _URL_LIKE.match(target) and not target.startswith("#"):
                        link_targets.append(
                            RawSpan(
                                doc,
                                tok.map[0] + 1,
                                1,
                                target,
                                SpanContext(headings.at(tok.map[0]), in_agent_file),
                            )
                        )

    # Inline code spans, located precisely on unmasked source lines.
    for lineno0, line in enumerate(lines):
        if masked[lineno0]:
            continue
        base_ctx = SpanContext(headings.at(lineno0), in_agent_file)
        for m in _INLINE_CODE.finditer(line):
            body = m.group("body").strip()
            if not body:
                continue
            following = line[m.end() :].lstrip("`")
            ctx = base_ctx
            if following.startswith(("...", "…")):
                ctx = SpanContext(
                    base_ctx.heading_path, base_ctx.in_agent_file, ellipsis_after=True
                )
            if _PROMPT.match(body):
                command_lines.append(
                    RawSpan(doc, lineno0 + 1, m.start("body") + 1, _PROMPT.sub("", body), ctx)
                )
            else:
                inline_spans.append(RawSpan(doc, lineno0 + 1, m.start("body") + 1, body, ctx))

    return Extracted(inline_spans, command_lines, link_targets)
