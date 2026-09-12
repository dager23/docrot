"""Configuration loading and merging.

Precedence: CLI flags > docrot.toml > pyproject.toml [tool.docrot] > defaults.
Unknown keys warn rather than crash: config must never be the reason a CI run
explodes.
"""

from __future__ import annotations

import fnmatch
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

from docrot.model import Severity

#: Doc files that describe history are *supposed* to reference dead code.
#: Migration and upgrade guides belong here for the same reason a changelog
#: does: their whole job is to name the APIs that went away.
HISTORICAL_DOC_STEMS = (
    "changelog",
    "changes",
    "history",
    "news",
    "release",
    "releases",
    "migration",
    "migrating",
    "migrate",
    "upgrade",
    "upgrading",
    "porting",
    "breaking",
    "deprecation",
    "deprecations",
)

#: Agent context files get elevated treatment: agents act on their claims.
AGENT_FILE_NAMES = (
    "CLAUDE.md",
    "AGENTS.md",
    "GEMINI.md",
    ".cursorrules",
)
AGENT_FILE_GLOBS = (
    ".github/copilot-instructions.md",
    ".claude/**/*.md",
    ".cursor/rules/*",
)

DEFAULT_DOC_GLOBS = ("**/*.md", "**/*.rst")

_KNOWN_KEYS = {
    "packages",
    "docs",
    "exclude_docs",
    "rules",
    "severity",
    "fail_on",
    "temporal",
    "external_packages",
    "ignore_refs",
    "strict",
}


@dataclass(frozen=True)
class Config:
    root: Path
    packages: tuple[str, ...] = ()  # auto-detected when empty
    docs: tuple[str, ...] = DEFAULT_DOC_GLOBS
    exclude_docs: tuple[str, ...] = ()
    enabled_packs: tuple[str, ...] = ("core", "agents")
    disabled_rules: tuple[str, ...] = ()
    severity_overrides: dict[str, Severity] = field(default_factory=dict)
    fail_on: Severity = Severity.ERROR
    temporal: str = "auto"  # auto | on | off
    external_packages: tuple[str, ...] = ()
    ignore_refs: tuple[str, ...] = ()
    strict: bool = False
    show_unknown: bool = False
    changed_only: bool = False
    warnings: tuple[str, ...] = ()  # config-load warnings, surfaced in output

    def ref_ignored(self, target: str) -> bool:
        return any(fnmatch.fnmatchcase(target, pat) for pat in self.ignore_refs)


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            data: dict[str, Any] = tomllib.load(f)
            return data
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _severity(value: str) -> Severity:
    return Severity(value.lower())


def _apply_table(cfg: Config, table: dict[str, Any], source: str) -> Config:
    warnings = list(cfg.warnings)
    for key in table:
        if key not in _KNOWN_KEYS:
            warnings.append(f"{source}: unknown config key '{key}' ignored")

    updates: dict[str, Any] = {"warnings": tuple(warnings)}
    if "packages" in table:
        updates["packages"] = tuple(table["packages"])
    if "docs" in table:
        updates["docs"] = tuple(table["docs"])
    if "exclude_docs" in table:
        updates["exclude_docs"] = tuple(table["exclude_docs"])
    if "rules" in table:
        rules = table["rules"]
        if "enable" in rules:
            updates["enabled_packs"] = tuple(rules["enable"])
        if "disable" in rules:
            updates["disabled_rules"] = tuple(rules["disable"])
    if "severity" in table:
        overrides = dict(cfg.severity_overrides)
        for rule, sev in table["severity"].items():
            try:
                overrides[rule.upper()] = _severity(sev)
            except ValueError:
                warnings.append(f"{source}: invalid severity '{sev}' for {rule}")
                updates["warnings"] = tuple(warnings)
        updates["severity_overrides"] = overrides
    if "fail_on" in table:
        try:
            updates["fail_on"] = _severity(table["fail_on"])
        except ValueError:
            warnings.append(f"{source}: invalid fail_on '{table['fail_on']}'")
            updates["warnings"] = tuple(warnings)
    if "temporal" in table:
        if table["temporal"] in ("auto", "on", "off"):
            updates["temporal"] = table["temporal"]
        else:
            warnings.append(f"{source}: temporal must be auto|on|off")
            updates["warnings"] = tuple(warnings)
    if "external_packages" in table:
        updates["external_packages"] = tuple(table["external_packages"])
    if "ignore_refs" in table:
        updates["ignore_refs"] = tuple(table["ignore_refs"])
    if "strict" in table:
        updates["strict"] = bool(table["strict"])
    return replace(cfg, **updates)


def load_config(root: Path) -> Config:
    cfg = Config(root=root)
    pyproject = _read_toml(root / "pyproject.toml").get("tool", {}).get("docrot", {})
    if pyproject:
        cfg = _apply_table(cfg, pyproject, "pyproject.toml")
    own = _read_toml(root / "docrot.toml")
    if own:
        cfg = _apply_table(cfg, own, "docrot.toml")
    return cfg
