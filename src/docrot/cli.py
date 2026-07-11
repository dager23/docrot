"""Command-line interface.

docrot [check] [PATHS...]     check docs (default command)
docrot explain RULE           rule documentation
docrot anchors update [PATHS] recompute anchor hashes
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path, PurePosixPath

from docrot import __version__
from docrot.config import Config, load_config
from docrot.engine import exit_code, run_check
from docrot.model import Severity
from docrot.report import RENDERERS, render_text


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docrot",
        description="Deterministic doc-to-code drift checker.",
    )
    parser.add_argument("--version", action="version", version=f"docrot {__version__}")
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser("check", help="check docs for drift (default)")
    _add_check_args(check)

    explain = sub.add_parser("explain", help="explain a rule")
    explain.add_argument("rule", help="rule id, e.g. PY002")

    anchors = sub.add_parser("anchors", help="manage prose anchors")
    anchors_sub = anchors.add_subparsers(dest="anchors_command", required=True)
    update = anchors_sub.add_parser("update", help="recompute anchor hashes in docs")
    update.add_argument("paths", nargs="*", default=[])

    return parser


def _add_check_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("paths", nargs="*", default=[], help="restrict to docs under these paths")
    p.add_argument("--format", choices=sorted(RENDERERS), default="text")
    p.add_argument("--rules", help="comma-separated packs to enable (core,agents,anchors)")
    p.add_argument("--fail-on", choices=["error", "warning", "info"], dest="fail_on")
    p.add_argument("--no-temporal", action="store_true", help="disable the git temporal gate")
    p.add_argument(
        "--strict", action="store_true", help="medium-confidence findings also fail the run"
    )
    p.add_argument(
        "--show-unknown", action="store_true", help="list skipped (unverifiable) references"
    )
    p.add_argument(
        "--changed-only",
        action="store_true",
        help="only check docs modified relative to HEAD (pre-commit mode)",
    )
    p.add_argument("--root", default=".", help="repository root (default: cwd)")


def _apply_cli(config: Config, args: argparse.Namespace) -> Config:
    updates: dict[str, object] = {}
    if args.rules:
        updates["enabled_packs"] = tuple(s.strip() for s in args.rules.split(",") if s.strip())
    if args.fail_on:
        updates["fail_on"] = Severity(args.fail_on)
    if args.no_temporal:
        updates["temporal"] = "off"
    if args.strict:
        updates["strict"] = True
    if args.show_unknown:
        updates["show_unknown"] = True
    if args.changed_only:
        updates["changed_only"] = True
    if args.paths:
        prefixes = tuple(
            PurePosixPath(p.replace("\\", "/")).as_posix().rstrip("/") for p in args.paths
        )
        globs = []
        for pre in prefixes:
            globs += [pre, f"{pre}/**/*.md", f"{pre}/**/*.rst", f"{pre}/*.md", f"{pre}/*.rst"]
        updates["docs"] = tuple(globs)
    return replace(config, **updates)  # type: ignore[arg-type]


def _cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"docrot: not a directory: {root}", file=sys.stderr)
        return 2
    config = _apply_cli(load_config(root), args)
    report = run_check(config)
    renderer = RENDERERS[args.format]
    if renderer is render_text:
        render_text(report, sys.stdout, show_unknown=config.show_unknown)
    else:
        renderer(report, sys.stdout)
    return exit_code(report, config)


def _cmd_explain(args: argparse.Namespace) -> int:
    from docrot.rules.registry import RULES

    rule = RULES.get(args.rule.upper())
    if rule is None:
        print(f"docrot: unknown rule {args.rule!r}", file=sys.stderr)
        return 2
    print(f"{rule.id} [{rule.pack}] default severity: {rule.severity.value}")
    print(f"  {rule.summary}")
    print(
        "  Findings carry provenance when git history is available: the commit\n"
        "  where the doc line was written and the commit where resolution broke.\n"
        "  Suppress with <!-- docrot: ignore[" + rule.id + "] --> on or above the line."
    )
    return 0


def _cmd_anchors_update(args: argparse.Namespace) -> int:
    from docrot.discovery import discover_docs, discover_packages
    from docrot.resolve.python import PythonResolver
    from docrot.rules import anchors as anchors_mod
    from docrot.temporal.git import Git

    root = Path(".").resolve()
    config = load_config(root)
    git = Git(root)
    docs = discover_docs(config, git)
    if args.paths:
        wanted = tuple(PurePosixPath(p.replace("\\", "/")).as_posix() for p in args.paths)
        docs = [d for d in docs if d.startswith(wanted)]
    python = PythonResolver(discover_packages(config), config.external_packages)

    updated = 0
    for rel in docs:
        path = root / PurePosixPath(rel)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        anchors = anchors_mod.parse_anchors(Path(rel), text)
        if not anchors:
            continue
        lines = text.splitlines(keepends=True)
        changed = False
        for anchor in anchors:
            obj = python.find_object(anchor.symbol)
            if obj is None or not (obj.filepath and obj.lineno and obj.endlineno):
                print(f"{rel}:{anchor.line}: cannot locate `{anchor.symbol}`, skipped")
                continue
            fp = obj.filepath
            if not isinstance(fp, Path):
                continue
            new_hash = anchors_mod.symbol_body_hash(fp, obj.lineno, obj.endlineno)
            if new_hash is None:
                continue
            idx = anchor.line - 1
            new_line = anchors_mod.update_anchor_line(lines[idx], new_hash)
            if new_line != lines[idx]:
                lines[idx] = new_line
                changed = True
        if changed:
            path.write_text("".join(lines), encoding="utf-8")
            updated += 1
            print(f"updated anchors in {rel}")
    print(f"docrot: {updated} file(s) updated")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    known = {"check", "explain", "anchors", "-h", "--help", "--version"}
    if (
        not argv
        or (argv[0] not in known and not argv[0].startswith("-"))
        or (argv and argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version"))
    ):
        argv.insert(0, "check")

    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "explain":
            return _cmd_explain(args)
        if args.command == "anchors":
            return _cmd_anchors_update(args)
        return _cmd_check(args)
    except KeyboardInterrupt:
        return 3
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
