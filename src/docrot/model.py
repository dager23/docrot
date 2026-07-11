"""Core data model: every pipeline stage consumes and produces these types.

The pipeline is a chain of pure transformations:

    RawSpan -> Reference -> Resolution -> (TemporalEvidence) -> Finding

All types are frozen dataclasses so stages stay independently testable and a
run is deterministic given (worktree, git history, config).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path


class RefKind(enum.Enum):
    SYMBOL_DOTTED = "symbol-dotted"
    SYMBOL_CALL = "symbol-call"
    PATH = "path"
    COMMAND = "command"
    LINK = "link"
    ANCHOR = "anchor"


class Verdict(enum.Enum):
    """Tri-state resolution outcome.

    UNKNOWN never becomes a finding: it marks references outside the
    namespace we fully understand (external packages, dynamic attributes,
    tutorial placeholders). Silence beats noise.
    """

    RESOLVED = "resolved"
    BROKEN = "broken"
    UNKNOWN = "unknown"


class Confidence(enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Severity(enum.Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    IGNORE = "ignore"

    def at_least(self, other: Severity) -> bool:
        order = [Severity.IGNORE, Severity.INFO, Severity.WARNING, Severity.ERROR]
        return order.index(self) >= order.index(other)


@dataclass(frozen=True)
class SpanContext:
    heading_path: tuple[str, ...] = ()
    in_agent_file: bool = False
    fence_lang: str | None = None


@dataclass(frozen=True)
class RawSpan:
    """A candidate piece of inline code / command text found in a doc."""

    doc: Path  # repo-relative, POSIX separators
    line: int  # 1-based
    col: int  # 1-based
    text: str
    context: SpanContext = field(default_factory=SpanContext)


@dataclass(frozen=True)
class Reference:
    """A classified span: a checkable claim about the codebase."""

    span: RawSpan
    kind: RefKind
    target: str  # normalized: parens stripped, role markup stripped

    @property
    def doc(self) -> Path:
        return self.span.doc

    @property
    def line(self) -> int:
        return self.span.line

    @property
    def col(self) -> int:
        return self.span.col


@dataclass(frozen=True)
class Resolution:
    ref: Reference
    verdict: Verdict
    resolved_as: str | None = None  # e.g. "requests.models.Response (via alias)"
    boundary: str | None = None  # why UNKNOWN: "external:werkzeug", "dynamic:__getattr__"


@dataclass(frozen=True)
class CommitRef:
    sha: str
    date: str  # ISO date, empty when unknown

    @property
    def short(self) -> str:
        return self.sha[:7]


@dataclass(frozen=True)
class TemporalEvidence:
    introduced_at: CommitRef | None
    resolved_at_introduction: bool
    broken_since: CommitRef | None = None


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: Severity
    confidence: Confidence
    resolution: Resolution
    evidence: TemporalEvidence | None = None
    message: str = ""
    suggestion: str | None = None
    suppressed: bool = False

    @property
    def ref(self) -> Reference:
        return self.resolution.ref


@dataclass(frozen=True)
class Summary:
    refs: int
    resolved: int
    unknown: int
    findings: int
    suppressed: int
    by_severity: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Report:
    findings: tuple[Finding, ...]
    summary: Summary
    unknowns: tuple[Resolution, ...] = ()  # inspectable via --show-unknown
    notes: tuple[str, ...] = ()  # degraded-mode notices etc.
