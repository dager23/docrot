"""Rules: turn (Resolution, TemporalEvidence) into Findings.

The catalog is PLAN.md §7. Each judgment consumes a `history` state:

* ``confirmed``   — the reference resolved when the doc line was written
                    (temporal gate evidence): drift, HIGH confidence;
* ``refuted``     — history says it *never* resolved: fiction, silent;
* ``unavailable`` — no usable history (no git, shallow, uncommitted line):
                    only high-precision reference classes fire, at MEDIUM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from docrot.config import Config
from docrot.model import (
    Confidence,
    Finding,
    RefKind,
    Resolution,
    Severity,
    TemporalEvidence,
    Verdict,
)

History = Literal["confirmed", "refuted", "unavailable"]


@dataclass(frozen=True)
class RuleSpec:
    id: str
    pack: str
    severity: Severity
    summary: str


RULES: dict[str, RuleSpec] = {
    r.id: r
    for r in (
        RuleSpec(
            "PY001",
            "core",
            Severity.ERROR,
            "Own-package symbol does not resolve (unconfirmed by history)",
        ),
        RuleSpec(
            "PY002",
            "core",
            Severity.ERROR,
            "Symbol resolved when the doc line was written and no longer does",
        ),
        RuleSpec("PATH001", "core", Severity.ERROR, "Documented path is missing from the tree"),
        RuleSpec(
            "PATH002",
            "core",
            Severity.WARNING,
            "Path resolves only by basename at a different location",
        ),
        RuleSpec(
            "LINK001", "core", Severity.ERROR, "Relative markdown link target missing from the tree"
        ),
        RuleSpec(
            "CALL001",
            "core",
            Severity.WARNING,
            "Bare call-form resolved when written and no longer resolves",
        ),
        RuleSpec("AG001", "agents", Severity.ERROR, "Documented command target does not exist"),
        RuleSpec(
            "AG002", "agents", Severity.ERROR, "Broken path reference inside an agent context file"
        ),
        RuleSpec(
            "AN001",
            "anchors",
            Severity.WARNING,
            "Anchored code changed under this prose (hash mismatch)",
        ),
        RuleSpec("AN002", "anchors", Severity.ERROR, "Anchor target symbol no longer resolves"),
    )
}


def rule_enabled(rule: RuleSpec, config: Config) -> bool:
    if rule.id in config.disabled_rules:
        return False
    # anchors activate by presence of anchor comments, not by pack config
    return rule.pack in config.enabled_packs or rule.pack == "anchors"


def _make(
    rule_id: str,
    config: Config,
    resolution: Resolution,
    confidence: Confidence,
    evidence: TemporalEvidence | None = None,
    message: str = "",
    suggestion: str | None = None,
) -> Finding | None:
    rule = RULES[rule_id]
    if not rule_enabled(rule, config):
        return None
    severity = config.severity_overrides.get(rule.id, rule.severity)
    if severity is Severity.IGNORE:
        return None
    return Finding(
        rule=rule_id,
        severity=severity,
        confidence=confidence,
        resolution=resolution,
        evidence=evidence,
        message=message or rule.summary,
        suggestion=suggestion,
    )


def judge(
    resolution: Resolution,
    evidence: TemporalEvidence | None,
    config: Config,
    local_symbol: bool,
    history: History,
) -> Finding | None:
    """Map one resolved reference (+ history state) to at most one finding."""
    ref = resolution.ref
    kind = ref.kind
    in_agent = ref.span.context.in_agent_file

    if resolution.verdict is Verdict.RESOLVED:
        if kind in (RefKind.PATH, RefKind.LINK) and resolution.boundary == "basename":
            return _make(
                "PATH002",
                config,
                resolution,
                Confidence.HIGH,
                message=f"`{ref.target}` found at `{resolution.resolved_as}`, "
                "not at the documented location",
                suggestion=resolution.resolved_as,
            )
        return None

    if resolution.verdict is Verdict.UNKNOWN:
        return None

    # -- BROKEN ------------------------------------------------------------

    if kind is RefKind.SYMBOL_DOTTED:
        if history == "confirmed":
            return _make(
                "PY002",
                config,
                resolution,
                Confidence.HIGH,
                evidence,
                message=f"`{ref.target}` no longer resolves",
            )
        if history == "unavailable" and local_symbol:
            return _make(
                "PY001",
                config,
                resolution,
                Confidence.MEDIUM,
                message=f"`{ref.target}` does not resolve",
            )
        return None

    if kind is RefKind.SYMBOL_CALL:
        if history == "confirmed":
            return _make(
                "CALL001",
                config,
                resolution,
                Confidence.HIGH,
                evidence,
                message=f"`{ref.target}()` no longer resolves anywhere in the project",
            )
        return None

    if kind in (RefKind.PATH, RefKind.LINK):
        if resolution.boundary == "bare" and history != "confirmed":
            return None  # bare filename that never existed: reader-creates-it
        if history == "confirmed":
            rule = "AG002" if in_agent else ("LINK001" if kind is RefKind.LINK else "PATH001")
            return _make(
                rule,
                config,
                resolution,
                Confidence.HIGH,
                evidence,
                message=f"`{ref.target}` existed when documented and is now gone",
            )
        if history == "unavailable":
            rule = "AG002" if in_agent else ("LINK001" if kind is RefKind.LINK else "PATH001")
            return _make(
                rule,
                config,
                resolution,
                Confidence.MEDIUM,
                message=f"`{ref.target}` not found in the tree",
            )
        return None

    if kind is RefKind.COMMAND:
        if history == "refuted":
            return None
        confidence = Confidence.HIGH if history == "confirmed" else Confidence.MEDIUM
        unit = resolution.resolved_as or ref.target
        runner = (resolution.boundary or "task").split(":")[0]
        return _make(
            "AG001",
            config,
            resolution,
            confidence,
            evidence if history == "confirmed" else None,
            message=f"`{ref.target}` — `{unit}` is not a known {runner} target",
        )

    return None


def judge_anchor(resolution: Resolution, config: Config) -> Finding | None:
    if resolution.verdict is Verdict.RESOLVED or resolution.boundary == "anchor:no-hash":
        return None
    if resolution.boundary == "anchor:target-missing":
        return _make(
            "AN002",
            config,
            resolution,
            Confidence.HIGH,
            message=f"anchor target `{resolution.ref.target}` no longer resolves",
        )
    return _make(
        "AN001",
        config,
        resolution,
        Confidence.HIGH,
        message=f"code under anchored prose changed: `{resolution.ref.target}` "
        "(run `docrot anchors update` after reviewing the prose)",
    )
