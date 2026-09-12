# Benchmarks

docrot run against real open-source projects. Every figure here is
produced by `scripts/corpus_check.py` and rendered by
`scripts/render_benchmarks.py`; nothing is typed in by hand.

Reproduce with:

```bash
python scripts/corpus_check.py <corpus-dir> --json results.json
python scripts/render_benchmarks.py results.json -o BENCHMARKS.md
```

Run 2026-09-12 with docrot 0.1.0 on full-history clones
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


## Results

| Repo | Refs | Resolved | Unknown | Findings | Cold | Warm | Scenarios |
|---|---|---|---|---|---|---|---|
| anthropic-sdk-python | 63 | 20 | 36 | 0 | 4.43s | 4.75s | 6/6 |
| arrow | 13 | 9 | 2 | 1 | 2.99s | 3.05s | 8/8 |
| attrs | 215 | 142 | 50 | 0 | 2.52s | 2.51s | 4/4 |
| cachetools | 88 | 64 | 23 | 0 | 0.79s | 0.8s | 5/5 |
| click | 228 | 118 | 99 | 0 | 1.81s | 2.15s | 4/4 |
| flask | 443 | 158 | 217 | 0 | 13.73s | 10.76s | 6/6 |
| griffe | 343 | 187 | 139 | 1 | 2.88s | 2.9s | 5/5 |
| httpx | 187 | 82 | 85 | 1 | 2.41s | 2.41s | 5/5 |
| humanize | 17 | 6 | 11 | 0 | 0.67s | 0.67s | 6/6 |
| itsdangerous | 9 | 6 | 3 | 0 | 0.67s | 0.66s | 6/6 |
| jinja | 75 | 42 | 28 | 1 | 3.57s | 3.57s | 4/4 |
| loguru | 56 | 25 | 28 | 0 | 0.93s | 0.94s | 5/5 |
| markupsafe | 1 | 0 | 1 | 0 | 0.64s | 0.64s | 6/6 |
| packaging | 41 | 37 | 3 | 0 | 0.96s | 0.95s | 5/5 |
| platformdirs | 98 | 28 | 23 | 0 | 2.58s | 2.53s | 6/6 |
| pluggy | 96 | 69 | 18 | 0 | 1.34s | 1.34s | 4/4 |
| pydantic | 725 | 553 | 158 | 0 | 4.08s | 3.67s | 5/5 |
| python-dotenv | 42 | 13 | 19 | 0 | 1.04s | 1.05s | 7/7 |
| requests | 79 | 57 | 16 | 0 | 1.45s | 1.45s | 8/8 |
| rich | 336 | 300 | 31 | 3 | 12.22s | 12.24s | 7/7 |
| starlette | 151 | 38 | 101 | 0 | 1.93s | 2.01s | 5/5 |
| structlog | 317 | 248 | 52 | 1 | 4.0s | 4.02s | 4/4 |
| tenacity | 10 | 8 | 2 | 0 | 0.72s | 0.71s | 6/6 |
| typer | 2353 | 248 | 2085 | 0 | 2.8s | 2.82s | 6/6 |
| werkzeug | 87 | 44 | 14 | 2 | 5.48s | 5.53s | 6/6 |

**25 repositories, 6073 references checked.** 2502 resolved, 3244 skipped as unverifiable, 10 reported as findings.

**Seeded scenarios: 139/139 passed** (16 inapplicable and skipped), **0 crashes**.

Reference totals do not equal resolved plus unknown: the remainder are references the temporal gate refuted, meaning they never resolved at the commit where the line was written and so are treated as fiction rather than drift.

## Findings reported

| Repo | Rule | Location | Target | Verified | Notes |
|---|---|---|---|---|---|
| arrow | `PY003` | docs/api-guide.rst:23 | `arrow.locale` | true positive | the module is arrow/locales.py (plural); no arrow/locale.py has ever existed |
| griffe | `PATH001` | docs/guide/contributors/architecture.md:96 | `devdeps.txt` | true positive | existed at a5ef8eb (2024-07-10), removed in 48ad843 (2024-10-11); the guide still points at it |
| httpx | `PY003` | docs/advanced/transports.md:178 | `httpx.Mounts` | true positive | documented with a full usage example since 3faa4a8 (2024-02-14); no such class in any commit |
| jinja | `PY002` | docs/sandbox.rst:81 | `Environment.call` | true positive | explicit cross-reference target; Environment defines call_filter and call_test but no call |
| rich | `PY003` | docs/source/console.rst:368 | `rich.ScreenContext.update` | true positive | ScreenContext is never exported from the rich package root; it lives at rich.console.ScreenContext |
| rich | `PY003` | docs/source/markup.rst:103 | `rich.console.Print.print` | true positive | no Print class exists in rich.console; Console.print was meant. Two occurrences |
| rich | `PY003` | docs/source/markup.rst:105 | `rich.console.Print.print` | true positive | no Print class exists in rich.console; Console.print was meant. Two occurrences |
| structlog | `PY002` | docs/standard-library.md:117 | `structlog.stdlib._NAME_TO_LEVEL` | true positive | the mapping moved to structlog._log_levels; structlog's own docstring says so |
| werkzeug | `PY002` | docs/quickstart.rst:296 | `Request.make_conditional` | true positive | make_conditional is defined on Response, not Request |
| werkzeug | `PY003` | docs/routing.rst:64 | `werkzeug.exceptions.RequestRedirect` | true positive | RequestRedirect lives in werkzeug.routing.exceptions and is not re-exported into werkzeug.exceptions |

Every finding above was checked by hand against that project's own git history and source tree: **10 of 10 reviewed**, all confirmed as real documentation defects. Those labels are the one thing on this page a human wrote; they live in `scripts/verdicts.json`. Drafted reports are in `upstream-issues.md` outside the package.

## Honest limitations

- The corpus is biased toward unusually well-maintained projects.
  The 2023 EMSE baseline (28.9% of the top-1000 GitHub repositories
  carry at least one dead documentation reference) predicts a higher
  rate across the long tail.
- The unknown column is the price of precision. References docrot
  cannot verify - external packages, dynamic attributes, example
  values - are counted and inspectable via `--show-unknown`, never
  guessed at.
- Findings were verified against each project's git history by the
  tool's author. Independent confirmation is what upstream issue
  reports are for.
