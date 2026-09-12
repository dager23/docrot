"""Corpus harness: run docrot against real open-source projects.

For every repository this does three things:

1. *Smoke* - run each output format and confirm nothing crashes and that
   the machine-readable formats parse.
2. *Measure* - record references, verdict split and timings. These are the
   numbers published in BENCHMARKS.md; nothing there is derived by hand.
3. *Seed* - build true-positive and true-negative scenarios out of facts
   taken from the project itself (a symbol it really exports, a file it
   really tracks, a Make target it really defines) and assert docrot
   reaches the right verdict on each.

Seeded scenarios are committed on a throwaway branch; the repository is
restored to its original commit afterwards.

Usage:
    python scripts/corpus_check.py <corpus-dir> [--json OUT] [--only NAME ...]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROBE_DOC = "DOCROT_PROBE.md"
PROBE_BRANCH = "docrot-probe"
NEVER_EXISTED = "DocrotProbeNeverExisted"
PROBE_ASSET = "docrot_probe_asset.txt"


def run_docrot(repo: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "docrot.cli", "check", "--root", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=repo,
    )
    return proc.returncode, proc.stdout + proc.stderr


def docrot_json(repo: Path, *args: str) -> dict[str, Any]:
    code, out = run_docrot(repo, "--format", "json", *args)
    if code not in (0, 1):
        raise RuntimeError(f"docrot exited {code}: {out[:400]}")
    payload: dict[str, Any] = json.loads(out)
    return payload


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def commit(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message, "--no-verify")


@dataclass
class Scenario:
    name: str
    expectation: str
    passed: bool
    detail: str = ""


@dataclass
class RepoResult:
    repo: str
    head: str = ""
    refs: int = 0
    resolved: int = 0
    unknown: int = 0
    findings: int = 0
    cold_seconds: float = 0.0
    warm_seconds: float = 0.0
    finding_list: list[dict[str, Any]] = field(default_factory=list)
    scenarios: list[Scenario] = field(default_factory=list)
    error: str = ""


def package_facts(repo: Path) -> tuple[str, str, list[str]]:
    """(package name, package dir, really-exported symbols) from the project."""
    from docrot.config import load_config
    from docrot.discovery import discover_packages
    from docrot.model import RawSpan, Reference, RefKind, Verdict
    from docrot.resolve.python import PythonResolver

    packages = discover_packages(load_config(repo))
    if not packages:
        return "", "", []
    pkg = packages[0]
    resolver = PythonResolver(packages)
    exported: list[str] = []
    module = resolver.top_level_module(pkg.name)
    if module is not None:
        for name in sorted(module.members):
            if name.startswith("_"):
                continue
            target = f"{pkg.name}.{name}"
            ref = Reference(RawSpan(Path(PROBE_DOC), 1, 1, target), RefKind.SYMBOL_DOTTED, target)
            if resolver.resolve(ref).verdict is Verdict.RESOLVED:
                exported.append(name)
            if len(exported) >= 8:
                break
    return pkg.name, pkg.path.relative_to(repo).as_posix(), exported


def probe_findings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in payload["findings"] if f["doc"] == PROBE_DOC and not f["suppressed"]]


def _symbol_scenarios(repo: Path, pkg: str, real: str) -> list[Scenario]:
    (repo / PROBE_DOC).write_text(
        f"# docrot probe\n\n"
        f"Real export: `{pkg}.{real}`.\n"
        f"Absent symbol: `{pkg}.{NEVER_EXISTED}`.\n",
        encoding="utf-8",
    )
    commit(repo, "docrot probe: a real export and an absent one")
    found = probe_findings(docrot_json(repo))
    flagged = {f["target"] for f in found}

    real_target = f"{pkg}.{real}"
    never = f"{pkg}.{NEVER_EXISTED}"
    hit = next((f for f in found if f["target"] == never), None)
    return [
        Scenario(
            "true-negative-symbol",
            f"`{real_target}`, really exported, is not flagged",
            real_target not in flagged,
            f"flagged: {sorted(flagged)}" if real_target in flagged else "",
        ),
        Scenario(
            "true-positive-never-existed",
            f"`{never}` is flagged PY003",
            hit is not None and hit["rule"] == "PY003",
            "not flagged" if hit is None else f"rule={hit['rule']}",
        ),
    ]


def _still_resolves(repo: Path, target: str) -> bool:
    """Does `target` still resolve against the working tree as it stands?"""
    from docrot.config import load_config
    from docrot.discovery import discover_packages
    from docrot.model import RawSpan, Reference, RefKind, Verdict
    from docrot.resolve.python import PythonResolver

    resolver = PythonResolver(discover_packages(load_config(repo)))
    ref = Reference(RawSpan(Path(PROBE_DOC), 1, 1, target), RefKind.SYMBOL_DOTTED, target)
    return resolver.resolve(ref).verdict is Verdict.RESOLVED


def _drift_scenarios(repo: Path, pkg: str, pkg_dir: str, real: str) -> list[Scenario]:
    init = repo / pkg_dir / "__init__.py"
    if not init.is_file():
        return [Scenario("true-positive-drift", "package has __init__", True, "skipped")]
    lines = init.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    kept = [ln for ln in lines if real not in ln]
    if len(kept) == len(lines):
        return [
            Scenario("true-positive-drift", "export line removable", True, "skipped: star-export")
        ]

    init.write_text("".join(kept), encoding="utf-8")
    commit(repo, f"docrot probe: remove the {real} export")
    if _still_resolves(repo, f"{pkg}.{real}"):
        # star-imports and re-export shims can keep a name reachable after
        # its explicit line is gone, so there is no drift to detect here
        return [
            Scenario(
                "true-positive-drift",
                "export removal makes the symbol unreachable",
                True,
                "skipped: still reachable after removal",
            )
        ]
    hit = next(
        (f for f in probe_findings(docrot_json(repo)) if f["target"] == f"{pkg}.{real}"),
        None,
    )
    out = [
        Scenario(
            "true-positive-drift",
            f"removing `{real}` turns the doc line into a PY002 finding",
            hit is not None and hit["rule"] == "PY002",
            "not flagged" if hit is None else f"rule={hit['rule']}",
        )
    ]
    if hit is not None:
        ev = hit.get("evidence") or {}
        out.append(
            Scenario(
                "drift-provenance",
                "the finding names the commit where it broke",
                bool(ev.get("resolved_at_introduction") and ev.get("broken_since")),
                f"evidence={ev}",
            )
        )
    return out


def _path_scenarios(repo: Path) -> list[Scenario]:
    (repo / PROBE_ASSET).write_text("probe\n", encoding="utf-8")
    (repo / PROBE_DOC).write_text(
        f"# docrot probe\n\nThe asset lives at `{PROBE_ASSET}`.\n", encoding="utf-8"
    )
    commit(repo, "docrot probe: document a file that exists")
    present = probe_findings(docrot_json(repo))

    git(repo, "rm", "-q", PROBE_ASSET)
    commit(repo, "docrot probe: delete the documented file")
    hit = next((f for f in probe_findings(docrot_json(repo)) if f["target"] == PROBE_ASSET), None)
    return [
        Scenario(
            "true-negative-path",
            "a file that exists is not flagged",
            not present,
            str(present)[:160],
        ),
        Scenario(
            "true-positive-path",
            "deleting a documented file is flagged",
            hit is not None and hit["rule"] in ("PATH001", "AG002"),
            "not flagged" if hit is None else f"rule={hit['rule']}",
        ),
    ]


def _command_scenarios(repo: Path) -> list[Scenario]:
    makefile = repo / "Makefile"
    if not makefile.is_file():
        return []
    text = makefile.read_text(encoding="utf-8", errors="replace")
    targets = [
        t
        for t in re.findall(r"^([A-Za-z0-9][\w.-]*)\s*:(?!=)", text, re.MULTILINE)
        if not t.startswith(".")
    ]
    if not targets:
        return []
    real_target = targets[0]

    (repo / PROBE_DOC).write_text(f"Run `make {real_target}` to build.\n", encoding="utf-8")
    commit(repo, "docrot probe: document a real make target")
    flagged = {f["target"] for f in probe_findings(docrot_json(repo))}
    scenarios = [
        Scenario(
            "true-negative-command",
            f"`make {real_target}`, a target that exists, is not flagged",
            f"make {real_target}" not in flagged,
            str(sorted(flagged))[:160],
        )
    ]

    # delete that target: the documented command has now drifted
    pattern = re.compile(r"^" + re.escape(real_target) + r"\s*:(?!=)")
    kept = [ln for ln in text.splitlines(keepends=True) if not pattern.match(ln)]
    makefile.write_text("".join(kept), encoding="utf-8")
    commit(repo, f"docrot probe: remove the {real_target} target")
    hit = next(
        (f for f in probe_findings(docrot_json(repo)) if f["target"] == f"make {real_target}"),
        None,
    )
    scenarios.append(
        Scenario(
            "true-positive-command",
            f"removing the `{real_target}` target flags the documented command",
            hit is not None and hit["rule"] == "AG001",
            "not flagged" if hit is None else f"rule={hit['rule']}",
        )
    )
    return scenarios


def seed_scenarios(repo: Path, pkg: str, pkg_dir: str, exported: list[str]) -> list[Scenario]:
    if not pkg or not exported:
        return [Scenario("package-facts", "a package with exports", False, "none detected")]
    real = exported[0]
    scenarios = _symbol_scenarios(repo, pkg, real)
    scenarios += _drift_scenarios(repo, pkg, pkg_dir, real)
    scenarios += _path_scenarios(repo)
    scenarios += _command_scenarios(repo)
    return scenarios


def check_repo(repo: Path) -> RepoResult:
    result = RepoResult(repo=repo.name)
    original = git(repo, "rev-parse", "HEAD").strip()
    result.head = original[:10]
    try:
        for fmt in ("text", "json", "sarif", "github"):
            code, out = run_docrot(repo, "--format", fmt)
            if code not in (0, 1):
                raise RuntimeError(f"--format {fmt} exited {code}: {out[:300]}")
            if "Traceback" in out:
                raise RuntimeError(f"--format {fmt} raised: {out[:300]}")
        for fmt in ("json", "sarif"):
            json.loads(run_docrot(repo, "--format", fmt)[1])

        start = time.perf_counter()
        payload = docrot_json(repo)
        result.cold_seconds = round(time.perf_counter() - start, 2)
        start = time.perf_counter()
        docrot_json(repo)
        result.warm_seconds = round(time.perf_counter() - start, 2)

        summary = payload["summary"]
        result.refs = summary["refs"]
        result.resolved = summary["resolved"]
        result.unknown = summary["unknown"]
        result.findings = summary["findings"]
        result.finding_list = [
            {k: f[k] for k in ("rule", "doc", "line", "target", "message")}
            for f in payload["findings"]
            if not f["suppressed"]
        ]

        git(repo, "checkout", "-q", "-b", PROBE_BRANCH)
        try:
            pkg, pkg_dir, exported = package_facts(repo)
            result.scenarios = seed_scenarios(repo, pkg, pkg_dir, exported)
        finally:
            git(repo, "checkout", "-q", "-f", original, check=False)
            git(repo, "branch", "-q", "-D", PROBE_BRANCH, check=False)
            git(repo, "clean", "-qfd", check=False)
    except Exception as exc:  # the harness must survive an awkward repository
        result.error = f"{type(exc).__name__}: {exc}"
        git(repo, "checkout", "-q", "-f", original, check=False)
        git(repo, "branch", "-q", "-D", PROBE_BRANCH, check=False)
        git(repo, "clean", "-qfd", check=False)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus")
    parser.add_argument("--json", dest="out")
    parser.add_argument("--only", nargs="*", default=[])
    args = parser.parse_args()

    corpus = Path(args.corpus).resolve()
    repos = sorted(p for p in corpus.iterdir() if (p / ".git").is_dir())
    if args.only:
        wanted = set(args.only)
        repos = [r for r in repos if r.name in wanted]

    results: list[RepoResult] = []
    for repo in repos:
        print(f"--- {repo.name}", flush=True)
        result = check_repo(repo)
        results.append(result)
        if result.error:
            print(f"    ERROR {result.error}", flush=True)
            continue
        passed = sum(s.passed for s in result.scenarios)
        print(
            f"    {result.refs} refs, {result.findings} findings, "
            f"scenarios {passed}/{len(result.scenarios)}, "
            f"cold {result.cold_seconds}s warm {result.warm_seconds}s",
            flush=True,
        )
        for scenario in result.scenarios:
            if not scenario.passed:
                print(
                    f"    FAIL {scenario.name}: {scenario.expectation} [{scenario.detail}]",
                    flush=True,
                )

    total = sum(len(r.scenarios) for r in results)
    passed = sum(sum(s.passed for s in r.scenarios) for r in results)
    crashed = [r.repo for r in results if r.error]
    print(
        f"\n{len(results)} repositories | scenarios {passed}/{total} passed "
        f"| crashes: {len(crashed)}"
    )
    if crashed:
        print("crashed:", ", ".join(crashed))

    if args.out:
        Path(args.out).write_text(
            json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8"
        )
        print(f"wrote {args.out}")
    return 0 if passed == total and not crashed else 1


if __name__ == "__main__":
    raise SystemExit(main())
