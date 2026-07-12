"""Discovery: which docs to scan, which packages to index, which command
sources exist.

Zero-config behavior is defined here: git-tracked markdown/rst plus agent
context files, changelog-likes excluded; package roots detected from
pyproject or by scanning for ``__init__.py`` at the repo root and ``src/``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from docrot.config import AGENT_FILE_GLOBS, AGENT_FILE_NAMES, CHANGELOG_STEMS, Config
from docrot.globmatch import any_glob_match
from docrot.temporal.git import Git


@dataclass(frozen=True)
class PackageRoot:
    name: str
    path: Path  # absolute path to the package directory (contains __init__.py)

    @property
    def search_path(self) -> Path:
        return self.path.parent


@dataclass(frozen=True)
class CommandSources:
    makefile: Path | None = None
    tox_ini: Path | None = None
    noxfile: Path | None = None
    package_json: Path | None = None
    justfile: Path | None = None
    pyproject: Path | None = None


def is_agent_file(rel_posix: str) -> bool:
    name = PurePosixPath(rel_posix).name
    if name in AGENT_FILE_NAMES:
        return True
    return any_glob_match(rel_posix, AGENT_FILE_GLOBS)


def _is_changelog(rel_posix: str) -> bool:
    stem = PurePosixPath(rel_posix).stem.lower()
    return any(stem.startswith(c) for c in CHANGELOG_STEMS)


def discover_docs(config: Config, git: Git) -> list[str]:
    """Repo-relative POSIX paths of docs to scan, deterministic order."""
    if git.available:
        candidates = sorted(git.ls_files())
    else:
        candidates = sorted(
            p.relative_to(config.root).as_posix()
            for p in config.root.rglob("*")
            if p.is_file() and ".git" not in p.parts
        )

    selected: list[str] = []
    for rel in candidates:
        if is_agent_file(rel):
            selected.append(rel)
            continue
        if not any_glob_match(rel, config.docs):
            continue
        if _is_changelog(rel):
            continue
        if any_glob_match(rel, config.exclude_docs):
            continue
        selected.append(rel)

    if config.changed_only and git.available:
        changed = git.changed_files()
        selected = [r for r in selected if r in changed]
    return selected


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            data: dict[str, Any] = tomllib.load(f)
            return data
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _package_dir(root: Path, name: str) -> Path | None:
    """Locate the package directory, returning its *true on-disk casing*.

    PyPI names are often capitalized ("Flask") while import names are not;
    on case-insensitive filesystems a naive `base / name` check would
    "find" src/Flask and poison the whole symbol index with a package
    named Flask that git (case-sensitive) can never see.
    """
    mod = name.replace("-", "_")
    for base in (root, root / "src"):
        if not (base / mod / "__init__.py").is_file():
            continue
        try:
            entries = list(base.iterdir())
        except OSError:
            return base / mod
        for child in entries:  # exact case first
            if child.name == mod and (child / "__init__.py").is_file():
                return child
        for child in entries:  # true-case fallback (Flask -> flask)
            if child.name.lower() == mod.lower() and (child / "__init__.py").is_file():
                return child
    return None


def discover_packages(config: Config) -> list[PackageRoot]:
    root = config.root
    names: list[str] = list(config.packages)

    if not names:
        pyproject = _read_toml(root / "pyproject.toml")
        project_name = pyproject.get("project", {}).get("name")
        if isinstance(project_name, str):
            names.append(project_name)
        # setuptools/hatch explicit package lists, when present
        tool = pyproject.get("tool", {})
        for pkgs in (
            tool.get("setuptools", {}).get("packages"),
            tool.get("hatch", {})
            .get("build", {})
            .get("targets", {})
            .get("wheel", {})
            .get("packages"),
        ):
            if isinstance(pkgs, list):
                names.extend(PurePosixPath(str(p)).name for p in pkgs)

    roots: list[PackageRoot] = []
    seen: set[str] = set()
    for name in names:
        mod = name.replace("-", "_")
        if mod.lower() in seen:
            continue
        path = _package_dir(root, mod)
        if path is not None:
            # the directory's true name is the import name
            roots.append(PackageRoot(path.name, path))
            seen.add(path.name.lower())

    if not roots:
        # last resort: scan for top-level packages
        for base in (root, root / "src"):
            if not base.is_dir():
                continue
            for child in sorted(base.iterdir()):
                if (
                    child.is_dir()
                    and (child / "__init__.py").is_file()
                    and not child.name.startswith((".", "_"))
                    and child.name not in ("tests", "test", "docs", "examples", "scripts")
                    and child.name not in seen
                ):
                    roots.append(PackageRoot(child.name, child))
                    seen.add(child.name)
    return roots


def discover_command_sources(root: Path) -> CommandSources:
    def opt(name: str) -> Path | None:
        p = root / name
        return p if p.is_file() else None

    return CommandSources(
        makefile=opt("Makefile") or opt("makefile") or opt("GNUmakefile"),
        tox_ini=opt("tox.ini"),
        noxfile=opt("noxfile.py"),
        package_json=opt("package.json"),
        justfile=opt("justfile") or opt("Justfile") or opt(".justfile"),
        pyproject=opt("pyproject.toml"),
    )
