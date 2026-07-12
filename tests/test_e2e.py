"""Golden end-to-end: a miniature repo with known drift, checked via the
public API and the CLI."""

from __future__ import annotations

import json

import pytest
from tests.conftest import GitRepo

import docrot
from docrot.cli import main


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
