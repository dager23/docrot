"""Names that parse as dotted symbols but name files.

`settings.json` matches the dotted-symbol grammar, so symbol resolution is
tried first (that is what keeps `Response.json` from being mistaken for a
file). When it resolves as neither a symbol nor a path, the reference must
still reach the temporal gate as a *path* candidate, or a documented file
that was deleted goes unnoticed.
"""

from __future__ import annotations

from tests.conftest import GitRepo

import docrot

PYPROJECT = "[project]\nname = 'demo'\nversion = '0'\n"


def test_deleted_file_with_dotted_name_is_caught(git_repo: GitRepo) -> None:
    git_repo.commit(
        {
            "pyproject.toml": PYPROJECT,
            "demo/__init__.py": "\n",
            "settings.json": "{}\n",
            "README.md": "Configuration lives in `settings.json`.\n",
        },
        "document a config file that exists",
    )
    git_repo.remove("settings.json", message="delete the config file")

    report = docrot.check(git_repo.root)
    assert any(f.rule == "PATH001" and f.ref.target == "settings.json" for f in report.findings), [
        (f.rule, f.ref.target) for f in report.findings
    ]


def test_file_that_never_existed_stays_quiet(git_repo: GitRepo) -> None:
    git_repo.commit(
        {
            "pyproject.toml": PYPROJECT,
            "demo/__init__.py": "\n",
            "README.md": "Some tools read `tsconfig.json` from the project root.\n",
        },
        "mention a file this project never had",
    )
    assert docrot.check(git_repo.root).findings == ()


def test_existing_file_with_dotted_name_resolves(git_repo: GitRepo) -> None:
    git_repo.commit(
        {
            "pyproject.toml": PYPROJECT,
            "demo/__init__.py": "\n",
            "settings.json": "{}\n",
            "README.md": "Configuration lives in `settings.json`.\n",
        },
        "document a config file that exists",
    )
    report = docrot.check(git_repo.root)
    assert report.findings == ()
    assert report.summary.resolved >= 1


def test_method_named_json_is_not_mistaken_for_a_file(git_repo: GitRepo) -> None:
    # the original reason symbols are tried first
    git_repo.commit(
        {
            "pyproject.toml": PYPROJECT,
            "demo/__init__.py": "from demo.core import Response\n",
            "demo/core.py": "class Response:\n    def json(self):\n        return {}\n",
            "README.md": "Call `Response.json` to decode.\n",
        },
        "document a method whose name looks like a file extension",
    )
    assert docrot.check(git_repo.root).findings == ()
