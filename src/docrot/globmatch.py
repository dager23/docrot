"""Glob matching with real ``**`` semantics (fnmatch treats ``*`` as
crossing ``/`` and has no ``**``, which silently breaks patterns like
``**/*.md`` on root-level files).

Rules: ``**/`` matches zero or more directories, ``*``/``?`` never cross
``/``. Compiled patterns are cached.
"""

from __future__ import annotations

import functools
import re


@functools.lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out) + r"\Z")


def glob_match(path: str, pattern: str) -> bool:
    return _compile(pattern).match(path) is not None


def any_glob_match(path: str, patterns: tuple[str, ...]) -> bool:
    return any(glob_match(path, p) for p in patterns)
