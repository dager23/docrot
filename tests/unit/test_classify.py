"""Classifier tests — the precision contract from the spike."""

from __future__ import annotations

from pathlib import Path

from docrot.extract.classify import classify, path_fallback_eligible
from docrot.model import RawSpan, RefKind

DOC = Path("README.md")


def span(text: str) -> RawSpan:
    return RawSpan(DOC, 1, 1, text)


def kind_of(text: str, entry_points: frozenset[str] = frozenset()) -> RefKind | None:
    ref = classify(span(text), entry_points)
    return ref.kind if ref else None


class TestSymbolClassification:
    def test_dotted(self) -> None:
        assert kind_of("flask.Flask.route") is RefKind.SYMBOL_DOTTED

    def test_dotted_with_call_parens(self) -> None:
        ref = classify(span("requests.get()"), frozenset())
        assert ref is not None
        assert ref.kind is RefKind.SYMBOL_DOTTED
        assert ref.target == "requests.get"

    def test_callform(self) -> None:
        ref = classify(span("create_app()"), frozenset())
        assert ref is not None
        assert ref.kind is RefKind.SYMBOL_CALL
        assert ref.target == "create_app"

    def test_bare_words_never_flagged(self) -> None:
        # the `timeout`/`id` noise class from the spike
        assert kind_of("timeout") is None
        assert kind_of("id") is None
        assert kind_of("TRUE") is None

    def test_tilde_prefix_stripped(self) -> None:
        ref = classify(span("~pkg.mod.Klass"), frozenset())
        assert ref is not None
        assert ref.target == "pkg.mod.Klass"


class TestPathClassification:
    def test_slash_path(self) -> None:
        assert kind_of("src/app.py") is RefKind.PATH

    def test_py_extension_beats_symbol_grammar(self) -> None:
        # `auth.py` matches dotted grammar but is a file (spike lesson)
        assert kind_of("auth.py") is RefKind.PATH

    def test_json_ambiguity_prefers_symbol(self) -> None:
        # `Response.json` is a method, not a file (spike's funniest FP)
        assert kind_of("Response.json") is RefKind.SYMBOL_DOTTED

    def test_json_symbol_gets_path_fallback(self) -> None:
        ref = classify(span("package.json"), frozenset())
        assert ref is not None
        assert ref.kind is RefKind.SYMBOL_DOTTED
        assert path_fallback_eligible(ref)

    def test_windows_separators_normalized(self) -> None:
        ref = classify(span("src\\app.py"), frozenset())
        assert ref is not None
        assert ref.kind is RefKind.PATH
        assert ref.target == "src/app.py"


class TestCommandClassification:
    def test_known_runner(self) -> None:
        assert kind_of("make test") is RefKind.COMMAND
        assert kind_of("tox -e py312") is RefKind.COMMAND

    def test_entry_point_runner(self) -> None:
        assert kind_of("mytool serve --port 8000", frozenset({"mytool"})) is RefKind.COMMAND

    def test_unknown_multiword_dropped(self) -> None:
        assert kind_of("hello world") is None

    def test_shell_metachars_dropped(self) -> None:
        assert kind_of("FLASK_APP=app.py") is None
        assert kind_of("a|b") is None


class TestEllipsisPrefix:
    def test_ellipsis_span_is_not_a_claim(self) -> None:
        # "start typing `typer.File`..." (typer docs) — intentional prefix
        from docrot.model import SpanContext

        span = RawSpan(DOC, 1, 1, "typer.File", SpanContext(ellipsis_after=True))
        assert classify(span, frozenset()) is None


class TestForeignLanguageNoise:
    def test_js_callforms_are_callform_class(self) -> None:
        # These classify as SYMBOL_CALL but resolution keeps them UNKNOWN
        # (never BROKEN without temporal confirmation) — see resolver tests.
        assert kind_of("fetch()") is RefKind.SYMBOL_CALL
        assert kind_of("XMLHttpRequest()") is RefKind.SYMBOL_CALL
