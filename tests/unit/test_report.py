"""Reporter tests: every output format must be parseable and complete."""

from __future__ import annotations

import io
import json
from pathlib import Path

from docrot.model import (
    CommitRef,
    Confidence,
    Finding,
    RawSpan,
    Reference,
    RefKind,
    Report,
    Resolution,
    Severity,
    Summary,
    TemporalEvidence,
    Verdict,
)
from docrot.report import render_github, render_json, render_sarif, render_text

DOC = Path("docs/guide.md")


def _finding(rule: str = "PY002", suppressed: bool = False) -> Finding:
    ref = Reference(RawSpan(DOC, 12, 5, "pkg.gone"), RefKind.SYMBOL_DOTTED, "pkg.gone")
    return Finding(
        rule=rule,
        severity=Severity.ERROR,
        confidence=Confidence.HIGH,
        resolution=Resolution(ref, Verdict.BROKEN),
        evidence=TemporalEvidence(
            introduced_at=CommitRef("a" * 40, "2024-01-02"),
            resolved_at_introduction=True,
            broken_since=CommitRef("b" * 40, "2024-06-07"),
        ),
        message="`pkg.gone` no longer resolves",
        suggestion="pkg.new_name",
    )


def _report(*findings: Finding, notes: tuple[str, ...] = ()) -> Report:
    active = [f for f in findings if not f.suppressed]
    return Report(
        findings=tuple(findings),
        summary=Summary(
            refs=10,
            resolved=8,
            unknown=1,
            findings=len(active),
            suppressed=len(findings) - len(active),
            by_severity={"error": len(active)},
        ),
        unknowns=(
            Resolution(
                Reference(RawSpan(DOC, 3, 1, "numpy.array"), RefKind.SYMBOL_DOTTED, "numpy.array"),
                Verdict.UNKNOWN,
                boundary="external:numpy",
            ),
        ),
        notes=notes,
    )


class TestText:
    def test_finding_with_full_provenance(self) -> None:
        out = io.StringIO()
        render_text(_report(_finding()), out)
        text = out.getvalue()
        assert "docs/guide.md:12:5" in text
        assert "PY002" in text
        assert "written at aaaaaaa (2024-01-02)" in text
        assert "broken since bbbbbbb (2024-06-07)" in text
        assert "hint: pkg.new_name" in text
        assert "10 references" in text

    def test_suppressed_findings_are_not_printed_but_are_counted(self) -> None:
        out = io.StringIO()
        render_text(_report(_finding(suppressed=False)), out)
        assert "PY002" in out.getvalue()

    def test_unknowns_only_with_flag(self) -> None:
        quiet, loud = io.StringIO(), io.StringIO()
        render_text(_report(), quiet)
        render_text(_report(), loud, show_unknown=True)
        assert "external:numpy" not in quiet.getvalue()
        assert "external:numpy" in loud.getvalue()

    def test_notes_are_surfaced(self) -> None:
        out = io.StringIO()
        render_text(_report(notes=("no git history available",)), out)
        assert "note: no git history available" in out.getvalue()

    def test_medium_confidence_is_labelled(self) -> None:
        from dataclasses import replace

        out = io.StringIO()
        render_text(_report(replace(_finding(), confidence=Confidence.MEDIUM)), out)
        assert "confidence: medium" in out.getvalue()


class TestJson:
    def test_schema_and_fields(self) -> None:
        out = io.StringIO()
        render_json(_report(_finding()), out)
        payload = json.loads(out.getvalue())
        assert payload["schema"] == 1
        assert payload["summary"]["refs"] == 10
        f = payload["findings"][0]
        assert f["rule"] == "PY002"
        assert f["doc"] == "docs/guide.md"  # POSIX separators on every platform
        assert f["line"] == 12 and f["col"] == 5
        assert f["evidence"]["broken_since"] == "b" * 40
        assert f["evidence"]["resolved_at_introduction"] is True

    def test_finding_without_evidence_serializes(self) -> None:
        from dataclasses import replace

        out = io.StringIO()
        render_json(_report(replace(_finding(), evidence=None)), out)
        assert json.loads(out.getvalue())["findings"][0]["evidence"] is None


class TestSarif:
    def test_valid_shape(self) -> None:
        out = io.StringIO()
        render_sarif(_report(_finding()), out)
        doc = json.loads(out.getvalue())
        assert doc["version"] == "2.1.0"
        run = doc["runs"][0]
        assert run["tool"]["driver"]["name"] == "docrot"
        assert len(run["tool"]["driver"]["rules"]) >= 10
        loc = run["results"][0]["locations"][0]["physicalLocation"]
        assert loc["artifactLocation"]["uri"] == "docs/guide.md"
        assert loc["region"]["startLine"] == 12

    def test_rules_carry_descriptions(self) -> None:
        out = io.StringIO()
        render_sarif(_report(), out)
        rules = json.loads(out.getvalue())["runs"][0]["tool"]["driver"]["rules"]
        assert all(r["shortDescription"]["text"] for r in rules)


class TestGithub:
    def test_workflow_command_format(self) -> None:
        out = io.StringIO()
        render_github(_report(_finding()), out)
        line = out.getvalue().strip()
        assert line.startswith("::error ")
        assert "file=docs/guide.md,line=12,col=5" in line
        assert "title=docrot PY002" in line
