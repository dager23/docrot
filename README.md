# docrot

[![CI](https://github.com/dager23/docrot/actions/workflows/ci.yml/badge.svg)](https://github.com/dager23/docrot/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/docrot.svg)](https://pypi.org/project/docrot/)
[![Python versions](https://img.shields.io/pypi/pyversions/docrot.svg)](https://pypi.org/project/docrot/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Deterministic doc↔code drift checker for Python repositories.**

docrot finds references in your prose documentation — READMEs, `docs/`,
contributor guides, and agent context files like `CLAUDE.md`/`AGENTS.md` —
that no longer resolve against your codebase: symbols that were removed or
renamed, file paths that moved, `make`/`tox`/`npm` targets that are gone,
and APIs that were documented but never shipped.

No LLM. No side-store. Every finding is backed by static analysis and git
history, and comes with provenance: *the commit where the claim was written,
and the commit where it broke.*

```console
$ docrot
docs/advanced/transports.md:178:53  PY003 error  `httpx.Mounts` is documented but has never existed
    written at 3faa4a8 (2024-02-14)

docrot: 187 references | 82 resolved | 85 unknown (skipped) | 1 findings (1 error)
```

That output is real: on its first run against [httpx](https://github.com/encode/httpx)
(~14k stars), docrot found that the official transports documentation shows a
complete usage example for `httpx.Mounts` — a class that has never existed in
any commit of the codebase. On requests and flask, the same run produced
**zero findings** — not because docrot checked less, but because their docs
are actually in sync.

## Why

A 2023 Empirical Software Engineering study
([DOCER](https://arxiv.org/abs/2212.01479)) measured that **28.9% of the
top-1000 GitHub projects currently document a code element that no longer
exists**, and 82.3% have been in that state at some point. Since then the
problem got teeth: AI coding agents read `AGENTS.md`/`CLAUDE.md` as ground
truth on every session, so one stale path silently misdirects every run.

Adjacent tools deliberately stop short of this: doc *example* runners
(sybil, xdoctest, pytest-examples) check fenced code blocks; docstring
linters (docvet, pydoclint) check docstrings against signatures; link
checkers (lychee) check URLs; AGENTS.md linters check file structure. None
of them validate what your *prose* claims about your *code*.

## How it stays quiet: the two-gate design

False positives are what killed every previous attempt at this, so docrot
only reports when two independent gates agree:

**Gate 1 — resolution (static analysis).** References are resolved against a
[griffe](https://github.com/mkdocstrings/griffe)-backed index of your
package that understands star-import re-exports, alias chains
(`requests.Response` → `requests.models.Response`), class inheritance
(`flask.Flask.route` lives two base classes away), and instance attributes
assigned in `__init__`. Every reference gets a tri-state verdict — RESOLVED,
BROKEN, or **UNKNOWN — and UNKNOWN never becomes a finding**. External
packages, dynamic attributes, tutorial placeholders: silence, not noise.

**Gate 2 — history (git).** A broken reference is only *drift* if it once
resolved. docrot blames the doc line to the commit where the claim was
made, re-resolves the reference at that commit (by parsing file blobs — no
checkouts), and only then decides:

| resolved when written? | resolves now? | verdict |
|---|---|---|
| yes | no | **drift** — reported, with the breaking commit bisected |
| no  | no | fiction (a file the *reader* creates, pseudo-code) — silent |
| no, and it's your own package's namespace | no | **documented API that never shipped** — reported |

The result on these three: requests **0 findings**, flask **0
findings**, httpx **1 finding, a real bug** (709 references checked in
total, a few seconds per repository). The full corpus run is in
[BENCHMARKS.md](BENCHMARKS.md).

## Install & run

```bash
pip install docrot
docrot                  # zero config: checks tracked *.md, *.rst + agent files
docrot --format json    # machine-readable, stable schema
docrot --show-unknown   # audit what was skipped and why
docrot explain PY002    # rule documentation
```

Requires Python 3.10+ and git. Without git history docrot still runs, but
it says so and drops every finding to medium confidence, because the
drift-versus-fiction evidence is exactly what history provides.

### Command reference

| Invocation | Effect |
|---|---|
| `docrot` / `docrot check` | check the whole repository (the default command) |
| `docrot check docs/ README.md` | restrict the scan to those paths |
| `docrot explain PY003` | what a rule means and how to suppress it |
| `docrot anchors update` | recompute [anchor](#anchors) hashes after reviewing prose |

| Flag | Effect |
|---|---|
| `--format text\|json\|sarif\|github` | output format (default `text`) |
| `--rules core,agents,anchors` | enable only these rule packs |
| `--fail-on error\|warning\|info` | lowest severity that fails the run |
| `--strict` | medium-confidence findings also fail |
| `--no-temporal` | skip the git gate (faster, lower confidence) |
| `--show-unknown` | list every skipped reference and why |
| `--changed-only` | only docs changed against HEAD (pre-commit mode) |
| `--root PATH` | repository root (default: cwd) |

## Rules

| Rule | Fires when |
|---|---|
| `PY001` | Own-package symbol doesn't resolve (no git history to confirm) |
| `PY002` | Symbol resolved when the doc line was written — and no longer does |
| `PY003` | Own-package symbol is documented but **never existed** |
| `PATH001` | Documented path existed and is now gone |
| `PATH002` | File moved; docs still point at the old location |
| `LINK001` | Relative markdown link target missing |
| `CALL001` | Bare `func()` resolved when written, no longer resolves |
| `AG001` | Documented command target is gone (`make`/`tox`/`nox`/`poe`/`npm`/`just`/entry points/`python -m`) |
| `AG002` | Broken path in an agent context file (agents act on these) |
| `AN001`/`AN002` | Opt-in [anchors](#anchors): code changed under anchored prose |

Suppress inline with `<!-- docrot: ignore[PY002] -->`; suppressions are
counted in the report, never silent.

## CI

**GitHub Action** (findings become PR annotations, optional SARIF upload):

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0        # full history = full provenance
- uses: dager23/docrot@v0
```

**pre-commit:**

```yaml
repos:
  - repo: https://github.com/dager23/docrot
    rev: v0.1.0
    hooks:
      - id: docrot        # runs --changed-only for speed
```

Exit codes: `0` clean, `1` findings at/above `fail_on` severity, `2` usage
error. Medium-confidence findings (no git history) fail only under
`--strict`.

## Configuration

Everything is optional. Use `[tool.docrot]` in `pyproject.toml`, or a
standalone `docrot.toml` (same keys, no table header, takes precedence):

```toml
[tool.docrot]
packages = ["mypkg"]                 # default: auto-detected
exclude_docs = ["docs/archive/**"]   # changelogs are always excluded
severity = { PATH002 = "ignore" }
fail_on = "error"                    # error | warning | info
temporal = "auto"                    # auto | on | off
external_packages = ["werkzeug"]     # opt-in cross-package resolution
ignore_refs = ["flask.request.*"]     # glob-match references to skip
docs = ["**/*.md", "**/*.rst"]       # what counts as a doc
rules = { enable = ["core", "agents"], disable = ["LINK001"] }
strict = false
```

Unknown keys are reported and ignored rather than fatal. Changelogs,
migration guides and upgrade notes are always skipped: naming removed
APIs is their entire purpose.

## Anchors

For prose whose *meaning* depends on specific code (not just references),
bind a region to a symbol's implementation:

```markdown
<!-- docrot:anchor symbol=mypkg.auth.DigestAuth hash=sha256:9f2ab8... -->
Digest auth performs two requests: the first receives the challenge...
<!-- /docrot:anchor -->
```

The hash covers the AST-normalized body, so formatting and comment churn
never invalidate it — only structural change does. `docrot anchors update`
recomputes hashes after you review the prose. Anchors are strictly opt-in:
an absent anchor asserts nothing.

## What docrot deliberately does not do

- **No LLM calls, ever.** Semantic truth of prose isn't deterministically
  checkable; references and anchored claims are. docrot never guesses.
- **No docstring linting** (see [docvet](https://github.com/Alberto-Codes/docvet),
  pydoclint), **no example execution** (see [sybil](https://github.com/simplistix/sybil),
  pytest-examples), **no URL checking** (see [lychee](https://github.com/lycheeverse/lychee)).
  docrot is designed to sit alongside all of them.

## Status

Alpha (0.x). Symbol resolution is Python-only; doc formats are Markdown,
reStructuredText, and agent context files. Multi-language resolution
(tree-sitter), CLI flag introspection, `--fix` rename suggestions, and an
MCP server are planned.

Measured against 25 mature open-source projects: 6,073 references
checked, 10 findings, every one confirmed by hand as a real
documentation defect, and 139 of 139 seeded scenarios correct. The
numbers and the method are in [BENCHMARKS.md](BENCHMARKS.md).

## License

MIT
