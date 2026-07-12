# PLAN.md — `docrot`

**A deterministic doc↔code drift checker for Python repositories.**

> Flags prose documentation whose references to code — symbols, file paths, commands — no longer resolve against the codebase. No LLM. No side-store. CI-enforceable, zero-config first run.

**Status:** approved research ([research-deliverable.md](research-deliverable.md)); this document is the implementation blueprint.
**Package name:** `docrot` (PyPI availability confirmed 2026-07-12; fallbacks `deadrefs`, `anchorlint`).
**License:** MIT. **Python:** 3.10+.

---

## Table of contents

1. [Summary & thesis](#1-summary--thesis)
2. [Goals and non-goals](#2-goals-and-non-goals)
3. [User experience](#3-user-experience)
4. [System architecture](#4-system-architecture)
5. [Component design](#5-component-design)
6. [Data model & schemas](#6-data-model--schemas)
7. [Rules catalog](#7-rules-catalog)
8. [Configuration reference](#8-configuration-reference)
9. [Performance & caching](#9-performance--caching)
10. [Testing & quality strategy](#10-testing--quality-strategy)
11. [Benchmark & proof-of-value plan](#11-benchmark--proof-of-value-plan)
12. [Packaging, CI/CD, release](#12-packaging-cicd-release)
13. [Repository layout](#13-repository-layout)
14. [Milestones & acceptance criteria](#14-milestones--acceptance-criteria)
15. [Post-v1 roadmap](#15-post-v1-roadmap)
16. [Risks & mitigations](#16-risks--mitigations)
17. [Open design questions (with recommendations)](#17-open-design-questions)

---

## 1. Summary & thesis

Prose documentation makes checkable claims about code: `` `httpx.Mounts` ``, "bump the version in `__version__.py`", "run `make lint`". A 2023 EMSE study measured 28.9% of the top-1000 GitHub repos carrying at least one such reference that no longer resolves. Agent context files (CLAUDE.md/AGENTS.md) made this acute: agents consume these claims as ground truth on every session.

No OSS tool validates prose→code references. Executable examples (sybil), docstrings (docvet), URLs (lychee), and explicit Sphinx roles are covered; inline prose references are not.

`docrot` closes this with a **two-gate architecture** validated by our spike on requests/flask/httpx:

- **Gate 1 — temporal (git):** a reference is only *drift* if it resolved at some past revision. Placeholder files (`views.py` in tutorials), external names (`fetch()`), and pseudo-code never resolved, so they are never flagged. This gate is what killed the false-positive problem that sank the academic prototype (DOCER).
- **Gate 2 — resolution (static analysis):** "no longer resolves" must be judged by a resolver that understands star-imports, alias/re-export chains, class inheritance, and `__init__` instance attributes — griffe-grade, not grep. Our spike reproduced every historical false-positive class and each maps to a known static-analysis technique.

A finding is reported only when **both** gates agree, with provenance: *where it last resolved, when it broke*.

---

## 2. Goals and non-goals

### Goals (v1)

- **G1.** Detect broken references to Python symbols, file paths, and runnable commands in `*.md`, `*.rst`, and agent context files, with measured precision (<5% false positives on the benchmark corpus, see §11).
- **G2.** Zero-config first run: `uvx docrot` in a repo root produces useful output with no setup.
- **G3.** CI-native: deterministic exit codes, JSON/SARIF/GitHub-annotation output, pre-commit hook, GitHub Action.
- **G4.** Every finding carries *evidence* (the commit where the reference last resolved) and supports inline suppression.
- **G5.** Fast enough for pre-commit on changed files (<1s warm) and full-repo CI runs (<5s warm, <30s cold on flask-sized repos).
- **G6.** Two runtime dependencies max (`griffe`, `markdown-it-py`); pure Python; Windows/macOS/Linux.

### Non-goals (explicitly out of scope)

- **N1.** No LLM calls, ever, in the core. Semantic truth of prose ("this function is fast") is not checkable deterministically; we check *references and anchored claims only*. This honesty is the product's identity.
- **N2.** No docstring quality/consistency checking (docvet, pydoclint, ruff-D own this).
- **N3.** No execution of code examples (sybil/xdoctest/pytest-examples own this).
- **N4.** No URL/link checking beyond repo-relative file targets (lychee owns URLs).
- **N5.** No auto-rewriting of docs in v1 (`--fix` is roadmap; suggestions are output, not applied).
- **N6.** No AGENTS.md style/structure opinions (the agents-lint family owns "your file should have a Setup section"). We validate facts, not style.
- **N7.** Non-Python source languages in v1 (tree-sitter multi-language is post-v1; the doc formats are language-agnostic already).

---

## 3. User experience

### 3.1 CLI

```
docrot [check] [PATHS...]        # default command; PATHS default to repo root
docrot check --format json|sarif|github|text
docrot check --rules core,agents --no-temporal --strict
docrot check --changed-only      # pre-commit mode: only docs touched in the index
docrot explain PY002             # rule documentation in the terminal
docrot anchors update [PATHS]    # recompute opt-in anchor hashes (anchors pack)
docrot cache clear|info
```

Exit codes: `0` clean · `1` findings at/above fail severity · `2` usage/config error · `3` internal error.

### 3.2 Sample output (text format)

```
docs/advanced/transports.md:178:47  PY002 error  `httpx.Mounts` does not resolve
    resolved at 9a1c3f2 (2024-11-02) · last seen at c40e191 · broken since 41d0e77 (2025-01-15)
    hint: class removed from httpx._transports; nearest current name: Client(mounts=...)

.github/CONTRIBUTING.md:164:29  PATH002 warning  `__version__.py` not at documented location
    found matching file: httpx/__version__.py — reference resolves by basename; consider full path

AGENTS.md:23:5  AG001 error  `make integration-test` — no such Make target
    Makefile targets: test, lint, format, docs (target removed in 8f77aa0, 2026-03-02)

docs/index.md — 47 references checked, 44 resolved, 1 skipped (external), 2 findings
────────────────────────────────────────────────────────────────────────
docrot: 312 references · 296 resolved · 9 unknown (skipped) · 7 findings (5 error, 2 warning)
```

(The httpx narrative above is illustrative of the format; real provenance comes from git.)

### 3.3 Zero-config behavior

Without any configuration, `docrot`:

1. Detects package roots from `pyproject.toml` (`[project.name]`, setuptools/hatch/flit/pdm/uv source config) or by scanning for top-level `__init__.py` packages under `.`, `src/`.
2. Scans all **git-tracked** `*.md` and `*.rst` files plus known agent files (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `.cursorrules`, `.github/copilot-instructions.md`, `.claude/**/*.md`), excluding changelog-like names (`CHANGELOG*`, `CHANGES*`, `HISTORY*`, `NEWS*`, `RELEASES*`) and anything `.gitignore`d.
3. Enables rule packs `core` + `agents`; temporal gate `auto` (on when git history is available, degrades gracefully otherwise — see §5.4).
4. Fails on `error`-severity findings only.

### 3.4 Suppression

- Line-level: `<!-- docrot: ignore -->` (same line or immediately preceding line); with optional rule filter `<!-- docrot: ignore[PY002] -->`.
- Block-level fence: `<!-- docrot: off -->` … `<!-- docrot: on -->`.
- Path/pattern-level: config (`ignore_refs = ["flask.request.*"]`, `ignore_docs = ["docs/archive/**"]`).
- Every suppression is counted and visible in `--format json` (auditable, like ruff's `noqa` accounting).

---

## 4. System architecture

```
                ┌────────────────────────────────────────────────────────┐
                │                      docrot check                      │
                └────────────────────────────────────────────────────────┘
                                        │
        ┌───────────────┬───────────────┼────────────────┬───────────────┐
        ▼               ▼               ▼                ▼               ▼
  ┌───────────┐  ┌────────────┐  ┌───────────────┐  ┌───────────┐  ┌───────────┐
  │  Config    │  │ Discovery  │  │  Extraction   │  │ Indexes   │  │  Git      │
  │ pyproject/ │  │ doc files, │  │ md/rst parse →│  │ symbols   │  │ temporal  │
  │ docrot.toml│  │ pkg roots  │  │ RawSpan →     │  │ (griffe), │  │ service   │
  └───────────┘  └────────────┘  │ Reference     │  │ file tree,│  │ blame/    │
                                  │ (classified)  │  │ commands  │  │ cat-file  │
                                  └───────┬───────┘  └─────┬─────┘  └─────┬─────┘
                                          │                │              │
                                          ▼                ▼              │
                                  ┌──────────────────────────────┐        │
                                  │        Resolution engine      │        │
                                  │ verdict: RESOLVED | BROKEN |  │        │
                                  │          UNKNOWN              │        │
                                  └──────────────┬───────────────┘        │
                                                 │ BROKEN candidates      │
                                                 ▼                        │
                                  ┌──────────────────────────────┐        │
                                  │   Temporal gate (Gate 1)      │◄───────┘
                                  │ ever-resolved? → drift/fiction│
                                  └──────────────┬───────────────┘
                                                 ▼
                                  ┌──────────────────────────────┐
                                  │  Rules → Findings → Reporter  │
                                  │  text / json / sarif / github │
                                  └──────────────────────────────┘
```

Pipeline contract: every stage is a pure function over immutable dataclasses (§6), so each is independently testable and the whole pipeline is deterministic given (worktree state, git history, config).

---

## 5. Component design

### 5.1 Discovery (`docrot/discovery.py`)

- **Doc files:** `git ls-files` filtered by glob set (fallback: `Path.rglob` honoring a minimal ignore list when not a git repo). Agent-file set is a maintained constant.
- **Package roots:** ordered detection: (1) `[tool.docrot].packages`; (2) build-backend config (setuptools `packages`/`package-dir`, hatch `packages`, flit/pdm/uv equivalents); (3) heuristic scan for `<name>/__init__.py` at `.` and `src/`. Emits `PackageRoot(name, path)` list. Namespace packages: supported via griffe's search-path loading.
- **Command sources:** presence-detect `Makefile`, `tox.ini`, `noxfile.py`, `package.json`, `pyproject.toml` (`[project.scripts]`, `[project.entry-points]`, `[tool.poe.tasks]`, `[tool.pdm.scripts]`, `[tool.uv]`), `justfile`.

### 5.2 Extraction (`docrot/extract/`)

**Markdown (`markdown.py`):** parse with `markdown-it-py` (CommonMark + GFM tables). We consume the token stream:

- `code_inline` tokens → candidate spans. Block tokens carry `map` line ranges; inline column offsets are recovered by scanning the source line for the span text (with occurrence counting for duplicates).
- `fence`/`code_block` tokens → **not** scanned for symbol refs (that's example territory), **except**: fences tagged `bash|sh|shell|console|text` are passed to the *command extractor* (§5.5), because "run this" blocks are claims about the repo, not example programs.
- `link` tokens with relative, non-URL targets → path references (rule LINK001).
- HTML comment tokens → suppression directives and anchor blocks.
- Front-matter (if present) → per-file config (e.g. `docrot: off`).

**reStructuredText (`rst.py`), v1 scope:** regex-level extraction of (a) ``` ``inline literals`` ```, (b) explicit roles `` :py:func:`x` ``, `` :class:`~pkg.mod.X` `` (strip `~`/`!` prefixes), (c) `:file:` roles. Line/col from match offsets. Full docutils AST parsing is deferred (§15) — roles are already high-precision, and Sphinx-built projects have partial coverage from nitpick mode; our marginal value there is lower by design.

**Classification (`classify.py`):** each raw span becomes at most one `Reference`:

| Pattern (anchored, full-span) | Kind | Examples |
|---|---|---|
| `IDENT(.IDENT)+ (\(\))?` | `symbol` (dotted) | `flask.Flask.route`, `Session.mount()` |
| `IDENT\(\)` | `symbol` (call-form) | `create_app()` |
| contains `/`, or `\w+\.(py\|toml\|cfg\|ini\|md\|rst\|txt\|yml\|yaml\|json\|lock)$` | `path` | `src/app.py`, `pyproject.toml` |
| first token ∈ known runners or entry-point names | `command` | `make test`, `tox -e py312`, `httpx --help` |
| anything else | *dropped* | `timeout`, `PR #12`, `O(n)` |

Hard precision rules learned from the spike:

- **Bare single words are never symbol candidates** (the `timeout`/`id` noise class), with one exception: bare call-forms `foo()` (still gated by temporal, §5.4).
- Spans containing spaces, `=`, or shell metacharacters are never symbols (may still be commands).
- A span matching both symbol and path grammars (e.g. `Response.json` — the spike's funniest FP) is tried **symbol-first**, and only becomes a path candidate if it contains `/` or a non-code file extension. `.py` endings prefer path; `.json`-like ambiguity prefers symbol.
- Docs whose *filename* matches the changelog set are skipped entirely (DOCER's #1 FP class: historical statements are supposed to reference dead code).

### 5.3 Resolution engine (`docrot/resolve/`)

Tri-state verdict per reference — this is central to precision:

- **RESOLVED** — affirmatively found.
- **BROKEN** — affirmatively *not* found in a namespace we fully understand.
- **UNKNOWN** — outside our knowledge (external package not configured, dynamic attribute, unresolvable base class). **UNKNOWN never becomes a finding.** It is counted and inspectable (`--show-unknown`), preserving honesty without noise.

**Python symbols (`python.py`), backed by griffe:**

1. Load each `PackageRoot` with `griffe.load(...)` (static analysis; no imports executed). Griffe natively provides the four capabilities our spike proved necessary: alias/re-export resolution, wildcard-import expansion, class member inheritance, and instance attributes assigned in `__init__`.
2. Resolution of a dotted ref `a.b.c`:
   - If `a` is a local package name → walk members, following aliases (`resolve_alias`), including inherited members. Success → RESOLVED. Failure at a fully-analyzed boundary → BROKEN. Failure at a dynamic boundary (`__getattr__` present, unresolvable alias target, base class from an unloaded package) → UNKNOWN.
   - If `a` is a *class name* found anywhere in the index (spike's `Session.mount` case) → attempt `Class.member` against every class of that name; any success → RESOLVED; all-fail with all classes fully analyzed → BROKEN, else UNKNOWN.
   - If `a` is an unknown name → UNKNOWN (external), unless listed in `external_packages` config, in which case griffe loads that installed package and the same walk applies (how `flask.request.args` → werkzeug can be made checkable *opt-in*).
   - Call-form bare names `foo()` → RESOLVED if the name exists as any callable in the index; otherwise UNKNOWN (never BROKEN without a dot — too weak a claim; temporal gate may upgrade it, §5.4).
3. The loaded griffe module tree is serialized into a compact internal `SymbolIndex` (name → kind, parent, aliases, "fully_analyzed" flag) so historical indexes (§5.4) and caches share one format.

**Paths (`paths.py`):**

- Exact repo-relative existence first (against `git ls-files` set, so ignored/untracked junk doesn't mask drift).
- Bare filenames (`auth.py`, `__version__.py` — spike FP class): resolve by unique-basename lookup → RESOLVED (optionally PATH002 "resolves by basename" info-level note when the doc implies a location that's wrong).
- Globs/placeholders (`*`, `<name>`, `{...}`) → UNKNOWN.
- Paths under directories that don't exist and never existed → UNKNOWN (fiction, e.g. tutorial `yourapplication/views.py`); temporal gate decides (§5.4).

**Commands (`commands.py`):**

First token dispatch:

| Runner | Validation |
|---|---|
| `make <target>` | target list parsed from `Makefile` (regex on `^[A-Za-z0-9._-]+:` minus pattern rules; `include` followed one level) |
| `tox -e <env>` | envlist from `tox.ini`/`pyproject` |
| `nox -s <session>` | AST-parse `noxfile.py` for `@nox.session` names |
| `poe <task>` / `pdm run` / `uv run <script>` | pyproject task tables |
| `npm|pnpm|yarn run <script>` | `package.json` scripts |
| `just <recipe>` | `justfile` recipes |
| `python -m <mod>` | module resolvable in SymbolIndex or stdlib list |
| `<entry-point> ...` | name ∈ `[project.scripts]`/`[project.gui-scripts]` |
| `pytest <path>` etc. with path args | path resolution as above |
| anything else | UNKNOWN |

Only the *addressable unit* (target/script/module name) is validated in v1 — never flags/options (argparse/click flag introspection is post-v1, §15). This keeps the command pack near-zero-FP.

### 5.4 Temporal gate (`docrot/temporal/`) — Gate 1, the differentiator

Purpose: convert "doesn't resolve now" into one of **drift** (existed before → finding) or **fiction** (never existed → skip). Runs only on BROKEN/UNKNOWN candidates that survive Gate 2 triage, so its cost is proportional to *problems*, not to doc size.

Algorithm per candidate reference in doc `D` at line `L`:

1. **Introduction commit:** `git blame -L {L},{L} --porcelain -- D` → commit `C` where the line was last changed (the doc author's claim was made against the tree at `C`).
2. **Targeted historical resolution at `C`** (cheap path, no full checkout):
   - Symbols: map dotted ref to candidate file paths at `C` (`pkg/mod.py`, `pkg/mod/__init__.py` for each prefix split), fetch blobs via `git cat-file blob C:path`, AST-parse the single blob, check the terminal name (defs/classes/assigns/imports, one-level re-export chase with bounded depth 3 across blobs). Result: resolved-then / not.
   - Paths: `git cat-file -e C:path` (plus basename search in `git ls-tree -r C` when needed — cached per commit).
   - Commands: fetch the runner file blob at `C` (Makefile, package.json…) and re-run the same parser.
3. **Fallback full index:** if the targeted chase hits its depth bound or dynamic constructs, build a full `SymbolIndex` at `C` via `git worktree add --detach <cachedir>/wt <C>` (worktrees are cheap; removed after). Indexes are cached keyed by the **package subtree hash** (`git rev-parse C:src/pkg`), so any number of commits sharing identical package trees reuse one index.
4. **Verdict combination:**

| Resolved at C? | Resolves now? | Verdict | Rule |
|---|---|---|---|
| yes | no | **drift — finding** (confidence HIGH) | PY002/PATH001/AG001… |
| no | no | fiction — skipped (UNKNOWN) | — |
| — (no git / shallow) | no | degraded mode, see below | PY001 at MEDIUM |

5. **Break provenance (for the report):** first-parent bisection between `C` and `HEAD` over the *subtree hashes* of the owning package (only commits that changed the package can change the verdict), binary-searching the earliest commit where resolution fails → "broken since `<sha>` (`<date>`)". Bounded to ≤ `log2(n)` targeted resolutions; skipped under `--fast`.

**Degraded modes (explicit, honest):**

- *Shallow clone* (CI default): attempt `git fetch --deepen=…` escalation only in the GitHub Action (which controls its environment); the library itself reports "temporal gate unavailable (shallow history before <sha>)" and downgrades would-be findings to MEDIUM confidence, which fail CI only under `--strict`. Action docs recommend `fetch-depth: 0`.
- *No git:* only PY001-class findings (own-package dotted refs BROKEN by Gate 2) are reported, at MEDIUM.

### 5.5 Anchors pack (`docrot/rules/anchors.py`) — opt-in, Swimm-without-the-SaaS

For prose whose *meaning* (not just references) depends on specific code, authors can bind a doc region to a symbol's implementation:

```markdown
<!-- docrot:anchor symbol=httpx._auth.DigestAuth hash=sha256:9f2ab8... -->
Digest auth performs two requests: the first receives the challenge...
<!-- /docrot:anchor -->
```

- `docrot anchors update` computes/updates hashes = sha256 of the **normalized symbol body** (griffe-extracted source, whitespace/comment-normalized via AST round-trip, so formatting churn doesn't invalidate).
- Check: AN001 (hash mismatch → "code under this explanation changed; review prose"), AN002 (anchor target gone).
- This is deliberately opt-in (lesson from the Serena rejection: mandatory side-metadata "may not happen"; opt-in anchors have neutral failure — an absent anchor asserts nothing false).
- Mechanism note: hash-of-normalized-body is deliberately distinct from Swimm's patented multi-signal similarity scoring (risk §16.3).

### 5.6 Reporting (`docrot/report/`)

- `text`: grouped by file, color if TTY, `--quiet` summary-only.
- `json`: full `Finding` records (schema §6.3), machine-stable, versioned `"schema": 1`.
- `sarif`: SARIF 2.1.0 → GitHub code-scanning annotations on PRs (this is the highest-leverage CI surface; agents-lint family has nothing here).
- `github`: `::error file=,line=,col=::` workflow commands for zero-setup annotation.

---

## 6. Data model & schemas

All internal types are frozen dataclasses (`docrot/model.py`), fully typed, no runtime deps.

### 6.1 Core types

```python
class RefKind(Enum): SYMBOL_DOTTED, SYMBOL_CALL, PATH, COMMAND, ANCHOR, LINK

class Verdict(Enum): RESOLVED, BROKEN, UNKNOWN

class Confidence(Enum): HIGH, MEDIUM, LOW

@dataclass(frozen=True)
class RawSpan:      # extraction output
    doc: Path; line: int; col: int; text: str; context: SpanContext
    # SpanContext: heading_path: tuple[str,...], in_agent_file: bool, fence_lang: str|None

@dataclass(frozen=True)
class Reference:    # classification output
    span: RawSpan; kind: RefKind; target: str          # normalized (parens stripped, ~ stripped)

@dataclass(frozen=True)
class Resolution:   # gate 2 output
    ref: Reference; verdict: Verdict
    resolved_as: str | None       # e.g. "requests.models.Response.iter_lines (via alias requests.Response)"
    boundary: str | None          # why UNKNOWN: "external:werkzeug", "dynamic:__getattr__", ...

@dataclass(frozen=True)
class TemporalEvidence:           # gate 1 output
    introduced_at: CommitRef      # blame commit for the doc line
    resolved_at_introduction: bool
    broken_since: CommitRef | None
    last_resolved_at: CommitRef | None

@dataclass(frozen=True)
class Finding:
    rule: str; severity: Severity; confidence: Confidence
    ref: Reference; resolution: Resolution
    evidence: TemporalEvidence | None
    suggestion: str | None        # "did you mean ...", "file moved to ..."
    suppressed: bool
```

### 6.2 SymbolIndex (shared by live & historical resolution, cacheable)

```python
@dataclass
class SymbolIndex:
    packages: dict[str, ModuleNode]   # trees of Node(name, kind, children, alias_target, fully_analyzed)
    class_bases: dict[str, list[str]] # for MRO walking
    callables: set[str]               # bare-name membership for call-forms
    source: str                       # "worktree" | commit sha
    subtree_keys: dict[str, str]      # pkg -> git subtree hash (cache key)
```

Serialization: versioned JSON in the cache dir; invalidated by subtree hash mismatch, never by time.

### 6.3 JSON output schema (stable contract, excerpt)

```json
{ "schema": 1, "summary": {"refs": 312, "resolved": 296, "unknown": 9, "findings": 7},
  "findings": [{
    "rule": "PY002", "severity": "error", "confidence": "high",
    "doc": "docs/advanced/transports.md", "line": 178, "col": 47,
    "target": "httpx.Mounts", "kind": "symbol",
    "evidence": {"introduced_at": "9a1c3f2", "broken_since": "41d0e77",
                  "last_resolved_at": "c40e191"},
    "suggestion": null, "suppressed": false }]}
```

---

## 7. Rules catalog

| ID | Pack | Default severity | Fires when |
|---|---|---|---|
| **PY001** | core | error* | Own-package dotted symbol BROKEN (current state; *MEDIUM confidence without temporal → error only under `--strict`) |
| **PY002** | core | error | Symbol BROKEN now **and** resolved at doc-line introduction (temporal-confirmed drift) |
| **PY003** | core | info | Symbol resolves only via deprecated alias / moved location (suggestion: canonical name) |
| **PATH001** | core | error | Documented path missing now, existed at introduction |
| **PATH002** | core | warning | Path resolves only by basename at a different location than written |
| **LINK001** | core | error | Relative markdown link target missing from tree |
| **CALL001** | core | warning | Bare call-form `foo()` resolved at introduction, no longer resolves anywhere |
| **AG001** | agents | error | Command target (make/tox/nox/poe/npm/just/entry-point/`python -m`) not found |
| **AG002** | agents | error | Path reference inside an agent context file BROKEN (PATH001 logic, elevated: agents act on these) |
| **AG003** | agents | warning | Agent file references a harness resource that doesn't exist (`.claude/agents/x.md`, skill dirs, hook scripts) |
| **AN001** | anchors | warning | Anchor hash mismatch (implementation changed under anchored prose) |
| **AN002** | anchors | error | Anchor target symbol no longer resolves |

Pack defaults: `core` + `agents` on; `anchors` on only when anchor comments are present. Every rule documented via `docrot explain <ID>` and in docs, each with a "why this is trustworthy" section stating its gates — the credibility contract.

---

## 8. Configuration reference

`[tool.docrot]` in `pyproject.toml`, or `docrot.toml` (identical schema, takes precedence). Everything optional.

```toml
[tool.docrot]
packages = ["httpx"]                  # default: auto-detect (§3.3)
docs = ["README.md", "docs/**/*.md", "AGENTS.md"]   # default: tracked md/rst + agent files
exclude_docs = ["docs/archive/**"]    # changelog-likes always excluded unless re-included
rules = { enable = ["core", "agents"], disable = ["PATH002"] }
severity = { AG001 = "error", CALL001 = "ignore" }
fail_on = "error"                     # error | warning | info
temporal = "auto"                     # auto | on | off
external_packages = ["werkzeug"]      # opt-in resolution across installed deps
ignore_refs = ["flask.request.*"]     # glob on normalized target
strict = false                        # MEDIUM-confidence findings also fail
cache_dir = ".docrot_cache"           # gitignore'd; CI-restorable
```

Precedence: CLI flags > `docrot.toml` > `pyproject.toml` > defaults. Config is validated with actionable errors (unknown keys warn, don't crash).

---

## 9. Performance & caching

Budgets (flask-sized repo: ~25 modules, ~80 doc files): **cold < 30s, warm < 5s, `--changed-only` < 1s.** Enforced by a benchmark test in CI (§10).

- **Live SymbolIndex:** built once per run; cached by package subtree hash — unchanged package ⇒ zero griffe work on next run.
- **Historical resolution:** targeted blob-parse path (§5.4.2) avoids checkouts entirely for the common case; full historical indexes only on fallback, cached by subtree hash (many commits share trees).
- **Blame batching:** one `git blame --porcelain` per doc file (not per line), parsed once.
- **Extraction cache:** per-doc blob sha → extracted `Reference` list.
- **Parallelism:** doc extraction and per-file resolution parallelized with `concurrent.futures` (process pool for extraction is overkill; threads suffice — work is subprocess-git and C-accelerated parsing). Git subprocess calls are the bottleneck; batched and memoized.
- Cache is content-addressed → **no TTLs, no staleness bugs in the staleness tool** (would be embarrassing).

---

## 10. Testing & quality strategy

- **Unit tests** per component; pytest, no network, no cloning.
- **The FP regression corpus (the crown jewel):** every false-positive class discovered in the spike becomes a permanent fixture asserting *not flagged*:
  - star-import re-export (`httpx.Client` via `from ._client import *`)
  - alias chain (`requests.Response.iter_lines` via `requests.models`)
  - cross-module inheritance (`flask.Flask.route` via `Scaffold`)
  - instance attribute (`Response.raw` assigned in `__init__`)
  - tutorial placeholder paths (`views.py`, `hello.py` never in tree)
  - foreign-language spans (`fetch()`, `XMLHttpRequest()`)
  - extension ambiguity (`Response.json` is a symbol, not a file)
  - changelog historical references
  - basename-elsewhere (`__version__.py` under package dir)
  Plus true-positive fixtures (the `httpx.Mounts` shape) asserting *flagged with correct provenance*.
- **Temporal fixtures:** git repos constructed in `tmp_path` by scripted commits (helper builds "symbol added → doc written → symbol removed" histories); assert PY002 evidence commits exactly.
- **Golden e2e:** miniature repos under `tests/golden/*/` with `expected.json`; run the real CLI, diff normalized output. Windows CI catches path-separator regressions (spike output showed `docs\api.rst` — normalization to POSIX in all reports).
- **Property tests** (hypothesis, dev-only): extractor total on arbitrary markdown; classifier never throws; suppression parser round-trips.
- **Static gates:** `mypy --strict`, `ruff check` + `ruff format`, 90% coverage floor.
- **Self-hosting:** `docrot` runs on its own repo in CI from M1 onward (dogfood gate).

---

## 11. Benchmark & proof-of-value plan

Runs at M6, gates the v1.0 release, and doubles as launch material:

1. **Corpus:** 20 popular repos, stratified: markdown-first (httpx, fastapi, mkdocs, rich, typer, pydantic), Sphinx/rst (requests, flask, click), agent-file-bearing repos (sampled from the arXiv:2511.12884 dataset), and 4 mid-size (<5k stars) repos.
2. **Metrics:** per-repo findings labeled TP/FP by hand (labeling sheet in-repo, reviewable); **precision target ≥95%**; UNKNOWN rate recorded (transparency about coverage); runtime cold/warm vs budgets; comparison run of DOCER_tool and agents-lint on the same corpus where applicable.
3. **Upstream validation:** file issues/PRs for confirmed drift (starting with `httpx.Mounts`) — maintainer acceptance is the strongest external evidence, exactly as DOCER demonstrated; outcomes tracked in the README.
4. **Publication:** `BENCHMARKS.md` with methodology + raw JSON, reproducible via `scripts/bench.py`.

Honesty clause (amended per project owner, 2026-07-12): if precision lands <95% after tuning, this is **not an automatic kill**. The sequence is: (1) attempt concrete workarounds — narrow default rule packs, tighten classification patterns, grow the FP corpus from every miss — and re-measure; (2) if still <95%, present the measured numbers, the FP breakdown, and remaining options to the project owner, who decides whether to ship, iterate, or stop.

---

## 12. Packaging, CI/CD, release

- **Build:** hatchling; `pyproject.toml` sole source of truth; `uv` for dev env (`uv sync`); runtime deps `griffe>=1.0`, `markdown-it-py>=3.0` only. Extras: `docrot[rst]` (docutils, post-v1 full rst), dev extras for tooling.
- **CI (GitHub Actions):** test matrix {3.10–3.13} × {ubuntu, windows, macos}; lint/type/coverage gates; self-dogfood step; benchmark smoke (2 repos, runtime budget assert); release workflow → PyPI trusted publishing on tag; SLSA provenance.
- **Distribution surfaces (shipped in-repo):**
  - `.pre-commit-hooks.yaml` (`docrot --changed-only`)
  - `action.yml` composite GitHub Action (installs via `uvx`, runs with SARIF upload, handles fetch-depth deepening)
- **Versioning:** semver; 0.x during M1–M5; v1.0.0 only after the §11 gate. `CHANGELOG.md` (keep-a-changelog).
- **Docs:** README (problem → 60-second demo → the httpx case study → rule catalog link); mkdocs-material site at v1.0 (dogfooded by docrot itself, naturally).

## 13. Repository layout

```
docrot/
├── pyproject.toml  ├── README.md  ├── CHANGELOG.md  ├── LICENSE
├── action.yml      ├── .pre-commit-hooks.yaml
├── src/docrot/
│   ├── __init__.py       # __version__, public API: check(), Finding
│   ├── cli.py            # argparse (no click dep — dogfood minimalism)
│   ├── config.py         # load/merge/validate
│   ├── discovery.py      # docs, package roots, command sources
│   ├── model.py          # dataclasses & enums (§6)
│   ├── extract/          # markdown.py, rst.py, classify.py, suppress.py
│   ├── resolve/          # python.py (griffe), paths.py, commands.py
│   ├── temporal/         # git.py (subprocess wrapper), gate.py, provenance.py
│   ├── index/            # symbol_index.py, cache.py
│   ├── rules/            # base.py, core.py, agents.py, anchors.py, registry.py
│   └── report/           # text.py, json_.py, sarif.py, github.py
├── tests/
│   ├── unit/ …           # mirrors src
│   ├── fp_corpus/        # §10 regression fixtures (the spike's legacy)
│   ├── golden/           # e2e mini-repos + expected.json
│   └── temporal/         # git-history builders
├── scripts/bench.py      # §11 harness
└── docs/                 # mkdocs site (v1.0)
```

## 14. Milestones & acceptance criteria

Each milestone = mergeable, green CI, dogfood passing.

| M | Deliverable | Acceptance criteria |
|---|---|---|
| **M0** | Scaffold | pyproject, CI matrix, mypy-strict/ruff/coverage gates green on empty skeleton; `docrot --version` works via `uvx` |
| **M1** | Extraction + paths | Markdown extractor with line/col; classifier with full FP-corpus fixtures for classification; PATH001/002 + LINK001 live against file tree; `docrot` runs usefully on its own repo |
| **M2** | Python resolution | griffe-backed SymbolIndex; tri-state verdicts; PY001 live; spike's requests/flask/httpx FP classes all green in corpus; `httpx.Mounts` shape detected on the httpx fixture |
| **M3** | Temporal gate | blame batching, targeted historical resolution, worktree fallback, subtree-hash caching; PY002/PATH001/CALL001 with provenance; degraded-mode behaviors tested; warm-run budget met |
| **M4** | Commands pack | AG001–AG003 across make/tox/nox/poe/npm/just/entry-points/`python -m`; agent-file discovery complete |
| **M5** | Surfaces | JSON schema v1 frozen, SARIF validated against GitHub upload, `github` format, suppressions with accounting, `explain`, pre-commit hook, Action |
| **M6** | Benchmark | §11 executed; **precision ≥95%, else workarounds attempted and owner decides** (§11 honesty clause); upstream issues filed; BENCHMARKS.md published |
| **M7** | v0.1.0 → v1.0.0 | PyPI trusted publishing; README case study; announce (r/Python, HN Show, python-discuss); anchors pack (AN001/2) ships in 0.x during M5–M7 as beta |

Sequencing rationale: M1 alone already exceeds the agents-lint family's path checking (language-agnostic + temporal-ready); M2–M3 is the moat; M4–M5 is adoption surface; M6 is the credibility gate.

## 15. Post-v1 roadmap

- **Full rst/docutils + MyST** parsing; Sphinx-role deep validation.
- **CLI flag introspection** (click/typer statically; argparse via safe AST evaluation) → validate `--flags` in documented commands.
- **`--fix`**: apply rename suggestions (path moves, symbol renames detected via removal-commit diff similarity).
- **MCP server** (`docrot serve-mcp`): `verify_reference`, `check_doc`, `list_drift` — lets agents validate context files before trusting them (closes the loop with this project's origin).
- **Multi-language resolution** via tree-sitter (JS/TS first — the agents-lint audience), same two-gate design.
- **Env var / config-key references** (`DATABASE_URL`, `[tool.x]` keys) against settings modules and pyproject.
- **Docstring cross-references** (`:func:` inside docstrings) — only if docvet doesn't get there first; prefer integration over competition.

## 16. Risks & mitigations (engineering view)

1. **Precision misses 95%.** Mitigation: UNKNOWN-by-default posture, temporal gate, FP corpus grown from every benchmark miss; scope ratchet (disable low-precision rules by default) before shipping. If still short after workarounds, escalate to project owner for the ship/iterate/stop decision (§11).
2. **Git subprocess portability/perf (Windows).** Mitigation: single wrapper module, batched calls, full Windows CI leg from M0; no libgit2 dependency to go wrong.
3. **griffe API churn / limits (e.g., exotic dynamic exports).** Mitigation: pin `>=1,<2`; all griffe access behind `resolve/python.py`; dynamic boundaries → UNKNOWN by design, so griffe gaps degrade to silence, not noise.
4. **Swimm patent adjacency.** Mitigation: mechanism is symbol resolution + git ancestry + content hashes (abundant 2022-and-earlier prior art: DOCER, tagref, doctest); no similarity-scoring auto-sync heuristics; note kept in docs.
5. **Niche competitor adds symbol checks.** Mitigation: speed to M3 (temporal provenance is the hard part), benchmark publication, and PY-resolution depth as moat; agents-lint interop (we emit JSON they could consume) beats rivalry.
6. **Adoption plateau (yet-another-linter fatigue).** Mitigation: the Action + SARIF surface (findings appear as PR annotations with commit evidence — visceral), the httpx-style case studies from M6, `--changed-only` pre-commit speed, and zero-config value.

## 17. Open design questions

Recorded with recommendations; none block M0–M2.

1. **Should LINK001 (relative markdown links) be on by default** given partial overlap with lychee/markdown-link-check? *Recommendation: yes* — trivially cheap, and our temporal provenance makes even this commodity check better; users with lychee can disable the rule.
2. **Default `fail_on` for agent files: error or warning?** Agents acting on stale facts argues for error; new-tool trust argues warning. *Recommendation: error for AG001/AG002 (facts), warning elsewhere; revisit with benchmark data.*
3. **`docs/` under Sphinx projects: skip roles we can't fully emulate?** *Recommendation: validate role targets with our own resolver but cap severity at warning in .rst files during 0.x (Sphinx-nitpick users have partial coverage; don't double-report as error).*
4. **Cache location:** repo-local `.docrot_cache/` (CI-restorable, needs gitignore) vs platform user-cache. *Recommendation: repo-local default with auto-gitignore-check warning; `cache_dir` overridable.*
5. **Bare filename references (`views.py`) outside agent files:** even with temporal gating these skew tutorial-heavy. *Recommendation: PATH rules require a `/` or a match against tracked basenames at introduction time; pure-fiction basenames stay UNKNOWN.* (Already reflected in §5.3.)

---

*Prepared 2026-07-12. Implementation begins at M0 upon approval of this plan.*
