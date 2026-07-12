# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-07-12

### Added

- Two-gate drift detection engine: griffe-backed tri-state symbol
  resolution (RESOLVED/BROKEN/UNKNOWN) plus a git temporal gate that
  separates drift (resolved when written, broken now) from fiction
  (never resolved), with commit provenance and breaking-commit bisection.
- Reference extraction from Markdown (markdown-it-py), reStructuredText
  (inline literals + Sphinx roles), and agent context files
  (CLAUDE.md, AGENTS.md, GEMINI.md, .cursorrules, copilot instructions).
- Rules: PY001/PY002/PY003 (symbols, including documented-but-never-shipped
  APIs), PATH001/PATH002 (paths, move detection with exact-location history
  confirmation), LINK001 (doc-relative markdown links), CALL001 (bare
  call-forms), AG001 (make/tox/nox/poe/pdm/npm/pnpm/yarn/just/entry-point/
  `python -m`/pytest command targets), AG002 (agent-file paths),
  AN001/AN002 (opt-in AST-normalized hash anchors).
- Precision gates hardened on real repositories: true-case package
  detection (Flask/flask on case-insensitive filesystems), implicit alias
  resolution for base-class walks, empty-MRO-with-declared-bases treated
  as incomplete, unique-basename-only relocation matching, dot-directory
  path normalization (`.github/...`), runtime-identifier string-literal
  gate (entry-point groups like `flask.commands` are not symbol claims).
- Output formats: text, JSON (schema v1), SARIF 2.1.0, GitHub workflow
  commands; inline suppressions with accounting; `docrot explain <RULE>`.
- Zero-config discovery (packages from pyproject, tracked docs via git),
  `--changed-only` pre-commit mode, `--strict`, `--show-unknown`.
- GitHub Action (`action.yml`) with SARIF upload and shallow-clone
  deepening; `.pre-commit-hooks.yaml`; benchmark harness
  (`scripts/bench.py`).

### Changed (during the benchmark campaign)

- Agent context files (CLAUDE.md, AGENTS.md, .claude/*.md, copilot
  instructions) are checked even when untracked or gitignored — agents
  read them regardless.
- Historical resolution no longer uses grep-evidence fallback for
  module-rooted references: "a def of that name existed somewhere" is not
  "resolved as written" (rich.ScreenContext.update lesson).
- Inline spans followed by an ellipsis ("start typing `typer.File`...")
  are intentional prefixes, never symbol claims.
- Explicit markdown links get no bare-filename exemption: a link is a
  concrete claim that its target exists.
- ASCII-only console output (Windows cp1252 terminals).

### Verified (see BENCHMARKS.md)

Nine-repo full-history benchmark (httpx, requests, flask, click,
starlette, rich, typer, pydantic, anthropic-sdk-python): ~4,880
references checked, 4 findings, all four manually confirmed as genuine
documentation defects (httpx.Mounts never existed;
rich.ScreenContext.update and rich.console.Print.print ×2 are broken
Sphinx roles that never resolved). 0 false positives.
