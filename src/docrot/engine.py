"""The orchestrator: discovery → extraction → resolution → gates → findings."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from docrot.config import Config
from docrot.discovery import (
    discover_command_sources,
    discover_docs,
    discover_packages,
    is_agent_file,
)
from docrot.extract.classify import (
    classify,
    classify_command,
    classify_link,
    path_fallback_eligible,
)
from docrot.extract.markdown import Extracted, extract_markdown
from docrot.extract.rst import extract_rst
from docrot.extract.suppress import parse_suppressions
from docrot.model import (
    Finding,
    Reference,
    RefKind,
    Report,
    Resolution,
    Summary,
    Verdict,
)
from docrot.resolve.commands import CommandResolver, build_inventory
from docrot.resolve.paths import PathResolver
from docrot.resolve.python import PythonResolver
from docrot.rules import anchors as anchors_mod
from docrot.rules.registry import History, judge, judge_anchor
from docrot.temporal.gate import TemporalGate
from docrot.temporal.git import Git
from docrot.temporal.history import HistoricalResolver


def run_check(config: Config) -> Report:
    git = Git(config.root)
    notes: list[str] = list(config.warnings)

    docs = discover_docs(config, git)
    packages = discover_packages(config)
    sources = discover_command_sources(config.root)

    python = PythonResolver(packages, config.external_packages)
    for err in python.load_errors:
        notes.append(f"package load: {err}")

    if git.available:
        tracked = git.ls_files()
    else:
        tracked = frozenset(
            p.relative_to(config.root).as_posix()
            for p in config.root.rglob("*")
            if p.is_file() and ".git" not in p.parts
        )
    paths = PathResolver(tracked)
    commands = CommandResolver(build_inventory(sources), python, tracked)

    temporal_on = config.temporal != "off" and git.available
    if config.temporal == "on" and not git.available:
        notes.append("temporal gate requested but no git history is available")
    if temporal_on and git.is_shallow:
        notes.append(
            "shallow clone: temporal evidence may be incomplete "
            "(use fetch-depth: 0 in CI for full provenance)"
        )

    pkg_dirs = {p.name: p.path.relative_to(config.root).as_posix() for p in packages}
    gate = TemporalGate(git, HistoricalResolver(git, pkg_dirs), sources) if temporal_on else None

    findings: list[Finding] = []
    unknowns: list[Resolution] = []
    total_refs = 0
    resolved_count = 0

    for rel in docs:
        abs_path = config.root / PurePosixPath(rel)
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        doc = Path(rel)
        agent = is_agent_file(rel)
        suppressions = parse_suppressions(text)

        if rel.endswith(".rst"):
            extracted: Extracted = extract_rst(doc, text, agent)
        else:
            extracted = extract_markdown(doc, text, agent)

        refs: list[Reference] = []
        for span in extracted.inline_spans:
            ref = classify(span, commands.inv.entry_points)
            if ref is not None:
                refs.append(ref)
        for span in extracted.command_lines:
            cmd = classify_command(span)
            if cmd is not None:
                refs.append(cmd)
        refs.extend(classify_link(span) for span in extracted.link_targets)

        for ref in refs:
            if config.ref_ignored(ref.target):
                continue
            total_refs += 1
            resolution = _resolve(ref, python, paths, commands)

            if resolution.verdict is Verdict.RESOLVED:
                resolved_count += 1
            elif resolution.verdict is Verdict.UNKNOWN:
                unknowns.append(resolution)

            if (
                resolution.verdict is Verdict.BROKEN
                and ref.kind is RefKind.SYMBOL_DOTTED
                and git.available
                and _is_runtime_identifier(ref.target, git, pkg_dirs)
            ):
                # the code itself uses this dotted name as a *string* (entry
                # point group, config key, event name): not a symbol claim
                resolution = Resolution(ref, Verdict.UNKNOWN, boundary="string-literal")
                unknowns.append(resolution)

            history: History = "unavailable"
            evidence = None
            if resolution.verdict is Verdict.BROKEN and gate is not None:
                scope = _scope_for(ref, pkg_dirs)
                evidence = gate.examine(ref, scope)
                if evidence is not None:
                    history = "confirmed" if evidence.resolved_at_introduction else "refuted"
            elif (
                resolution.verdict is Verdict.RESOLVED
                and resolution.boundary == "basename"
                and "/" in ref.target
                and gate is not None
            ):
                # unique-basename relocation: PATH002 only when the documented
                # location provably existed (file moved, docs didn't)
                evidence = gate.examine(ref, None, exact_path=True)
                if evidence is not None:
                    history = "confirmed" if evidence.resolved_at_introduction else "refuted"

            local_symbol = (
                ref.kind is RefKind.SYMBOL_DOTTED
                and ref.target.split(".")[0] in python.local_package_names
            )
            finding = judge(resolution, evidence, config, local_symbol, history)
            if finding is not None:
                if suppressions.matches(ref.line, finding.rule):
                    finding = _suppress(finding)
                findings.append(finding)

        # anchors (opt-in by presence)
        for anchor in anchors_mod.parse_anchors(doc, text):
            total_refs += 1
            obj = python.find_object(anchor.symbol)
            current_hash = None
            if obj is not None and obj.filepath and obj.lineno and obj.endlineno:
                fp = obj.filepath
                if isinstance(fp, Path):
                    current_hash = anchors_mod.symbol_body_hash(fp, obj.lineno, obj.endlineno)
            resolution = anchors_mod.check_anchor(anchor, current_hash, obj is not None)
            if resolution.verdict is Verdict.RESOLVED:
                resolved_count += 1
            finding = judge_anchor(resolution, config)
            if finding is not None:
                if suppressions.matches(anchor.line, finding.rule):
                    finding = _suppress(finding)
                findings.append(finding)

    findings.sort(key=lambda f: (f.ref.doc.as_posix(), f.ref.line, f.ref.col, f.rule))
    active = [f for f in findings if not f.suppressed]
    by_severity: dict[str, int] = {}
    for f in active:
        by_severity[f.severity.value] = by_severity.get(f.severity.value, 0) + 1

    summary = Summary(
        refs=total_refs,
        resolved=resolved_count,
        unknown=len(unknowns),
        findings=len(active),
        suppressed=len(findings) - len(active),
        by_severity=by_severity,
    )
    return Report(
        findings=tuple(findings),
        summary=summary,
        unknowns=tuple(unknowns),
        notes=tuple(notes),
    )


def _resolve(
    ref: Reference,
    python: PythonResolver,
    paths: PathResolver,
    commands: CommandResolver,
) -> Resolution:
    if ref.kind in (RefKind.SYMBOL_DOTTED, RefKind.SYMBOL_CALL):
        resolution = python.resolve(ref)
        if resolution.verdict is not Verdict.RESOLVED and path_fallback_eligible(ref):
            as_path = paths.resolve(Reference(ref.span, RefKind.PATH, ref.target))
            if as_path.verdict is Verdict.RESOLVED:
                return Resolution(
                    ref,
                    Verdict.RESOLVED,
                    resolved_as=as_path.resolved_as,
                    boundary="as-path",
                )
        return resolution
    if ref.kind is RefKind.LINK:
        # markdown links resolve relative to the containing document first
        normalized = _normalize_dots(ref.doc.parent, ref.target)
        if normalized is not None and paths.has(normalized):
            return Resolution(ref, Verdict.RESOLVED, resolved_as=normalized)
        return paths.resolve(ref)
    if ref.kind is RefKind.PATH:
        return paths.resolve(ref)
    if ref.kind is RefKind.COMMAND:
        return commands.resolve(ref)
    return Resolution(ref, Verdict.UNKNOWN, boundary="unclassified")


def _normalize_dots(base: Path, target: str) -> str | None:
    """Join and normalize `target` against `base` without touching the fs."""
    parts: list[str] = list(base.parts)
    for piece in target.split("/"):
        if piece in ("", "."):
            continue
        if piece == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(piece)
    return "/".join(parts) if parts else None


def _is_runtime_identifier(target: str, git: Git, pkg_dirs: dict[str, str]) -> bool:
    for pkg_dir in pkg_dirs.values():
        for quoted in (f'"{target}"', f"'{target}'"):
            if git.grep_worktree(quoted, pkg_dir):
                return True
    return False


def _scope_for(ref: Reference, pkg_dirs: dict[str, str]) -> str | None:
    if ref.kind in (RefKind.SYMBOL_DOTTED, RefKind.SYMBOL_CALL):
        head = ref.target.split(".")[0]
        return pkg_dirs.get(head)
    return None


def _suppress(finding: Finding) -> Finding:
    from dataclasses import replace

    return replace(finding, suppressed=True)


def exit_code(report: Report, config: Config) -> int:
    from docrot.model import Confidence

    for f in report.findings:
        if f.suppressed:
            continue
        if not f.severity.at_least(config.fail_on):
            continue
        if f.confidence is Confidence.HIGH or config.strict:
            return 1
    return 0
