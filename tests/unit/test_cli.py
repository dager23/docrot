"""CLI dispatch, flags, and exit codes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import GitRepo

from docrot.cli import main


@pytest.fixture
def repo(git_repo: GitRepo) -> GitRepo:
    git_repo.commit(
        {
            "pyproject.toml": "[project]\nname = 'demo'\nversion = '0'\n",
            "demo/__init__.py": "from demo.core import Widget\n",
            "demo/core.py": "class Widget:\n    pass\n",
            "README.md": ("Use `demo.Widget` and `demo.Ghost`.\nExternal `numpy.array` too.\n"),
        },
        "one good reference, one that never existed, one external",
    )
    return git_repo


class TestDispatch:
    def test_bare_invocation_defaults_to_check(
        self, repo: GitRepo, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(repo.root)
        code = main([])
        assert code == 1
        assert "references" in capsys.readouterr().out

    def test_leading_flag_implies_check(
        self, repo: GitRepo, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(repo.root)
        main(["--format", "json"])
        assert json.loads(capsys.readouterr().out)["schema"] == 1

    def test_explicit_check_subcommand(
        self, repo: GitRepo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["check", "--root", str(repo.root)]) == 1
        assert "PY003" in capsys.readouterr().out

    def test_missing_root_is_usage_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["check", "--root", "definitely/not/here"]) == 2
        assert "not a directory" in capsys.readouterr().err


class TestFlags:
    def test_format_choices_all_render(
        self, repo: GitRepo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        for fmt in ("text", "json", "sarif", "github"):
            main(["check", "--root", str(repo.root), "--format", fmt])
            assert capsys.readouterr().out.strip(), f"{fmt} produced nothing"

    def test_show_unknown_lists_skipped(
        self, repo: GitRepo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["check", "--root", str(repo.root), "--show-unknown"])
        assert "skipped (unknown/unverifiable)" in capsys.readouterr().out

    def test_fail_on_info_still_exits_one(self, repo: GitRepo) -> None:
        assert main(["check", "--root", str(repo.root), "--fail-on", "info"]) == 1

    def test_disabling_the_pack_clears_the_finding(
        self, repo: GitRepo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["check", "--root", str(repo.root), "--rules", "agents"])
        assert code == 0
        assert "0 findings" in capsys.readouterr().out

    def test_no_temporal_downgrades_confidence(
        self, repo: GitRepo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["check", "--root", str(repo.root), "--no-temporal"])
        assert "confidence: medium" in capsys.readouterr().out

    def test_path_argument_narrows_the_scan(
        self, repo: GitRepo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo.commit({"docs/extra.md": "`demo.Widget`\n"}, "add a doc under docs/")
        main(["check", "--root", str(repo.root), "docs"])
        out = capsys.readouterr().out
        assert "1 references" in out  # only the doc under docs/, not the README


class TestExplain:
    def test_known_rule(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["explain", "py003"]) == 0  # case-insensitive
        out = capsys.readouterr().out
        assert "PY003" in out
        assert "Suppress with" in out

    def test_unknown_rule(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["explain", "ZZ999"]) == 2
        assert "unknown rule" in capsys.readouterr().err


class TestAnchors:
    def test_update_writes_hash_then_check_is_clean(
        self, repo: GitRepo, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doc = repo.root / "README.md"
        doc.write_text(
            "<!-- docrot:anchor symbol=demo.core.Widget -->\nIt is a widget.\n"
            "<!-- /docrot:anchor -->\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(repo.root)
        assert main(["anchors", "update"]) == 0
        assert "hash=sha256:" in doc.read_text(encoding="utf-8")
        capsys.readouterr()
        assert main(["check", "--root", str(repo.root)]) == 0

    def test_update_reports_unlocatable_symbols(
        self, repo: GitRepo, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (repo.root / "README.md").write_text(
            "<!-- docrot:anchor symbol=demo.core.NoSuchThing -->\ntext\n<!-- /docrot:anchor -->\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(repo.root)
        main(["anchors", "update"])
        assert "cannot locate" in capsys.readouterr().out


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    import docrot

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert docrot.__version__ in capsys.readouterr().out


def test_public_api_check(repo: GitRepo) -> None:
    import docrot

    report = docrot.check(repo.root)
    assert report.summary.refs > 0
    assert isinstance(Path(report.findings[0].ref.doc), Path)
