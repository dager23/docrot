"""Temporal gate tests: drift vs. fiction on scripted git histories."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import GitRepo

from docrot.discovery import discover_command_sources
from docrot.model import RawSpan, Reference, RefKind
from docrot.temporal.gate import TemporalGate
from docrot.temporal.git import Git
from docrot.temporal.history import HistoricalResolver

DOC = Path("README.md")


def ref(target: str, kind: RefKind, line: int = 1) -> Reference:
    return Reference(RawSpan(DOC, line, 1, target), kind, target)


def make_gate(repo: GitRepo, pkg_dirs: dict[str, str]) -> TemporalGate:
    git = Git(repo.root)
    return TemporalGate(git, HistoricalResolver(git, pkg_dirs), discover_command_sources(repo.root))


class TestSymbolDrift:
    def test_confirmed_drift(self, git_repo: GitRepo) -> None:
        git_repo.commit(
            {
                "pkg/__init__.py": "from pkg.core import Mounts\n",
                "pkg/core.py": "class Mounts:\n    pass\n",
                "README.md": "Use `pkg.Mounts` for mounting.\n",
            },
            "add Mounts and document it",
        )
        git_repo.commit(
            {
                "pkg/__init__.py": "\n",
                "pkg/core.py": "class Client:\n    pass\n",
            },
            "remove Mounts",
        )
        gate = make_gate(git_repo, {"pkg": "pkg"})
        evidence = gate.examine(ref("pkg.Mounts", RefKind.SYMBOL_DOTTED), "pkg")
        assert evidence is not None
        assert evidence.resolved_at_introduction
        assert evidence.broken_since is not None

    def test_fiction_refuted(self, git_repo: GitRepo) -> None:
        git_repo.commit(
            {
                "pkg/__init__.py": "\n",
                "README.md": "Then call `pkg.imaginary_thing` (never existed).\n",
            },
            "docs reference something that never existed",
        )
        gate = make_gate(git_repo, {"pkg": "pkg"})
        evidence = gate.examine(ref("pkg.imaginary_thing", RefKind.SYMBOL_DOTTED), "pkg")
        assert evidence is not None
        assert not evidence.resolved_at_introduction

    def test_uncommitted_line_no_evidence(self, git_repo: GitRepo) -> None:
        git_repo.commit({"pkg/__init__.py": "\n", "README.md": "one line\n"}, "init")
        (git_repo.root / "README.md").write_text(
            "one line\nnew uncommitted `pkg.thing` mention\n", encoding="utf-8"
        )
        gate = make_gate(git_repo, {"pkg": "pkg"})
        assert gate.examine(ref("pkg.thing", RefKind.SYMBOL_DOTTED, line=2), "pkg") is None


class TestPathDrift:
    def test_removed_file_confirmed(self, git_repo: GitRepo) -> None:
        git_repo.commit(
            {
                "scripts/setup.sh": "echo hi\n",
                "README.md": "Run `scripts/setup.sh` first.\n",
            },
            "add script and doc",
        )
        git_repo.remove("scripts/setup.sh", message="drop setup script")
        gate = make_gate(git_repo, {})
        evidence = gate.examine(ref("scripts/setup.sh", RefKind.PATH), None)
        assert evidence is not None
        assert evidence.resolved_at_introduction
        assert evidence.broken_since is not None

    def test_reader_creates_file_fiction(self, git_repo: GitRepo) -> None:
        git_repo.commit({"README.md": "Create a file called `views.py` with:\n"}, "tutorial")
        gate = make_gate(git_repo, {})
        evidence = gate.examine(ref("views.py", RefKind.PATH), None)
        assert evidence is not None
        assert not evidence.resolved_at_introduction


class TestCommandDrift:
    def test_removed_make_target(self, git_repo: GitRepo) -> None:
        git_repo.commit(
            {
                "Makefile": "test:\n\techo t\nintegration-test:\n\techo i\n",
                "README.md": "Run `make integration-test` before merging.\n",
            },
            "makefile with target",
        )
        git_repo.commit({"Makefile": "test:\n\techo t\n"}, "drop integration-test target")
        gate = make_gate(git_repo, {})
        evidence = gate.examine(ref("make integration-test", RefKind.COMMAND), None)
        assert evidence is not None
        assert evidence.resolved_at_introduction
