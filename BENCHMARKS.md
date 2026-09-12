# Benchmarks

Verification of docrot against real open-source repositories, per the
two-gate design described in the README. Run 2026-07-12 with docrot 0.1.0 on
full-history clones (Windows 10, Python 3.13, subprocess git).

Reproduce with:

```bash
python scripts/bench.py <workdir>
```

## Corpus results

| Repo | Refs checked | Resolved | Unknown (skipped) | Findings | Cold | Warm |
|---|---|---|---|---|---|---|
| httpx | 187 | 82 | 86 | **1** | 1.5s | 1.4s |
| requests | 63 | 41 | 19 | 0 | 0.6s | 0.6s |
| flask | 438 | 152 | 205 | 0 | 6.5s | 6.0s |
| click | 197 | 119 | 78 | 0 | 2.0s | 1.3s |
| starlette | 125 | 41 | 84 | 0 | 1.8s | 1.0s |
| rich | 336 | 300 | 31 | **3** | 6.2s | 3.5s |
| typer | 2470 | 247 | 2184 | 0 | 3.8s | 2.4s |
| pydantic | 1016 | 629 | 387 | 0 | 4.0s | 2.3s |
| anthropic-sdk-python | 52 | 20 | 32 | 0 | 12.3s* | 2.4s |

\* first run after clone includes git warm-up.

Totals: **~4,880 references checked, 4 findings.**

## Finding-by-finding ground truth

Every finding was manually verified against the repository's git history.

| # | Repo | Finding | Ground truth | Label |
|---|---|---|---|---|
| 1 | httpx | PY003 `httpx.Mounts` (docs/advanced/transports.md:178) | Documented with a complete usage example since commit 3faa4a8 (2024-02-14). `git log -S "class Mounts"` over the full history is empty: the class never existed in any commit. | **TP** |
| 2 | rich | PY003 `rich.ScreenContext.update` (docs/source/console.rst:368) | The Sphinx role `` :meth:`~rich.ScreenContext.update` `` never resolved: `ScreenContext` lives at `rich.console.ScreenContext` and has never been exported from the `rich` package root (verified at the introducing commit and at HEAD). | **TP** |
| 3 | rich | PY003 `rich.console.Print.print` (docs/source/markup.rst:103) | No class `Print` has ever existed in `rich.console`; the role is a typo for `Console.print`, shipped in the rendered docs. | **TP** |
| 4 | rich | PY003 `rich.console.Print.print` (docs/source/markup.rst:105) | Same defect, second occurrence. | **TP** |

**Finding precision: 4/4 (100%) on this corpus.**

## False positives found and eliminated during this campaign

The benchmark is also a hostile test: every candidate false positive
became a fix and a regression test.

- `typer.File` — the typer docs say "start typing `typer.File`..." as an
  editor-autocomplete example. A trailing ellipsis now marks a span as an
  intentional prefix, never a symbol claim.
- `flask.commands` — a setuptools entry-point *group name* the code uses
  as a string literal, not a symbol. Dotted names that appear as quoted
  string literals in the package source are excluded.
- Flask's capitalized PyPI name (`Flask`) colliding with the `flask`
  import package on case-insensitive filesystems.
- Sphinx-style base-class chains (`flask.Flask.route` lives two classes
  away) requiring implicit alias resolution; an empty MRO with declared
  bases is treated as *unknown*, never *broken*.
- MkDocs doc-relative links; `.github/`-style dot directories; ambiguous
  basename matches (`htmlcov/index.html`); parenthesized pseudo-paths.

## Honest limitations

- Four findings is a small numerator; the 100% figure is evidence of the
  design posture (silence over noise), not a statistical guarantee. The
  corpus is biased toward exceptionally well-maintained projects — the
  EMSE 2023 baseline (28.9% of top-1000 repos carry ≥1 dead doc
  reference) predicts more findings on the long tail.
- The unknown column is the price of precision: references docrot cannot
  verify (external packages, example variables, opaque runners) are
  counted and inspectable via `--show-unknown`, never guessed at.
- Labeling was performed by the tool's author against git history;
  independent confirmation is what upstream issue reports are for.
