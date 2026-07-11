"""Thin, memoized wrapper over the git CLI.

Subprocess git (rather than a binding like pygit2) keeps the dependency
surface at zero and works everywhere CI does. All calls are batched or
memoized: blame is fetched once per document, tree listings once per commit.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

_ZERO_SHA = "0" * 40


class GitError(RuntimeError):
    pass


class Git:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._blame_cache: dict[str, dict[int, tuple[str, str]]] = {}
        self._tree_cache: dict[str, frozenset[str]] = {}
        self._blob_cache: dict[tuple[str, str], str | None] = {}
        self._date_cache: dict[str, str] = {}
        self._available: bool | None = None

    # -- plumbing ---------------------------------------------------------

    def _run(self, *args: str, check: bool = True) -> str:
        try:
            proc = subprocess.run(
                ["git", "-c", "core.quotepath=false", *args],
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:  # git not installed
            raise GitError("git executable not found") from exc
        if check and proc.returncode != 0:
            raise GitError(proc.stderr.strip() or f"git {args[0]} failed")
        return proc.stdout

    @property
    def available(self) -> bool:
        """True when we are inside a git work tree with at least one commit."""
        if self._available is None:
            try:
                inside = self._run("rev-parse", "--is-inside-work-tree").strip() == "true"
                self._available = inside and bool(self._run("rev-parse", "HEAD", check=False))
                if self._available:
                    # a repo with zero commits has no HEAD
                    self._available = (
                        self._run("rev-parse", "--verify", "HEAD", check=False).strip() != ""
                    )
            except GitError:
                self._available = False
        return self._available

    @property
    def is_shallow(self) -> bool:
        try:
            return self._run("rev-parse", "--is-shallow-repository").strip() == "true"
        except GitError:
            return False

    # -- worktree state ----------------------------------------------------

    def ls_files(self) -> frozenset[str]:
        out = self._run("ls-files")
        return frozenset(line for line in out.splitlines() if line)

    def changed_files(self) -> frozenset[str]:
        """Files modified/added in the index or worktree relative to HEAD."""
        out = self._run("diff", "--name-only", "HEAD")
        staged = self._run("diff", "--name-only", "--cached")
        return frozenset(x for x in (out + staged).splitlines() if x)

    # -- history -----------------------------------------------------------

    def blame(self, doc: str) -> dict[int, tuple[str, str]]:
        """Map 1-based line number -> (commit sha, ISO date) for a document.

        Lines not yet committed map to ("", "").
        """
        if doc in self._blame_cache:
            return self._blame_cache[doc]
        result: dict[int, tuple[str, str]] = {}
        try:
            out = self._run("blame", "--line-porcelain", "--", doc)
        except GitError:
            self._blame_cache[doc] = result
            return result
        sha = ""
        date = ""
        lineno = 0
        for raw in out.splitlines():
            if raw.startswith("\t"):
                if sha and sha != _ZERO_SHA:
                    result[lineno] = (sha, date)
                else:
                    result[lineno] = ("", "")
                continue
            head, _, rest = raw.partition(" ")
            if len(head) == 40 and all(c in "0123456789abcdef" for c in head):
                sha = head
                parts = rest.split()
                if len(parts) >= 2:
                    lineno = int(parts[1])
            elif head == "author-time":
                ts = int(rest.strip())
                date = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        self._blame_cache[doc] = result
        return result

    def blob(self, commit: str, path: str) -> str | None:
        """File content at a commit, or None when absent."""
        key = (commit, path)
        if key in self._blob_cache:
            return self._blob_cache[key]
        proc = subprocess.run(
            ["git", "cat-file", "-p", f"{commit}:{path}"],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        content = proc.stdout if proc.returncode == 0 else None
        self._blob_cache[key] = content
        return content

    def exists_at(self, commit: str, path: str) -> bool:
        proc = subprocess.run(
            ["git", "cat-file", "-e", f"{commit}:{path}"],
            cwd=self.root,
            capture_output=True,
        )
        return proc.returncode == 0

    def tree_paths(self, commit: str) -> frozenset[str]:
        """All file paths in the tree at a commit (cached per commit)."""
        if commit in self._tree_cache:
            return self._tree_cache[commit]
        try:
            out = self._run("ls-tree", "-r", "--name-only", commit)
            paths = frozenset(line for line in out.splitlines() if line)
        except GitError:
            paths = frozenset()
        self._tree_cache[commit] = paths
        return paths

    def commit_date(self, sha: str) -> str:
        if sha in self._date_cache:
            return self._date_cache[sha]
        try:
            out = self._run("show", "-s", "--format=%cs", sha).strip()
        except GitError:
            out = ""
        self._date_cache[sha] = out
        return out

    def subtree_hash(self, commit: str, path: str) -> str | None:
        """Hash of a directory subtree at a commit — the natural cache key."""
        out = self._run("rev-parse", f"{commit}:{path}", check=False).strip()
        return out or None

    def grep_exists(self, fixed_string: str, commit: str, path: str) -> bool:
        """True when `fixed_string` occurs in any file under `path` at `commit`."""
        proc = subprocess.run(
            ["git", "grep", "-l", "--fixed-strings", fixed_string, commit, "--", path],
            cwd=self.root,
            capture_output=True,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())

    def first_parent_commits(self, since: str, path: str | None = None) -> list[str]:
        """Commits from `since` (exclusive) to HEAD, oldest first."""
        args = ["rev-list", "--first-parent", "--reverse", f"{since}..HEAD"]
        if path:
            args += ["--", path]
        try:
            out = self._run(*args)
        except GitError:
            return []
        return [line for line in out.splitlines() if line]
