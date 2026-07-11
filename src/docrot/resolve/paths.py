"""Path reference resolution against the tracked file tree.

Resolution order (PLAN.md §5.3):

1. exact repo-relative match (against git-tracked files, so ignored junk
   can't mask drift);
2. unique-or-any basename match elsewhere → RESOLVED, noted as
   "resolves by basename" (PATH002 material);
3. globs and placeholder-looking targets → UNKNOWN;
4. plain misses → BROKEN candidate for the temporal gate.
"""

from __future__ import annotations

import re
from collections import defaultdict

from docrot.model import Reference, Resolution, Verdict

_PLACEHOLDER = re.compile(r"[*<>{}]|\byour|\bmy_|\bexample\b|\bpath/to\b", re.IGNORECASE)


class PathResolver:
    def __init__(self, tracked: frozenset[str]) -> None:
        self.tracked = tracked
        self._by_basename: dict[str, list[str]] = defaultdict(list)
        for p in tracked:
            self._by_basename[p.rsplit("/", 1)[-1]].append(p)
        self._dirs: set[str] = set()
        for p in tracked:
            parts = p.split("/")
            for i in range(1, len(parts)):
                self._dirs.add("/".join(parts[:i]))

    def resolve(self, ref: Reference) -> Resolution:
        target = ref.target.rstrip("/")
        if not target:
            return Resolution(ref, Verdict.UNKNOWN, boundary="empty")
        if _PLACEHOLDER.search(target):
            return Resolution(ref, Verdict.UNKNOWN, boundary="placeholder")

        if target in self.tracked or target in self._dirs:
            return Resolution(ref, Verdict.RESOLVED, resolved_as=target)

        basename = target.rsplit("/", 1)[-1]
        matches = self._by_basename.get(basename, [])
        if matches:
            note = "basename" if "/" in target else "bare-basename"
            return Resolution(ref, Verdict.RESOLVED, resolved_as=matches[0], boundary=note)

        if "/" not in target:
            # Bare filename that matches nothing: could be a file the
            # *reader* is told to create. Temporal gate decides.
            return Resolution(ref, Verdict.BROKEN, boundary="bare")
        return Resolution(ref, Verdict.BROKEN)
