from __future__ import annotations

from pathlib import Path

from docrot.model import RawSpan, Reference, RefKind, Verdict
from docrot.resolve.commands import (
    TaskInventory,
    check_command,
    inventory_from_texts,
    parse_just_recipes,
    parse_make_targets,
    parse_nox_sessions,
    parse_package_json_scripts,
    parse_tox_envs,
)
from docrot.resolve.paths import PathResolver

DOC = Path("README.md")


def pref(target: str) -> Reference:
    return Reference(RawSpan(DOC, 1, 1, target), RefKind.PATH, target)


class TestPathResolver:
    TRACKED = frozenset(
        {
            "src/mypkg/__init__.py",
            "src/mypkg/auth.py",
            "pyproject.toml",
            "docs/guide.md",
        }
    )

    def test_exact_match(self) -> None:
        r = PathResolver(self.TRACKED)
        assert r.resolve(pref("docs/guide.md")).verdict is Verdict.RESOLVED

    def test_directory_match(self) -> None:
        r = PathResolver(self.TRACKED)
        assert r.resolve(pref("src/mypkg")).verdict is Verdict.RESOLVED

    def test_basename_elsewhere(self) -> None:
        # `auth.py` documented without its real location (spike __version__.py case)
        r = PathResolver(self.TRACKED)
        res = r.resolve(pref("auth.py"))
        assert res.verdict is Verdict.RESOLVED
        assert res.resolved_as == "src/mypkg/auth.py"

    def test_ambiguous_basename_unknown(self) -> None:
        # several unrelated files share the basename: any suggestion is a guess
        tracked = self.TRACKED | {"docs/index.html", "site/admin/index.html"}
        r = PathResolver(frozenset(tracked))
        res = r.resolve(pref("htmlcov/index.html"))
        assert res.verdict is Verdict.UNKNOWN
        assert res.boundary == "ambiguous-basename"

    def test_dot_directory_preserved(self) -> None:
        from docrot.extract.classify import classify
        from docrot.model import RawSpan

        ref = classify(RawSpan(DOC, 1, 1, ".github/AI_POLICY.md"), frozenset())
        assert ref is not None
        assert ref.target == ".github/AI_POLICY.md"

    def test_placeholder_unknown(self) -> None:
        r = PathResolver(self.TRACKED)
        assert r.resolve(pref("path/to/config.py")).verdict is Verdict.UNKNOWN
        assert r.resolve(pref("yourapplication/views.py")).verdict is Verdict.UNKNOWN

    def test_bare_missing_marked_bare(self) -> None:
        # tutorial `views.py` the reader creates — gate decides, marker set
        r = PathResolver(self.TRACKED)
        res = r.resolve(pref("views.py"))
        assert res.verdict is Verdict.BROKEN
        assert res.boundary == "bare"

    def test_missing_with_slash_broken(self) -> None:
        r = PathResolver(self.TRACKED)
        assert r.resolve(pref("src/mypkg/removed.py")).verdict is Verdict.BROKEN


class TestRunnerParsers:
    def test_make_targets(self) -> None:
        targets, opaque = parse_make_targets(
            "test: deps\n\techo hi\n\nlint:\n\truff .\n\n%.o: %.c\n\tcc\n"
            ".PHONY: test lint\nVAR := x\n"
        )
        assert targets == frozenset({"test", "lint"})
        assert not opaque

    def test_make_computed_targets_opaque(self) -> None:
        _, opaque = parse_make_targets("$(GEN)-build:\n\techo hi\n")
        assert opaque

    def test_tox_envs(self) -> None:
        envs, opaque = parse_tox_envs(
            "[tox]\nenvlist = py310, py311\n\n[testenv:lint]\ncommands = ruff\n"
        )
        assert envs == frozenset({"py310", "py311", "lint"})
        assert not opaque

    def test_tox_generated_opaque(self) -> None:
        _, opaque = parse_tox_envs("[tox]\nenvlist = py{310,311}\n")
        assert opaque

    def test_nox_sessions(self) -> None:
        sessions = parse_nox_sessions(
            "import nox\n\n@nox.session\ndef tests(s): ...\n\n"
            "@nox.session(name='type-check')\ndef typecheck(s): ...\n"
        )
        assert sessions == frozenset({"tests", "type-check"})

    def test_npm_scripts(self) -> None:
        assert parse_package_json_scripts('{"scripts": {"build": "x", "dev": "y"}}') == (
            frozenset({"build", "dev"})
        )

    def test_just_recipes(self) -> None:
        assert "deploy" in parse_just_recipes("deploy:\n  echo hi\n")


class TestCheckCommand:
    INV = TaskInventory(
        make_targets=frozenset({"test", "lint"}),
        tox_envs=frozenset({"py310"}),
        nox_sessions=frozenset({"tests"}),
        npm_scripts=frozenset({"build"}),
        just_recipes=frozenset({"deploy"}),
        entry_points=frozenset({"mytool"}),
    )

    def test_make_known(self) -> None:
        assert check_command("make test", self.INV, None).verdict is Verdict.RESOLVED

    def test_make_dead_target(self) -> None:
        chk = check_command("make integration-test", self.INV, None)
        assert chk.verdict is Verdict.BROKEN
        assert chk.unit == "integration-test"

    def test_make_source_absent_unknown(self) -> None:
        chk = check_command("make anything", TaskInventory(), None)
        assert chk.verdict is Verdict.UNKNOWN

    def test_tox_env(self) -> None:
        assert check_command("tox -e py310", self.INV, None).verdict is Verdict.RESOLVED
        assert check_command("tox -e py399", self.INV, None).verdict is Verdict.BROKEN

    def test_npm_run(self) -> None:
        assert check_command("npm run build", self.INV, None).verdict is Verdict.RESOLVED
        assert check_command("npm run gone", self.INV, None).verdict is Verdict.BROKEN

    def test_npm_builtin_unknown(self) -> None:
        assert check_command("npm install", self.INV, None).verdict is Verdict.UNKNOWN

    def test_entry_point_name(self) -> None:
        assert check_command("mytool serve", self.INV, None).verdict is Verdict.RESOLVED

    def test_python_m_stdlib(self) -> None:
        assert check_command("python -m json.tool", self.INV, None).verdict is (Verdict.RESOLVED)

    def test_pytest_path(self) -> None:
        tracked = frozenset({"tests/test_app.py"})
        assert (
            check_command("pytest tests/test_app.py", self.INV, None, tracked).verdict
            is Verdict.RESOLVED
        )
        assert (
            check_command("pytest tests/test_gone.py", self.INV, None, tracked).verdict
            is Verdict.BROKEN
        )

    def test_unknown_runner(self) -> None:
        assert check_command("cargo build", self.INV, None).verdict is Verdict.UNKNOWN


class TestRunnerCoverage:
    """Every runner docrot claims to understand, exercised."""

    INV = TaskInventory(
        make_targets=frozenset({"test"}),
        tox_envs=frozenset({"py312"}),
        nox_sessions=frozenset({"tests"}),
        npm_scripts=frozenset({"build"}),
        just_recipes=frozenset({"deploy"}),
        poe_tasks=frozenset({"lint"}),
        pdm_scripts=frozenset({"serve"}),
        entry_points=frozenset({"mytool"}),
    )

    def test_nox_session(self) -> None:
        assert check_command("nox -s tests", self.INV, None).verdict is Verdict.RESOLVED
        assert check_command("nox -s gone", self.INV, None).verdict is Verdict.BROKEN

    def test_nox_without_session_is_unknown(self) -> None:
        assert check_command("nox", self.INV, None).verdict is Verdict.UNKNOWN

    def test_poe_task(self) -> None:
        assert check_command("poe lint", self.INV, None).verdict is Verdict.RESOLVED
        assert check_command("poe gone", self.INV, None).verdict is Verdict.BROKEN

    def test_pdm_run_known_script(self) -> None:
        assert check_command("pdm run serve", self.INV, None).verdict is Verdict.RESOLVED

    def test_pdm_run_unknown_is_passthrough(self) -> None:
        # pdm run also executes arbitrary commands: absence proves nothing
        assert check_command("pdm run anything", self.INV, None).verdict is Verdict.UNKNOWN

    def test_just_recipe(self) -> None:
        assert check_command("just deploy", self.INV, None).verdict is Verdict.RESOLVED
        assert check_command("just gone", self.INV, None).verdict is Verdict.BROKEN

    def test_yarn_and_pnpm_run(self) -> None:
        assert check_command("pnpm run build", self.INV, None).verdict is Verdict.RESOLVED
        assert check_command("yarn run gone", self.INV, None).verdict is Verdict.BROKEN

    def test_uv_run_entry_point(self) -> None:
        assert check_command("uv run mytool", self.INV, None).verdict is Verdict.RESOLVED
        assert check_command("uv run whatever", self.INV, None).verdict is Verdict.UNKNOWN

    def test_tox_combined_short_flag(self) -> None:
        assert check_command("tox -epy312", self.INV, None).verdict is Verdict.RESOLVED

    def test_tox_without_env_is_unknown(self) -> None:
        assert check_command("tox", self.INV, None).verdict is Verdict.UNKNOWN

    def test_make_with_variable_assignment_ignored(self) -> None:
        assert check_command("make test FOO=1", self.INV, None).verdict is Verdict.RESOLVED

    def test_make_without_target_is_unknown(self) -> None:
        assert check_command("make", self.INV, None).verdict is Verdict.UNKNOWN

    def test_empty_command(self) -> None:
        assert check_command("", self.INV, None).verdict is Verdict.UNKNOWN

    def test_pytest_without_path(self) -> None:
        assert check_command("pytest -q", self.INV, None).verdict is Verdict.UNKNOWN


class TestInventoryFromTexts:
    def test_builds_from_raw_blobs(self) -> None:
        inv = inventory_from_texts(
            {
                "makefile": "build:\n\techo\n",
                "tox_ini": "[tox]\nenvlist = py311\n",
                "noxfile": "import nox\n@nox.session\ndef unit(s): ...\n",
                "package_json": '{"scripts": {"dev": "x"}}',
                "justfile": "run:\n  echo\n",
                "pyproject": "[project.scripts]\nmine = 'a:b'\n",
            }
        )
        assert inv.make_targets == frozenset({"build"})
        assert inv.tox_envs == frozenset({"py311"})
        assert inv.nox_sessions == frozenset({"unit"})
        assert inv.npm_scripts == frozenset({"dev"})
        assert "run" in (inv.just_recipes or frozenset())
        assert inv.entry_points == frozenset({"mine"})

    def test_absent_sources_stay_none(self) -> None:
        inv = inventory_from_texts({"makefile": None})
        assert inv.make_targets is None
        assert inv.tox_envs is None
