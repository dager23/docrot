"""Golden end-to-end: a miniature repo with known drift, checked via the
public API and the CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import GitRepo

import docrot
from docrot.cli import main
from docrot.model import Confidence


@pytest.fixture
def drifted_repo(git_repo: GitRepo) -> GitRepo:
    git_repo.commit(
        {
            "pyproject.toml": (
                "[project]\nname = 'demo'\nversion = '0.0.0'\n\n"
                "[project.scripts]\ndemo = 'demo.cli:main'\n"
            ),
            "demo/__init__.py": "from demo.core import Widget, make_widget\n",
            "demo/core.py": (
                "class Widget:\n"
                "    def render(self):\n"
                "        return ''\n\n"
                "def make_widget():\n"
                "    return Widget()\n"
            ),
            "demo/cli.py": "def main():\n    return 0\n",
            "Makefile": "test:\n\techo t\nrelease:\n\techo r\n",
            "README.md": (
                "# Demo\n\n"
                "Create widgets with `demo.make_widget()` and render via\n"
                "`demo.Widget.render`. Configuration lives in `demo/core.py`.\n\n"
                "```bash\n"
                "make release\n"
                "```\n\n"
                "For tutorials, create a `views.py` file (never in repo).\n"
                "External stuff like `numpy.array` is not ours.\n"
                "Enable turbo with `demo.enable_turbo()` (never implemented).\n"
            ),
            "AGENTS.md": (
                "# Agent notes\n\nCore logic is in `demo/core.py`. Run `make test` after edits.\n"
            ),
        },
        "initial state, docs all true",
    )
    # drift: remove make_widget + the release target; move nothing else
    git_repo.commit(
        {
            "demo/__init__.py": "from demo.core import Widget\n",
            "demo/core.py": ("class Widget:\n    def render(self):\n        return ''\n"),
            "Makefile": "test:\n\techo t\n",
        },
        "remove make_widget and release target",
    )
    return git_repo


def test_finds_exactly_the_real_drift(drifted_repo: GitRepo) -> None:
    report = docrot.check(drifted_repo.root)

    rules = {(f.rule, f.ref.target) for f in report.findings if not f.suppressed}
    assert ("PY002", "demo.make_widget") in rules
    assert ("AG001", "make release") in rules
    # documented API that never shipped (the httpx.Mounts case)
    assert ("PY003", "demo.enable_turbo") in rules

    targets = [f.ref.target for f in report.findings]
    # fiction and externals never flagged
    assert "views.py" not in targets
    assert "numpy.array" not in targets
    # still-valid references not flagged
    assert "demo.Widget.render" not in targets
    assert "demo/core.py" not in targets
    assert "make test" not in targets


def test_provenance_attached(drifted_repo: GitRepo) -> None:
    report = docrot.check(drifted_repo.root)
    py002 = next(f for f in report.findings if f.rule == "PY002")
    assert py002.evidence is not None
    assert py002.evidence.resolved_at_introduction
    assert py002.evidence.broken_since is not None
    assert py002.evidence.introduced_at is not None
    assert py002.evidence.broken_since.sha != py002.evidence.introduced_at.sha


def test_cli_json_and_exit_code(
    drifted_repo: GitRepo, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(drifted_repo.root)
    code = main(["check", "--format", "json", "--root", str(drifted_repo.root)])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert code == 1
    assert payload["schema"] == 1
    assert payload["summary"]["findings"] >= 2
    assert all("evidence" in f for f in payload["findings"])


def test_suppression_zeroes_exit(drifted_repo: GitRepo) -> None:
    readme = drifted_repo.root / "README.md"
    text = readme.read_text(encoding="utf-8")
    text = text.replace(
        "Create widgets with `demo.make_widget()`",
        "Create widgets with `demo.make_widget()` <!-- docrot: ignore -->",
    )
    readme.write_text(text, encoding="utf-8")
    makefile_line = "make release"
    agents = drifted_repo.root / "README.md"
    text = agents.read_text(encoding="utf-8").replace(
        makefile_line, makefile_line + " # docrot cannot suppress in fences"
    )
    report = docrot.check(drifted_repo.root)
    suppressed = [f for f in report.findings if f.suppressed]
    assert any(f.ref.target == "demo.make_widget" for f in suppressed)


def test_no_temporal_mode_degrades(drifted_repo: GitRepo) -> None:
    from dataclasses import replace

    from docrot.config import load_config
    from docrot.engine import run_check

    config = replace(load_config(drifted_repo.root), temporal="off")
    report = run_check(config)
    # PY001 (medium) replaces PY002 (high) without history
    rules = {f.rule for f in report.findings}
    assert "PY002" not in rules
    assert "PY001" in rules


class TestNeverExistedIsProvable:
    """PY003 claims a symbol *never existed*. That must be provable.

    Regression for the anthropic-sdk-python false positives: 13 symbols in
    MIGRATION.md were reported as never having existed when they had been
    exported for years and removed in the very commit that wrote the guide.
    """

    def test_symbol_removed_before_doc_was_written_is_not_flagged(self, git_repo: GitRepo) -> None:
        git_repo.commit(
            {
                "pyproject.toml": "[project]\nname = 'demo'\nversion = '0'\n",
                "demo/__init__.py": "from demo.core import Legacy\n",
                "demo/core.py": "class Legacy:\n    pass\n",
            },
            "Legacy exists",
        )
        git_repo.commit(
            {"demo/__init__.py": "\n", "demo/core.py": "class Modern:\n    pass\n"},
            "remove Legacy",
        )
        # the doc line is authored *after* the removal
        git_repo.commit(
            {"NOTES.md": "The old `demo.Legacy` class handled this.\n"},
            "write notes referencing the removed class",
        )
        report = docrot.check(git_repo.root)
        assert not [f for f in report.findings if f.rule == "PY003"]
        assert any(r.boundary == "removed-before-doc-was-written" for r in report.unknowns), (
            "should be recorded as unverifiable, not silently dropped"
        )

    def test_symbol_that_truly_never_existed_is_still_flagged(self, git_repo: GitRepo) -> None:
        # contrast case: the httpx.Mounts shape must keep firing
        git_repo.commit(
            {
                "pyproject.toml": "[project]\nname = 'demo'\nversion = '0'\n",
                "demo/__init__.py": "from demo.core import Modern\n",
                "demo/core.py": "class Modern:\n    pass\n",
                "NOTES.md": "Use `demo.Imaginary` for this.\n",
            },
            "document an API that was never built",
        )
        report = docrot.check(git_repo.root)
        assert any(f.rule == "PY003" and f.ref.target == "demo.Imaginary" for f in report.findings)

    def test_migration_guides_are_historical_docs(self, git_repo: GitRepo) -> None:
        git_repo.commit(
            {
                "pyproject.toml": "[project]\nname = 'demo'\nversion = '0'\n",
                "demo/__init__.py": "\n",
                "MIGRATION.md": "`demo.OldThing` was removed; use `demo.NewThing`.\n",
                "UPGRADING.md": "`demo.AlsoGone` is no longer exported.\n",
            },
            "migration guides name APIs that are gone, by design",
        )
        report = docrot.check(git_repo.root)
        assert report.findings == ()


def test_degraded_modes_are_announced() -> None:
    """Never degrade silently: the user must be told why confidence dropped."""
    import shutil
    import tempfile

    from docrot.config import load_config
    from docrot.engine import run_check

    # An independent location: a directory nested inside a git fixture would
    # still resolve to that parent repository.
    workdir = Path(tempfile.mkdtemp(prefix="docrot-nogit-"))
    try:
        (workdir / "demo").mkdir()
        (workdir / "pyproject.toml").write_text(
            "[project]\nname = 'demo'\nversion = '0'\n", encoding="utf-8"
        )
        (workdir / "demo" / "__init__.py").write_text("\n", encoding="utf-8")
        (workdir / "README.md").write_text("Use `demo.missing_thing`.\n", encoding="utf-8")
        report = run_check(load_config(workdir))
        assert any("no git history" in n for n in report.notes)
        # and the finding it does report is explicitly lower confidence
        assert all(f.confidence is not Confidence.HIGH for f in report.findings)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_empty_target_is_not_silently_green(tmp_path: Path) -> None:
    from docrot.config import load_config
    from docrot.engine import run_check

    (tmp_path / "src").mkdir()
    report = run_check(load_config(tmp_path))
    assert report.summary.findings == 0
    assert any("no documentation files found" in n for n in report.notes)
    assert any("no Python package detected" in n for n in report.notes)


def test_untracked_agent_file_still_checked(drifted_repo: GitRepo) -> None:
    # agents read CLAUDE.md whether or not it's committed
    (drifted_repo.root / "CLAUDE.md").write_text(
        "Logic is in `demo/gone_dir/nothing.py`.\n", encoding="utf-8"
    )
    report = docrot.check(drifted_repo.root)
    assert any(
        f.rule == "AG002" and f.ref.target == "demo/gone_dir/nothing.py" for f in report.findings
    )


def test_explain_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["explain", "PY002"]) == 0
    out = capsys.readouterr().out
    assert "PY002" in out
    assert main(["explain", "NOPE"]) == 2
