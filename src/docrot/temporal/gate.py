"""The temporal gate: drift vs. fiction.

For each reference that Gate 2 (current-state resolution) marked BROKEN:

1. `git blame` the doc line → the commit C where the claim was made;
2. check whether the reference resolved at C (strict AST/blob evidence);
3. resolved-then + broken-now = drift (finding); never-resolved = fiction
   (skipped as UNKNOWN);
4. optionally bisect first-parent history for the earliest commit where
   resolution fails ("broken since"), for the report's provenance line.

Commands get the same treatment with runner files re-parsed at C.
"""

from __future__ import annotations

from pathlib import Path

from docrot.discovery import CommandSources
from docrot.model import CommitRef, Reference, RefKind, TemporalEvidence, Verdict
from docrot.resolve.commands import TaskInventory, check_command, inventory_from_texts
from docrot.temporal.git import Git
from docrot.temporal.history import HistoricalResolver


class TemporalGate:
    def __init__(
        self,
        git: Git,
        history: HistoricalResolver,
        sources: CommandSources,
        bisect: bool = True,
    ) -> None:
        self.git = git
        self.history = history
        self.sources = sources
        self.bisect = bisect
        self._inv_cache: dict[str, TaskInventory] = {}

    def _commit_ref(self, sha: str) -> CommitRef:
        return CommitRef(sha, self.git.commit_date(sha))

    def _introduction(self, ref: Reference) -> tuple[str, str] | None:
        blame = self.git.blame(ref.doc.as_posix())
        entry = blame.get(ref.line)
        if not entry or not entry[0]:
            return None
        return entry

    def _resolved_at(self, commit: str, ref: Reference) -> bool:
        if ref.kind == RefKind.COMMAND:
            return self._command_resolved_at(commit, ref)
        return self.history.resolved_at(commit, ref)

    def _command_resolved_at(self, commit: str, ref: Reference) -> bool:
        inv = self._inv_cache.get(commit)
        if inv is None:

            def rel(p: Path | None) -> str | None:
                if p is None:
                    return None
                try:
                    return p.relative_to(self.git.root).as_posix()
                except ValueError:
                    return None

            texts: dict[str, str | None] = {}
            for key, path in (
                ("makefile", self.sources.makefile),
                ("tox_ini", self.sources.tox_ini),
                ("noxfile", self.sources.noxfile),
                ("package_json", self.sources.package_json),
                ("justfile", self.sources.justfile),
                ("pyproject", self.sources.pyproject),
            ):
                rp = rel(path)
                texts[key] = self.git.blob(commit, rp) if rp else None
            inv = inventory_from_texts(texts)
            self._inv_cache[commit] = inv
        check = check_command(ref.target, inv, None, self.git.tree_paths(commit))
        return check.verdict is Verdict.RESOLVED

    def _broken_since(self, intro_sha: str, ref: Reference, scope: str | None) -> CommitRef | None:
        if not self.bisect:
            return None
        commits = self.git.first_parent_commits(intro_sha, scope)
        if not commits:
            return None
        # invariant: resolved at intro, broken at HEAD -> first failing commit
        lo, hi = 0, len(commits) - 1
        first_bad: str | None = None
        # verify the endpoint actually fails before bisecting a large range
        if self._resolved_at(commits[hi], ref):
            return None  # broken only in uncommitted state
        while lo <= hi:
            mid = (lo + hi) // 2
            if self._resolved_at(commits[mid], ref):
                lo = mid + 1
            else:
                first_bad = commits[mid]
                hi = mid - 1
        return self._commit_ref(first_bad) if first_bad else None

    def examine(self, ref: Reference, scope: str | None) -> TemporalEvidence | None:
        """Evidence for a currently-BROKEN reference, or None without history.

        `scope` narrows the bisection walk to commits touching a path
        (usually the owning package directory).
        """
        intro = self._introduction(ref)
        if intro is None:
            return None
        sha, date = intro
        resolved_then = self._resolved_at(sha, ref)
        evidence = TemporalEvidence(
            introduced_at=CommitRef(sha, date or self.git.commit_date(sha)),
            resolved_at_introduction=resolved_then,
        )
        if resolved_then:
            broken = self._broken_since(sha, ref, scope)
            if broken is not None:
                evidence = TemporalEvidence(
                    introduced_at=evidence.introduced_at,
                    resolved_at_introduction=True,
                    broken_since=broken,
                )
        return evidence
