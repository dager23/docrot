# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-12

First release.

### Added

- Two-gate drift detection. Symbol resolution is backed by
  [griffe](https://github.com/mkdocstrings/griffe) and returns a tri-state
  verdict, where `UNKNOWN` never becomes a finding: star-import re-exports,
  alias chains, class inheritance and instance attributes all resolve, and
  anything dynamic degrades to silence rather than noise. A git temporal
  gate then separates drift (resolved when the line was written, broken
  now) from fiction (never resolved), and bisects the commit where a
  reference broke so every finding carries provenance.
- Reference extraction from Markdown, reStructuredText and agent context
  files (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `.cursorrules`, Copilot
  instructions, `.claude/**`), the last of which are checked even when
  untracked, because agents read them regardless.
- Rules `PY001`/`PY002`/`PY003` for symbols, `PATH001`/`PATH002` for paths,
  `LINK001` for relative links, `CALL001` for bare call forms,
  `AG001`/`AG002` for commands and agent-file paths, and the opt-in
  `AN001`/`AN002` anchors, which bind prose to an AST-normalized hash of a
  symbol body so formatting churn does not invalidate it.
- Command validation across make, tox, nox, poe, pdm, npm, pnpm, yarn,
  just, console entry points, `python -m` and pytest paths.
- Output as text, JSON (schema 1), SARIF 2.1.0 and GitHub workflow
  commands; inline suppressions that are counted rather than silent;
  `docrot explain`; zero-config discovery; `--changed-only` for pre-commit.
- A GitHub Action with SARIF upload and shallow-clone deepening, a
  pre-commit hook, and a corpus harness (`scripts/corpus_check.py`) that
  seeds true positives and true negatives from facts about real projects.

### Verified

See [BENCHMARKS.md](BENCHMARKS.md) for the measured corpus run. Confirmed
findings in real projects include `httpx.Mounts`, documented with a
complete usage example since 2024 and never implemented in any commit, and
two broken Sphinx cross-references in rich.

### Notes on precision

Several classes of false positive were found by running against real
repositories and are now regression-tested:

- `PY003` claims a symbol never existed, which must be provable. Probing
  only the doc line's introduction commit reported symbols removed *before*
  the line was written as never having existed - on anthropic-sdk-python
  that produced 13 false positives in `MIGRATION.md`. Absence is now proven
  across history, and migration, upgrade and porting guides join changelogs
  as documents whose purpose is to name removed APIs.
- Names like `settings.json` parse as dotted symbols. Symbol resolution is
  tried first, which is what keeps `Response.json` from being mistaken for
  a file, but such references now continue to the temporal gate as path
  candidates so a deleted documented file is still caught.
- reStructuredText roles of the form ``:meth:`text <pkg.real.target>` ``
  were extracting the display text instead of the target.
- Package directories are matched by their true on-disk casing, so `Flask`
  and `flask` do not collide on case-insensitive filesystems.
- Dotted names that the code itself uses as string literals, such as
  setuptools entry-point groups, are not symbol claims.

### Reporting

Degradation is never silent. A missing git history, a disabled temporal
gate, an empty documentation set and an undetected package each produce an
explicit note, so a green run in the wrong directory cannot be mistaken
for a clean repository.
