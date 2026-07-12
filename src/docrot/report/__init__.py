"""Report rendering: text, json, sarif, github workflow commands."""

from __future__ import annotations

import json
import sys
from typing import TextIO

from docrot.model import Confidence, Finding, Report, Severity

_SEV_COLOR = {Severity.ERROR: "31", Severity.WARNING: "33", Severity.INFO: "36"}


def _color(code: str, text: str, enabled: bool) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if enabled else text


def render_text(report: Report, out: TextIO = sys.stdout, show_unknown: bool = False) -> None:
    color = out.isatty()
    current_doc = None
    for f in report.findings:
        if f.suppressed:
            continue
        doc = f.ref.doc.as_posix()
        if doc != current_doc:
            if current_doc is not None:
                out.write("\n")
            current_doc = doc
        sev = _color(_SEV_COLOR.get(f.severity, "0"), f.severity.value, color)
        out.write(f"{doc}:{f.ref.line}:{f.ref.col}  {f.rule} {sev}  {f.message}\n")
        ev = f.evidence
        if ev and ev.introduced_at:
            line = f"    written at {ev.introduced_at.short}"
            if ev.introduced_at.date:
                line += f" ({ev.introduced_at.date})"
            if ev.resolved_at_introduction:
                line += " when it still resolved"
            if ev.broken_since:
                line += f" | broken since {ev.broken_since.short}"
                if ev.broken_since.date:
                    line += f" ({ev.broken_since.date})"
            out.write(line + "\n")
        if f.confidence is not Confidence.HIGH:
            out.write(f"    confidence: {f.confidence.value}\n")
        if f.suggestion:
            out.write(f"    hint: {f.suggestion}\n")

    if show_unknown and report.unknowns:
        out.write("\nskipped (unknown/unverifiable):\n")
        for r in report.unknowns:
            out.write(
                f"  {r.ref.doc.as_posix()}:{r.ref.line}  `{r.ref.target}`"
                f"  [{r.boundary or 'unknown'}]\n"
            )

    for note in report.notes:
        out.write(f"note: {note}\n")

    s = report.summary
    out.write(
        f"\ndocrot: {s.refs} references | {s.resolved} resolved | "
        f"{s.unknown} unknown (skipped) | {s.findings} findings"
    )
    if s.by_severity:
        parts = ", ".join(f"{n} {sev}" for sev, n in sorted(s.by_severity.items()))
        out.write(f" ({parts})")
    if s.suppressed:
        out.write(f" | {s.suppressed} suppressed")
    out.write("\n")


def _finding_dict(f: Finding) -> dict[str, object]:
    ev: dict[str, object] | None = None
    if f.evidence:
        ev = {
            "introduced_at": f.evidence.introduced_at.sha if f.evidence.introduced_at else None,
            "introduced_date": (
                f.evidence.introduced_at.date if f.evidence.introduced_at else None
            ),
            "resolved_at_introduction": f.evidence.resolved_at_introduction,
            "broken_since": f.evidence.broken_since.sha if f.evidence.broken_since else None,
            "broken_date": f.evidence.broken_since.date if f.evidence.broken_since else None,
        }
    return {
        "rule": f.rule,
        "severity": f.severity.value,
        "confidence": f.confidence.value,
        "doc": f.ref.doc.as_posix(),
        "line": f.ref.line,
        "col": f.ref.col,
        "target": f.ref.target,
        "kind": f.ref.kind.value,
        "message": f.message,
        "evidence": ev,
        "suggestion": f.suggestion,
        "suppressed": f.suppressed,
    }


def render_json(report: Report, out: TextIO = sys.stdout) -> None:
    payload = {
        "schema": 1,
        "summary": {
            "refs": report.summary.refs,
            "resolved": report.summary.resolved,
            "unknown": report.summary.unknown,
            "findings": report.summary.findings,
            "suppressed": report.summary.suppressed,
            "by_severity": report.summary.by_severity,
        },
        "findings": [_finding_dict(f) for f in report.findings],
        "notes": list(report.notes),
    }
    json.dump(payload, out, indent=2)
    out.write("\n")


_SARIF_LEVEL = {Severity.ERROR: "error", Severity.WARNING: "warning", Severity.INFO: "note"}


def render_sarif(report: Report, out: TextIO = sys.stdout) -> None:
    from docrot.rules.registry import RULES

    results = []
    for f in report.findings:
        if f.suppressed:
            continue
        results.append(
            {
                "ruleId": f.rule,
                "level": _SARIF_LEVEL.get(f.severity, "warning"),
                "message": {"text": f.message},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": f.ref.doc.as_posix()},
                            "region": {
                                "startLine": f.ref.line,
                                "startColumn": f.ref.col,
                            },
                        }
                    }
                ],
            }
        )
    sarif = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "docrot",
                        "informationUri": "https://github.com/yshah-afk/docrot",
                        "rules": [
                            {
                                "id": r.id,
                                "shortDescription": {"text": r.summary},
                            }
                            for r in RULES.values()
                        ],
                    }
                },
                "results": results,
            }
        ],
    }
    json.dump(sarif, out, indent=2)
    out.write("\n")


def render_github(report: Report, out: TextIO = sys.stdout) -> None:
    kind = {Severity.ERROR: "error", Severity.WARNING: "warning", Severity.INFO: "notice"}
    for f in report.findings:
        if f.suppressed:
            continue
        out.write(
            f"::{kind.get(f.severity, 'warning')} "
            f"file={f.ref.doc.as_posix()},line={f.ref.line},col={f.ref.col},"
            f"title=docrot {f.rule}::{f.message}\n"
        )


RENDERERS = {
    "text": render_text,
    "json": render_json,
    "sarif": render_sarif,
    "github": render_github,
}
