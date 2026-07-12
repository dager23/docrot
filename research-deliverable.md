# Research Deliverable: `docrot` — a deterministic doc↔code drift checker

**Date:** 2026-07-12
**Status:** Research complete. Recommendation: **BUILD** (v1 scope defined below).
**Lineage:** Direct descendant of the rejected Serena semantic-summary proposal. The maintainers' rejection argument — *"[externally stored summaries] will become stale essentially immediately, and add negative value. The only things that have a chance of staying up to date are things inside the code"* (MischaPanch, Serena issue thread) — is not an objection to this idea. It **is** this idea: nobody validates the external information that already exists.

---

## 1. Problem statement

Every repository carries prose that makes checkable claims about code: READMEs (“call `create_app()` in `app.py`”), contributor guides (“bump the version in `__version__.py`”), MkDocs/markdown documentation (“use `httpx.Mounts` to mount transports”), and — newly and most consequentially — agent context files (CLAUDE.md, AGENTS.md, .cursorrules) that AI coding agents consume as ground truth on every request.

Nothing in CI validates any of it. When code changes, these references rot silently. Historically that cost human confusion; in the agent era a stale path or dead symbol in AGENTS.md **actively misdirects an agent on every session**, multiplying the cost of one stale line across thousands of model invocations.

The problem, precisely: **there is no deterministic, language-aware, CI-enforceable check that references to code entities inside prose documentation still resolve against the codebase.**

## 2. Evidence people experience it

**Academic (quantified):**
- *Detecting outdated code element references in software repository documentation* (EMSE 2023, [10.1007/s10664-023-10397-6](https://link.springer.com/article/10.1007/s10664-023-10397-6), [arXiv:2212.01479](https://arxiv.org/abs/2212.01479)): of the top-1000 most-popular GitHub projects, **28.9% currently contain at least one outdated code reference in their documentation; 82.3% were outdated at least once in their history** (3,000+ projects analyzed). Authors filed issues on real projects (e.g. [google/clif#52](https://github.com/google/clif/issues/52)), some leading to fixes.
- *Agent READMEs: An Empirical Study of Context Files for Agentic Coding* ([arXiv:2511.12884](https://arxiv.org/abs/2511.12884), Nov 2025): 2,303 context files across 1,925 repos; **62.3% contain build/run commands, 75.0% testing procedures, 67.7% architecture descriptions** — all checkable claims. Authors explicitly recommend developing **“linters for agent context files to detect divergences”** and treating context files “with the same rigor applied to Dockerfile or CI/CD workflows.”

**Practitioner:**
- The entire agent-context-file discourse of 2025–26 revolves around staleness: “Your AGENTS.md is probably lying” (giacomo/agents-lint tagline), “Your CLAUDE.md is making your agent dumber” (Medium), AGENTS.md guides warning that path references “change constantly” and stale info “actively poisons the context.”
- First-party evidence from this project's own history: Serena's maintainers independently articulated staleness of code-adjacent prose as the central failure mode of documentation-like artifacts.
- Commercial validation: Swimm (patented “Auto-sync”), Dosu, DeepDocs, Mintlify all sell doc-freshness — a market exists; none of it is open, deterministic, or Python-native.

**Our own spike (this session):** a 150-line prototype found **live drift in httpx master (~14k stars)**: `docs/advanced/transports.md` documents `httpx.Mounts` with a complete usage example; no `Mounts` class exists anywhere in the package.

## 3. Existing solutions (all of them)

| Tool | What it checks | Mechanism | Adoption/status | Misses |
|---|---|---|---|---|
| **DOCER / DOCER_tool** (academic) | Code-element refs in README/wiki | Regex extraction + git snapshot comparison | Abandoned research artifact (3 commits, 3 stars) | Regex-only resolution → FPs on renames/changelogs; no symbol semantics; unmaintained |
| **Swimm** (commercial) | Code snippets/tokens coupled to docs | Patented multi-signal “Auto-sync” | Funded product | Closed, SaaS+IDE platform, not CI-composable, not Python-ecosystem-native |
| **Dosu / DeepDocs / Mintlify agents** (commercial) | Doc freshness, auto-PR updates | LLM + git heuristics | Funded products | Non-deterministic, paid API, cloud; unusable as a hard CI gate |
| **Docvet** (PyPI) | Docstring quality/freshness | git blame/diff heuristics + griffe | v1.15.1, 9 stars, active | **Docstrings only** — no README/markdown/agent files; time-based not reference-based |
| **sybil, xdoctest, pytest-examples, pytest-markdown-docs, pytest-doctestplus, mdcheckr** | Executable code *blocks* in docs | Run/parse the examples | Mature, adopted | Only fenced examples; prose references (backticks, paths, API mentions) invisible |
| **agents-lint, AgentLint, agentlinter, cclint, mauhpr/agentlint** (6+ tools, 2025–26) | AGENTS.md/CLAUDE.md structure, file paths, npm scripts | Path existence, package.json lookup, style rules | All <100 stars, JS/TS-centric | Explicitly **no code-symbol validation**; agent files only, not general docs; no Python symbol awareness |
| **pallaprolus/drift** (VS Code) | JSDoc/docstring vs signature | AST pairing | 1 star, editor-only | No CI, no prose docs |
| **doc-drift-guard** (PyPI) | Imports inside Python code blocks in markdown | AST import validation | 0 stars, 8 commits | Code blocks only; no prose refs, no paths, no temporal logic |
| **Sphinx nitpicky / MkDocs strict (+autorefs)** | Explicit cross-reference *syntax* (`:py:func:`, `[foo][pkg.mod]`) | Doc-build resolution | Universal | Only opt-in role syntax inside the doc build; plain backticks, GFM READMEs, CONTRIBUTING, agent files never pass through a build |
| **tagref / xreferee** | `[tag:x]`/`[ref:x]` comment cross-refs | String matching | Niche, used by Airbnb/Notion | Tags only; no relationship to actual code entities |
| **lychee / markdown-link-check** | URLs and markdown *links* | HTTP/file existence | Widely adopted | The precedent proving the model works — but only for links, never code references |

## 4. Why existing solutions are insufficient

The space is covered in every dimension **except the load-bearing one**:

- Executable examples → solved (sybil family). Docstring↔signature → solved (docvet/pydoclint/ruff-D). Links → solved (lychee). Explicit doc-build cross-refs → solved (Sphinx/MkDocs).
- **Prose references to code entities — the thing DOCER measured at 28.9% breakage — have no OSS checker at all.** The agent-lint wave validates paths and npm scripts but explicitly disclaims symbol validation, is JS-centric, and only reads agent files. The commercial products prove value but are closed and non-deterministic.
- Rust solved this *in the compiler* (intra-doc links are build-checked). Python's ecosystem moved from validated Sphinx roles to unvalidated GitHub-flavored markdown (READMEs, MkDocs prose, agent files) and lost validation on the way. That's the abstraction hole.

## 5. Why doesn't this already exist? (kill attempt, honestly reported)

Attempted kills and outcomes:

1. **“It's already built.”** Closest candidates examined above; each is scoped away from the gap (docstrings-only, examples-only, paths-only, links-only, closed-source). Not killed.
2. **“Academics tried and it failed.”** DOCER worked (issues filed, fixes made, EMSE acceptance) but was never engineered past a shell-script GitHub Action; typical research-prototype abandonment, not a viability verdict. Not killed — but it flags the real risk (below).
3. **“False positives make it useless.”** This is the serious objection, and our spike measured it directly on requests, flask, and httpx. Every FP fell into five mechanically fixable classes: star-import re-exports, alias chains (`requests.Response` → `requests.models.Response`), inherited attributes across MRO (even cross-package: `flask.Flask.route` lives on `sansio.Scaffold`), `self.x` instance attributes, and tutorial placeholder files (`views.py`, `hello.py`). Two design consequences: (a) resolution must be griffe-grade static analysis, not grep/regex — this is exactly why DOCER-class tools stall; (b) a **git-temporal gate** (flag only references that resolved at a past revision and don't now) structurally eliminates placeholder/external-name/pseudo-code noise, because fiction never resolved in the first place. Survived, with the architecture now dictated by evidence.
4. **“Maintainers reject this category.”** Opposite: Serena maintainers *articulated the problem*, the Agent-READMEs authors *requested the tool category*, and DOCER's filed issues led to real fixes. Not killed.
5. **“LLM agents make it obsolete — they can just check the docs.”** Per-session re-derivation is exactly the token waste this project's earlier research quantified; and LLM checking is non-deterministic, unsuitable as a CI gate. Deterministic index + optional MCP surface makes agents *consumers* of the tool, not replacements. Not killed.
6. **“Swimm's patent blocks it.”** Risk, not blocker: the patent covers their multi-signal auto-sync heuristic; a resolver + git-temporal + hash-anchor design is a materially different mechanism, in an area with abundant prior art (DOCER 2022 predates enforcement plausibility). Flagged in risks.

## 6. Alternates considered (anti-attachment check)

- **Hybrid structural/comprehension query router** (from the 83%-vs-92% gap): killed — platform absorption (Claude Code native LSP; Anthropic's own finding that agentic search beats retrieval), Serena occupies the MCP niche, and the fix is open research, not a package.
- **Semantic summary index** (original idea): killed by Serena maintainers; reasons documented and internalized.
- **AGENTS.md-only linter:** killed as positioning — six competitors in twelve months, all shallow; the defensible core (symbol-grade resolution) generalizes to all prose docs, so scoping to agent files is an artificial ceiling. Agent files become the *flagship rule-pack*, not the product.
- **LLM doc-updater:** killed — saturated commercially, non-deterministic, "another AI wrapper."

## 7. Technical constraints

- Static resolution only (no code execution) → must handle star-imports via `__all__`/public-name expansion, alias/re-export chains, MRO walking, instance attrs from `__init__`; dynamic magic (`__getattr__`, proxies like `flask.request`) needs a declared-exceptions mechanism and honest "unresolvable, skipped" semantics. **griffe** (mkdocstrings' engine) already implements most of this and is the natural resolution backend for Python; the reference extractor and temporal/anchoring engine are the novel parts.
- Git-temporal gate needs efficient historical indexing: index symbols at tagged releases / doc-last-touched commits, cache by tree hash. Shallow clones in CI need graceful degradation (fall back to current-only + high-precision pattern classes).
- Multi-language docs mention JS/SQL/shell — language-scoped extraction with conservative defaults (dotted/call-form/path patterns; bare words never flagged by default).
- Windows/POSIX path normalization; monorepos; docs referencing installed-package APIs vs repo-local source.

## 8. Risks

1. **Precision is the product.** If the default config flags one placeholder file, users uninstall. Mitigation: two-gate design, conservative default patterns, per-finding "why flagged" provenance (commit where the reference last resolved), inline suppression comments. Measured target: <5% FP on a 20-repo benchmark before v1.0.
2. **Adoption friction** (yet another CI tool). Mitigation: zero-config first run with valuable output (the httpx result took nothing but `docrot .`), pre-commit hook, GitHub Action, `--fix`-style rename suggestions.
3. **Swimm patent** — different mechanism, prior art; monitor, don't copy auto-sync heuristics.
4. **Agent-lint niche consolidation** — someone adds shallow symbol checks to an agents-linter. Our moat is resolution depth + generality; ship the Python resolver first, tree-sitter languages later.
5. **Scope creep into semantics.** Prose *meaning* drift is not checkable deterministically; the tool must stay honest: it checks references and anchored claims, not truth. (Optional hash-anchors — bind a paragraph to a symbol's body hash — give teams a Swimm-like opt-in without an LLM.)

## 9. Why this package deserves to exist

It closes a measured gap (28.9% of top repos affected) that every adjacent tool consciously scoped away from, at exactly the moment the cost of the gap exploded (agents consuming context files as ground truth), using a mechanism (two-gate temporal + AST-grade resolution) that prior attempts lacked and that our spike validated end-to-end — including finding real drift in a major project on the first run. It is the constructive inversion of the strongest criticism our previous idea received from real maintainers.

## 10. Proposed v1 scope

- **Name:** `docrot` (PyPI confirmed available; alternates `deadrefs`, `anchorlint`).
- **Core:** scan `*.md`/`*.rst`/agent files → extract inline-code references (dotted symbols, call-forms, file paths, CLI invocations) → resolve against (a) griffe-built symbol index of the project, (b) repo file tree, (c) console-script entry points (argparse/click/typer introspection) → apply git-temporal gate → report with provenance; non-zero exit for CI.
- **Rule packs:** `core` (symbols/paths), `agents` (CLAUDE.md/AGENTS.md specifics incl. command validation), `anchors` (opt-in hash-anchored prose claims).
- **Surfaces:** CLI, pre-commit hook, GitHub Action, optional MCP server (agents query "is this doc claim still valid?").
- **Proof-of-value plan:** run against top-100 PyPI packages' repos; publish measured precision + confirmed-drift count; file upstream issues (DOCER showed maintainers fix these); benchmark runtime (target: <5s warm on flask-sized repos).

## Final decision — the six questions

1. **What gap does it close?** Deterministic validation of prose→code references — measured at 28.9% breakage in top repos, unserved by any OSS tool.
2. **What can it do that competitors genuinely cannot?** Symbol-grade resolution (aliases, star-imports, MRO, instance attrs) + git-temporal false-positive gating + hash-anchored claims — DOCER had none of the resolution, agent-linters have none of either, commercial tools aren't deterministic or open.
3. **Why would someone switch?** Mostly they wouldn't be switching — there is nothing to switch *from* in OSS. Docvet/sybil/lychee users add it alongside; it's complementary by construction.
4. **Why hasn't it been solved?** Rust solved it in the compiler; Python's docs migrated to unvalidated markdown; the academic solution was validated then abandoned pre-engineering; commercial players captured it behind SaaS; and the naive approach (regex) hits an FP wall that discourages casual attempts. The agent-context explosion that makes it urgent is <18 months old.
5. **Would competing maintainers find it technically interesting?** The Serena maintainers argued *for* this property unprompted; the Agent-READMEs authors requested the category; griffe/mkdocstrings maintainers would recognize the resolver reuse as the correct architecture. The two-gate design is the "actually clever" part.
6. **Could it gain PyPI traction?** Precedent: lychee (links) and interrogate/docvet (docstrings) show appetite for narrow CI doc-checkers; the agent-files angle provides a timely wedge with six shallow competitors validating demand and none holding the deep ground.

**Recommendation: proceed to implementation** per the v1 scope, with the precision benchmark as the go/no-go gate for v1.0 release.
