"""Render BENCHMARKS.md from corpus_check.py output.

Every number in the published table comes out of this script, so no value
can be hand-derived. (An earlier revision of BENCHMARKS.md computed the
"resolved" column as references minus unknowns, which is wrong: totals
also include references the temporal gate refuted. Generating the table
removes the opportunity.)

Usage:
    python scripts/render_benchmarks.py <corpus-results.json> [-o BENCHMARKS.md]
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

HEADER = """# Benchmarks

docrot run against real open-source projects. Every figure here is
produced by `scripts/corpus_check.py` and rendered by
`scripts/render_benchmarks.py`; nothing is typed in by hand.

Reproduce with:

```bash
python scripts/corpus_check.py <corpus-dir> --json results.json
python scripts/render_benchmarks.py results.json -o BENCHMARKS.md
```

Run {run_date} with docrot {version} on full-history clones
(Windows 10, Python 3.13, git via subprocess).

## What the harness checks

For each repository it runs every output format and confirms none of them
crash, measures the reference counts and timings below, and then seeds
scenarios built from facts about that project - a symbol it really
exports, a file it really tracks, a Make target it really defines - to
confirm docrot reaches the right verdict on each:

| Scenario | Built from | Expected |
|---|---|---|
| `true-negative-symbol` | a symbol the package really exports | not flagged |
| `true-positive-never-existed` | a name that provably never existed | PY003 |
| `true-positive-drift` | removing that real export | PY002 |
| `drift-provenance` | the same finding | names the breaking commit |
| `true-negative-path` | a file that really exists | not flagged |
| `true-positive-path` | deleting that file | PATH001 |
| `true-negative-command` | a Make target that really exists | not flagged |
| `true-positive-command` | deleting that target | AG001 |

Seeded work happens on a throwaway branch; each repository is restored
afterwards.
"""


def render(results: list[dict[str, Any]], version: str) -> str:
    rows = []
    total_refs = total_findings = total_unknown = total_resolved = 0
    scen_pass = scen_total = 0
    skipped = 0

    for result in sorted(results, key=lambda r: r["repo"]):
        if result["error"]:
            rows.append(f"| {result['repo']} | error | | | | | | {result['error'][:60]} |")
            continue
        passed = sum(1 for s in result["scenarios"] if s["passed"])
        count = len(result["scenarios"])
        skipped += sum(1 for s in result["scenarios"] if "skipped" in (s["detail"] or ""))
        scen_pass += passed
        scen_total += count
        total_refs += result["refs"]
        total_findings += result["findings"]
        total_unknown += result["unknown"]
        total_resolved += result["resolved"]
        rows.append(
            f"| {result['repo']} | {result['refs']} | {result['resolved']} | "
            f"{result['unknown']} | {result['findings']} | "
            f"{result['cold_seconds']}s | {result['warm_seconds']}s | "
            f"{passed}/{count} |"
        )

    findings_rows = []
    for result in sorted(results, key=lambda r: r["repo"]):
        for finding in result["finding_list"]:
            findings_rows.append(
                f"| {result['repo']} | `{finding['rule']}` | "
                f"{finding['doc']}:{finding['line']} | `{finding['target']}` |"
            )

    crashes = [r["repo"] for r in results if r["error"]]
    body = [
        HEADER.format(run_date=date.today().isoformat(), version=version),
        "\n## Results\n",
        "| Repo | Refs | Resolved | Unknown | Findings | Cold | Warm | Scenarios |",
        "|---|---|---|---|---|---|---|---|",
        *rows,
        "",
        f"**{len(results)} repositories, {total_refs} references checked.** "
        f"{total_resolved} resolved, {total_unknown} skipped as unverifiable, "
        f"{total_findings} reported as findings.",
        "",
        f"**Seeded scenarios: {scen_pass}/{scen_total} passed** "
        f"({skipped} inapplicable and skipped), **{len(crashes)} crashes**.",
        "",
        "Reference totals do not equal resolved plus unknown: the remainder "
        "are references the temporal gate refuted, meaning they never "
        "resolved at the commit where the line was written and so are "
        "treated as fiction rather than drift.",
        "",
        "## Findings reported\n",
    ]
    if findings_rows:
        body += [
            "| Repo | Rule | Location | Target |",
            "|---|---|---|---|",
            *findings_rows,
        ]
    else:
        body.append("No findings on this corpus.")

    body += [
        "",
        "## Honest limitations",
        "",
        "- The corpus is biased toward unusually well-maintained projects.",
        "  The 2023 EMSE baseline (28.9% of the top-1000 GitHub repositories",
        "  carry at least one dead documentation reference) predicts a higher",
        "  rate across the long tail.",
        "- The unknown column is the price of precision. References docrot",
        "  cannot verify - external packages, dynamic attributes, example",
        "  values - are counted and inspectable via `--show-unknown`, never",
        "  guessed at.",
        "- Findings were verified against each project's git history by the",
        "  tool's author. Independent confirmation is what upstream issue",
        "  reports are for.",
        "",
    ]
    return "\n".join(body)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results")
    parser.add_argument("-o", "--out", default="BENCHMARKS.md")
    parser.add_argument("--version", default="")
    args = parser.parse_args()

    version = args.version
    if not version:
        import docrot

        version = docrot.__version__

    results = json.loads(Path(args.results).read_text(encoding="utf-8"))
    Path(args.out).write_text(render(results, version), encoding="utf-8", newline="\n")
    print(f"wrote {args.out} from {len(results)} repositories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
