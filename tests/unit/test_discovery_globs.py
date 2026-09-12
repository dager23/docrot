"""Doc/package discovery and glob semantics."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import GitRepo

from docrot.config import Config, load_config
from docrot.discovery import (
    discover_command_sources,
    discover_docs,
    discover_packages,
    is_agent_file,
)
from docrot.globmatch import glob_match
from docrot.temporal.git import Git


class TestGlobMatch:
    def test_star_does_not_cross_directories(self) -> None:
        assert glob_match("a.md", "*.md")
        assert not glob_match("docs/a.md", "*.md")

    def test_double_star_matches_zero_or_more_dirs(self) -> None:
        # the fnmatch trap: "**/*.md" must match a root-level file too
        assert glob_match("a.md", "**/*.md")
        assert glob_match("docs/a.md", "**/*.md")
        assert glob_match("docs/deep/a.md", "**/*.md")

    def test_question_mark_is_single_char(self) -> None:
        assert glob_match("a.md", "?.md")
        assert not glob_match("ab.md", "?.md")

    def test_trailing_double_star(self) -> None:
        assert glob_match("docs/archive/old.md", "docs/archive/**")

    def test_literal_dots_are_escaped(self) -> None:
        assert not glob_match("axmd", "a.md")


class TestAgentFiles:
    def test_known_names(self) -> None:
        assert is_agent_file("CLAUDE.md")
        assert is_agent_file("AGENTS.md")
        assert is_agent_file(".cursorrules")

    def test_nested_agent_globs(self) -> None:
        assert is_agent_file(".github/copilot-instructions.md")
        assert is_agent_file(".claude/agents/reviewer.md")

    def test_ordinary_docs_are_not_agent_files(self) -> None:
        assert not is_agent_file("README.md")
        assert not is_agent_file("docs/CLAUDE_NOTES.md")


class TestDocDiscovery:
    def test_historical_docs_are_skipped(self, git_repo: GitRepo) -> None:
        git_repo.commit(
            {
                "README.md": "x\n",
                "CHANGELOG.md": "x\n",
                "MIGRATION.md": "x\n",
                "UPGRADING.rst": "x\n",
                "docs/guide.md": "x\n",
            },
            "docs of several kinds",
        )
        cfg = load_config(git_repo.root)
        found = discover_docs(cfg, Git(git_repo.root))
        assert "README.md" in found
        assert "docs/guide.md" in found
        assert "CHANGELOG.md" not in found
        assert "MIGRATION.md" not in found
        assert "UPGRADING.rst" not in found

    def test_untracked_agent_file_included_ordinary_doc_not(self, git_repo: GitRepo) -> None:
        git_repo.commit({"README.md": "x\n"}, "init")
        (git_repo.root / "CLAUDE.md").write_text("x\n", encoding="utf-8")
        (git_repo.root / "loose.md").write_text("x\n", encoding="utf-8")
        found = discover_docs(load_config(git_repo.root), Git(git_repo.root))
        assert "CLAUDE.md" in found
        assert "loose.md" not in found

    def test_exclude_docs_config(self, git_repo: GitRepo) -> None:
        git_repo.commit({"docs/a.md": "x\n", "docs/archive/b.md": "x\n"}, "init")
        cfg = Config(root=git_repo.root, exclude_docs=("docs/archive/**",))
        found = discover_docs(cfg, Git(git_repo.root))
        assert "docs/a.md" in found
        assert "docs/archive/b.md" not in found

    def test_works_without_git(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("x\n", encoding="utf-8")
        found = discover_docs(load_config(tmp_path), Git(tmp_path))
        assert found == ["README.md"]


class TestPackageDiscovery:
    def test_src_layout(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "mypkg").mkdir(parents=True)
        (tmp_path / "src" / "mypkg" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'mypkg'\nversion = '0'\n", encoding="utf-8"
        )
        roots = discover_packages(load_config(tmp_path))
        assert [r.name for r in roots] == ["mypkg"]

    def test_hyphenated_project_name_maps_to_underscore(self, tmp_path: Path) -> None:
        (tmp_path / "my_pkg").mkdir()
        (tmp_path / "my_pkg" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'my-pkg'\nversion = '0'\n", encoding="utf-8"
        )
        assert [r.name for r in discover_packages(load_config(tmp_path))] == ["my_pkg"]

    def test_fallback_scan_skips_support_dirs(self, tmp_path: Path) -> None:
        for name in ("realpkg", "tests", "docs"):
            (tmp_path / name).mkdir()
            (tmp_path / name / "__init__.py").write_text("", encoding="utf-8")
        assert [r.name for r in discover_packages(load_config(tmp_path))] == ["realpkg"]

    def test_no_package_returns_empty(self, tmp_path: Path) -> None:
        assert discover_packages(load_config(tmp_path)) == []


class TestCommandSources:
    def test_detects_present_runners_only(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("test:\n\techo\n", encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        sources = discover_command_sources(tmp_path)
        assert sources.makefile is not None
        assert sources.pyproject is not None
        assert sources.tox_ini is None
        assert sources.package_json is None
