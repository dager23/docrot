"""Span classification: which inline spans are checkable claims?

Precision rules distilled from the validation spike (see PLAN.md §5.2):

* bare single words are never symbol candidates (`timeout`, `id` noise);
* spans with spaces/shell metacharacters are commands or nothing;
* symbol-vs-path ties break symbol-first, except clear code-file
  extensions (``auth.py`` is a path, ``Response.json`` is a symbol —
  the engine still tries the path index as a fallback for the latter).
"""

from __future__ import annotations

import re

from docrot.model import RawSpan, Reference, RefKind

_DOTTED = re.compile(r"^~?[A-Za-z_]\w*(\.[A-Za-z_]\w*)+(\(\))?$")
_CALLFORM = re.compile(r"^[A-Za-z_]\w*\(\)$")
_PATHLIKE_EXT = (
    ".py",
    ".pyi",
    ".toml",
    ".cfg",
    ".ini",
    ".md",
    ".rst",
    ".txt",
    ".yml",
    ".yaml",
    ".json",
    ".lock",
    ".sh",
    ".ps1",
    ".env",
    ".csv",
)
#: Extensions that unambiguously mean "file", beating the dotted-symbol grammar.
_FILE_FIRST_EXT = (".py", ".pyi", ".toml", ".cfg", ".ini", ".yml", ".yaml", ".lock", ".sh")

_SHELL_META = re.compile(r"[|&;<>$*{}=\[\]]")

#: First tokens that make a multi-word span a command candidate.
KNOWN_RUNNERS = frozenset(
    {
        "make",
        "tox",
        "nox",
        "poe",
        "pdm",
        "npm",
        "pnpm",
        "yarn",
        "just",
        "python",
        "python3",
        "py",
        "pytest",
        "uv",
        "uvx",
        "pip",
        "pipx",
    }
)


def classify(span: RawSpan, entry_points: frozenset[str]) -> Reference | None:
    text = span.text.strip().strip("​")
    if not text or "\n" in text:
        return None

    first_token = text.split()[0]
    if " " in text:
        if first_token in KNOWN_RUNNERS or first_token in entry_points:
            return Reference(span, RefKind.COMMAND, text)
        return None

    if _SHELL_META.search(text):
        return None

    # Path-first for unambiguous file spans (parens mean regex/pseudo-code,
    # never a repo path).
    if ("/" in text or "\\" in text) and "(" not in text:
        norm = _normalize_path(text)
        if not norm or " " in norm:
            return None
        return Reference(span, RefKind.PATH, norm)
    if text.endswith(_FILE_FIRST_EXT) and not text.startswith("."):
        return Reference(span, RefKind.PATH, text)

    if span.context.ellipsis_after:
        # "start typing `typer.File`..." — an intentional prefix, not a claim
        return None
    if _DOTTED.match(text):
        return Reference(span, RefKind.SYMBOL_DOTTED, text.lstrip("~").removesuffix("()"))
    if _CALLFORM.match(text):
        return Reference(span, RefKind.SYMBOL_CALL, text.removesuffix("()"))

    if text.endswith(_PATHLIKE_EXT) and not text.startswith("."):
        return Reference(span, RefKind.PATH, text)

    if first_token in entry_points or first_token in KNOWN_RUNNERS:
        # single-word command like `pytest` — too weak to validate alone
        return None
    return None


def _normalize_path(text: str) -> str:
    """POSIX separators; strip only a leading `./` and trailing `/` —
    never dot-directories like `.github`."""
    norm = text.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm.rstrip("/")


def classify_command(span: RawSpan) -> Reference | None:
    text = " ".join(span.text.split())
    if not text:
        return None
    return Reference(span, RefKind.COMMAND, text)


def classify_link(span: RawSpan) -> Reference:
    return Reference(span, RefKind.LINK, _normalize_path(span.text))


def path_fallback_eligible(ref: Reference) -> bool:
    """Dotted symbols like ``package.json`` also get a path-index lookup."""
    return ref.kind == RefKind.SYMBOL_DOTTED and ref.target.rsplit(".", 1)[-1].lower() in {
        e.lstrip(".") for e in _PATHLIKE_EXT
    }
