"""Benchmark harness: run docrot against a corpus of real repositories.

Usage:
    python scripts/bench.py <workdir> [repo-url ...]

Clones (full history) each repo into <workdir>, runs docrot, and writes
bench-results.json with findings + timing per repo. Findings are then
hand-labeled TP/FP in a copy of the JSON to compute precision (PLAN.md §11).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_CORPUS = [
    "https://github.com/encode/httpx",
    "https://github.com/psf/requests",
    "https://github.com/pallets/flask",
    "https://github.com/pallets/click",
    "https://github.com/encode/starlette",
    "https://github.com/Textualize/rich",
    "https://github.com/fastapi/typer",
    "https://github.com/pydantic/pydantic",
]


def run(workdir: Path, urls: list[str]) -> None:
    from docrot.config import load_config
    from docrot.engine import run_check
    from docrot.report import _finding_dict  # noqa: PLC2701 - internal reuse

    workdir.mkdir(parents=True, exist_ok=True)
    results = []
    for url in urls:
        name = url.rstrip("/").rsplit("/", 1)[-1]
        dest = workdir / name
        if not dest.exists():
            print(f"cloning {name}...", flush=True)
            subprocess.run(["git", "clone", "--quiet", url, str(dest)], check=True)
        t0 = time.perf_counter()
        report = run_check(load_config(dest))
        cold = time.perf_counter() - t0
        t0 = time.perf_counter()
        run_check(load_config(dest))
        warm = time.perf_counter() - t0
        s = report.summary
        print(
            f"{name}: {s.refs} refs, {s.findings} findings, "
            f"{s.unknown} unknown, cold {cold:.1f}s warm {warm:.1f}s"
        )
        results.append(
            {
                "repo": name,
                "url": url,
                "refs": s.refs,
                "resolved": s.resolved,
                "unknown": s.unknown,
                "cold_seconds": round(cold, 2),
                "warm_seconds": round(warm, 2),
                "findings": [
                    {**_finding_dict(f), "label": ""}  # hand-label TP/FP here
                    for f in report.findings
                ],
            }
        )
    out = workdir / "bench-results.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {out} — hand-label each finding's 'label' as TP/FP, then compute precision")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    run(Path(sys.argv[1]), sys.argv[2:] or DEFAULT_CORPUS)
