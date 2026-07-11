"""Command reference validation.

Only the *addressable unit* is validated — a Make target, a tox env, an
npm script, a `python -m` module, an entry-point name. Never flags/options
(post-v1). Opaque task files (computed targets, generated envs) mark the
whole runner UNKNOWN rather than risking a false BROKEN.
"""

from __future__ import annotations

import ast
import configparser
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from docrot.discovery import CommandSources
from docrot.model import Reference, Resolution, Verdict
from docrot.resolve.python import PythonResolver

_MAKE_TARGET = re.compile(r"^([A-Za-z0-9][\w./-]*)\s*:{1,2}(?!=)", re.MULTILINE)
_JUST_RECIPE = re.compile(r"^([A-Za-z_][\w-]*)(?:\s+[\w =\"'-]*)?\s*:\s*(?:$|[^=])", re.MULTILINE)


@dataclass
class TaskInventory:
    """Addressable units parsed from the repo's runner files."""

    make_targets: frozenset[str] | None = None  # None = source absent
    make_opaque: bool = False
    tox_envs: frozenset[str] | None = None
    tox_opaque: bool = False
    nox_sessions: frozenset[str] | None = None
    npm_scripts: frozenset[str] | None = None
    just_recipes: frozenset[str] | None = None
    poe_tasks: frozenset[str] | None = None
    pdm_scripts: frozenset[str] | None = None
    entry_points: frozenset[str] = frozenset()
    extras: dict[str, Any] = field(default_factory=dict)


def parse_make_targets(text: str) -> tuple[frozenset[str], bool]:
    targets = set()
    opaque = False
    for m in _MAKE_TARGET.finditer(text):
        name = m.group(1)
        if "%" in name or name.startswith("."):
            continue
        targets.add(name)
    # computed target names mean we can't enumerate reliably
    if re.search(r"^[^\t\n#=]*\$[({][^\n]*:", text, re.MULTILINE):
        opaque = True
    if re.search(r"^\.DEFAULT", text, re.MULTILINE) or "$(eval" in text:
        opaque = True
    return frozenset(targets), opaque


def parse_tox_envs(text: str) -> tuple[frozenset[str], bool]:
    parser = configparser.ConfigParser()
    try:
        parser.read_string(text)
    except configparser.Error:
        return frozenset(), True
    raw = parser.get("tox", "envlist", fallback="") or parser.get("tox", "env_list", fallback="")
    if "{" in raw or "$" in raw:
        return frozenset(), True
    envs = {e.strip() for chunk in raw.splitlines() for e in chunk.split(",") if e.strip()}
    # every [testenv:x] section is addressable too
    for section in parser.sections():
        if section.startswith("testenv:"):
            envs.add(section.split(":", 1)[1])
    return frozenset(envs), False


def parse_nox_sessions(text: str) -> frozenset[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return frozenset()
    sessions = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            call = dec if isinstance(dec, ast.Call) else None
            base = call.func if call else dec
            name = getattr(base, "attr", None) or getattr(base, "id", None)
            if name == "session":
                session_name = node.name
                if call:
                    for kw in call.keywords:
                        if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                            session_name = str(kw.value.value)
                sessions.add(session_name)
    return frozenset(sessions)


def parse_package_json_scripts(text: str) -> frozenset[str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return frozenset()
    scripts = data.get("scripts", {})
    return frozenset(scripts) if isinstance(scripts, dict) else frozenset()


def parse_just_recipes(text: str) -> frozenset[str]:
    return frozenset(m.group(1) for m in _JUST_RECIPE.finditer(text))


def parse_pyproject_tasks(text: str) -> dict[str, frozenset[str]]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    project = data.get("project", {})
    tool = data.get("tool", {})
    eps = set(project.get("scripts", {})) | set(project.get("gui-scripts", {}))
    poe = tool.get("poe", {}).get("tasks", {})
    pdm = tool.get("pdm", {}).get("scripts", {})
    return {
        "entry_points": frozenset(eps),
        "poe_tasks": frozenset(poe) if isinstance(poe, dict) else frozenset(),
        "pdm_scripts": frozenset(pdm) if isinstance(pdm, dict) else frozenset(),
    }


def build_inventory(sources: CommandSources) -> TaskInventory:
    inv = TaskInventory()

    def read(p: Path | None) -> str | None:
        if p is None:
            return None
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    if (text := read(sources.makefile)) is not None:
        inv.make_targets, inv.make_opaque = parse_make_targets(text)
    if (text := read(sources.tox_ini)) is not None:
        inv.tox_envs, inv.tox_opaque = parse_tox_envs(text)
    if (text := read(sources.noxfile)) is not None:
        inv.nox_sessions = parse_nox_sessions(text)
    if (text := read(sources.package_json)) is not None:
        inv.npm_scripts = parse_package_json_scripts(text)
    if (text := read(sources.justfile)) is not None:
        inv.just_recipes = parse_just_recipes(text)
    if (text := read(sources.pyproject)) is not None:
        tables = parse_pyproject_tasks(text)
        inv.entry_points = tables.get("entry_points", frozenset())
        inv.poe_tasks = tables.get("poe_tasks") or None
        inv.pdm_scripts = tables.get("pdm_scripts") or None
    return inv


def inventory_from_texts(texts: dict[str, str | None]) -> TaskInventory:
    """Build an inventory from raw file contents (used at historical commits)."""
    inv = TaskInventory()
    if (t := texts.get("makefile")) is not None:
        inv.make_targets, inv.make_opaque = parse_make_targets(t)
    if (t := texts.get("tox_ini")) is not None:
        inv.tox_envs, inv.tox_opaque = parse_tox_envs(t)
    if (t := texts.get("noxfile")) is not None:
        inv.nox_sessions = parse_nox_sessions(t)
    if (t := texts.get("package_json")) is not None:
        inv.npm_scripts = parse_package_json_scripts(t)
    if (t := texts.get("justfile")) is not None:
        inv.just_recipes = parse_just_recipes(t)
    if (t := texts.get("pyproject")) is not None:
        tables = parse_pyproject_tasks(t)
        inv.entry_points = tables.get("entry_points", frozenset())
        inv.poe_tasks = tables.get("poe_tasks") or None
        inv.pdm_scripts = tables.get("pdm_scripts") or None
    return inv


@dataclass(frozen=True)
class CommandCheck:
    verdict: Verdict
    unit: str = ""  # the addressable unit that was validated
    detail: str = ""


class CommandResolver:
    def __init__(
        self,
        inventory: TaskInventory,
        python: PythonResolver | None,
        tracked: frozenset[str] = frozenset(),
    ) -> None:
        self.inv = inventory
        self.python = python
        self.tracked = tracked

    def resolve(self, ref: Reference) -> Resolution:
        check = check_command(ref.target, self.inv, self.python, self.tracked)
        return Resolution(
            ref,
            check.verdict,
            resolved_as=check.unit or None,
            boundary=check.detail or None,
        )


def _named(unit: str, known: frozenset[str] | None, opaque: bool, runner: str) -> CommandCheck:
    if known is None:
        return CommandCheck(Verdict.UNKNOWN, unit, f"{runner}:source-absent")
    if unit in known:
        return CommandCheck(Verdict.RESOLVED, unit)
    if opaque:
        return CommandCheck(Verdict.UNKNOWN, unit, f"{runner}:opaque")
    return CommandCheck(Verdict.BROKEN, unit, runner)


def check_command(
    command: str,
    inv: TaskInventory,
    python: PythonResolver | None,
    tracked: frozenset[str] = frozenset(),
) -> CommandCheck:
    tokens = command.split()
    if not tokens:
        return CommandCheck(Verdict.UNKNOWN, detail="empty")
    runner, args = tokens[0], tokens[1:]

    if runner == "make":
        targets = [a for a in args if not a.startswith("-") and "=" not in a]
        if not targets:
            return CommandCheck(Verdict.UNKNOWN, detail="make:default-target")
        for t in targets:
            res = _named(t, inv.make_targets, inv.make_opaque, "make")
            if res.verdict is not Verdict.RESOLVED:
                return res
        return CommandCheck(Verdict.RESOLVED, targets[0])

    if runner == "tox":
        envs: list[str] = []
        for i, a in enumerate(args):
            if a in ("-e", "--env") and i + 1 < len(args):
                envs.extend(args[i + 1].split(","))
            elif a.startswith("-e") and len(a) > 2:
                envs.extend(a[2:].split(","))
        if not envs:
            return CommandCheck(Verdict.UNKNOWN, detail="tox:no-env")
        for e in envs:
            res = _named(e.strip(), inv.tox_envs, inv.tox_opaque, "tox")
            if res.verdict is not Verdict.RESOLVED:
                return res
        return CommandCheck(Verdict.RESOLVED, envs[0])

    if runner == "nox":
        sessions: list[str] = []
        for i, a in enumerate(args):
            if a in ("-s", "--session", "--sessions") and i + 1 < len(args):
                sessions.append(args[i + 1])
        if not sessions:
            return CommandCheck(Verdict.UNKNOWN, detail="nox:no-session")
        for s in sessions:
            res = _named(s, inv.nox_sessions, False, "nox")
            if res.verdict is not Verdict.RESOLVED:
                return res
        return CommandCheck(Verdict.RESOLVED, sessions[0])

    if runner == "poe":
        task = next((a for a in args if not a.startswith("-")), None)
        if task is None:
            return CommandCheck(Verdict.UNKNOWN, detail="poe:no-task")
        return _named(task, inv.poe_tasks, False, "poe")

    if runner == "pdm" and args[:1] == ["run"]:
        task = next((a for a in args[1:] if not a.startswith("-")), None)
        if task is None:
            return CommandCheck(Verdict.UNKNOWN, detail="pdm:no-task")
        if inv.pdm_scripts is not None and task in inv.pdm_scripts:
            return CommandCheck(Verdict.RESOLVED, task)
        return CommandCheck(Verdict.UNKNOWN, task, "pdm:passthrough")

    if runner in ("npm", "pnpm", "yarn") and args[:1] == ["run"]:
        script = next((a for a in args[1:] if not a.startswith("-")), None)
        if script is None:
            return CommandCheck(Verdict.UNKNOWN, detail=f"{runner}:no-script")
        return _named(script, inv.npm_scripts, False, runner)

    if runner == "just":
        recipe = next((a for a in args if not a.startswith("-")), None)
        if recipe is None:
            return CommandCheck(Verdict.UNKNOWN, detail="just:default")
        return _named(recipe, inv.just_recipes, False, "just")

    if runner in ("python", "python3", "py") and args[:1] == ["-m"] and len(args) >= 2:
        module = args[1]
        top = module.split(".")[0]
        if top in sys.stdlib_module_names:
            return CommandCheck(Verdict.RESOLVED, module)
        if python is not None and top in python.local_package_names:
            verdict = python.module_exists(module)
            return CommandCheck(verdict, module, "python-m" if verdict != Verdict.RESOLVED else "")
        return CommandCheck(Verdict.UNKNOWN, module, "python-m:external")

    if runner in ("uv", "uvx") and args[:1] == ["run"]:
        target = next((a for a in args[1:] if not a.startswith("-")), None)
        if target and target in inv.entry_points:
            return CommandCheck(Verdict.RESOLVED, target)
        return CommandCheck(Verdict.UNKNOWN, target or "", "uv:passthrough")

    if runner == "pytest":
        for a in args:
            if a.startswith("-"):
                continue
            path = a.split("::", 1)[0].replace("\\", "/").lstrip("./")
            if path.endswith(".py") and tracked:
                if path in tracked:
                    return CommandCheck(Verdict.RESOLVED, path)
                return CommandCheck(Verdict.BROKEN, path, "pytest:path")
        return CommandCheck(Verdict.UNKNOWN, detail="pytest:no-path")

    if runner in inv.entry_points:
        return CommandCheck(Verdict.RESOLVED, runner)

    return CommandCheck(Verdict.UNKNOWN, detail=f"runner:{runner}")
