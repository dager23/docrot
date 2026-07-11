"""Shared fixtures: synthetic packages and scripted git histories."""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest


def write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8")


class GitRepo:
    """A scripted git repository for temporal-gate tests."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._git("init", "-b", "main")
        self._git("config", "user.email", "test@docrot.invalid")
        self._git("config", "user.name", "docrot-tests")
        self._git("config", "commit.gpgsign", "false")

    def _git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True, check=True
        )
        return proc.stdout

    def commit(self, files: dict[str, str], message: str) -> str:
        write_tree(self.root, files)
        self._git("add", "-A")
        self._git("commit", "-m", message, "--no-verify")
        return self._git("rev-parse", "HEAD").strip()

    def remove(self, *paths: str, message: str) -> str:
        self._git("rm", "-q", *paths)
        self._git("commit", "-m", message, "--no-verify")
        return self._git("rev-parse", "HEAD").strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> GitRepo:
    return GitRepo(tmp_path)


@pytest.fixture
def synthetic_pkg(tmp_path: Path) -> Path:
    """A package exercising every FP class from the validation spike:

    star-import re-exports, alias chains, inheritance across modules,
    instance attributes, and a module-level proxy-ish attribute.
    """
    write_tree(
        tmp_path,
        {
            "pyproject.toml": """\
                [project]
                name = "mypkg"
                version = "0.0.0"

                [project.scripts]
                mypkg-cli = "mypkg.cli:main"
                """,
            "src/mypkg/__init__.py": """\
                from mypkg.api import *  # star-import re-export (httpx pattern)
                from mypkg.models import Response  # alias chain (requests pattern)

                globals_registry = {}

                __all__ = ["Client", "Response", "fetch_all"]
                """,
            "src/mypkg/api.py": """\
                __all__ = ["Client", "fetch_all"]

                class Client:
                    def request(self, method, url):
                        return None

                def fetch_all(urls):
                    return []
                """,
            "src/mypkg/models.py": """\
                from mypkg.base import BaseResponse

                class Response(BaseResponse):
                    def __init__(self):
                        self.raw = None  # instance attribute (Response.raw pattern)

                    def iter_lines(self):
                        yield ""
                """,
            "src/mypkg/base.py": """\
                class BaseResponse:
                    def close(self):  # inherited member (Flask.route pattern)
                        pass
                """,
            "src/mypkg/dynamic.py": """\
                def __getattr__(name):  # dynamic module: missing names are UNKNOWN
                    raise AttributeError(name)
                """,
            "src/mypkg/cli.py": """\
                def main():
                    return 0
                """,
        },
    )
    return tmp_path
