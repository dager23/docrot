"""The FP regression corpus for symbol resolution.

Every false-positive class the validation spike hit on requests/flask/httpx
is pinned here as a RESOLVED or UNKNOWN assertion — never BROKEN.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docrot.discovery import PackageRoot
from docrot.model import RawSpan, Reference, RefKind, Verdict
from docrot.resolve.python import PythonResolver

DOC = Path("README.md")


def ref(target: str, kind: RefKind = RefKind.SYMBOL_DOTTED) -> Reference:
    return Reference(RawSpan(DOC, 1, 1, target), kind, target)


@pytest.fixture
def resolver(synthetic_pkg: Path) -> PythonResolver:
    pkg = PackageRoot("mypkg", synthetic_pkg / "src" / "mypkg")
    return PythonResolver([pkg])


class TestFPCorpus:
    """Spike FP classes: each must NOT be BROKEN."""

    def test_star_import_reexport(self, resolver: PythonResolver) -> None:
        # httpx.Client via `from ._client import *`
        assert resolver.resolve(ref("mypkg.Client")).verdict is Verdict.RESOLVED

    def test_star_import_function(self, resolver: PythonResolver) -> None:
        assert resolver.resolve(ref("mypkg.fetch_all")).verdict is Verdict.RESOLVED

    def test_alias_chain_method(self, resolver: PythonResolver) -> None:
        # requests.Response.iter_lines via requests.models
        assert resolver.resolve(ref("mypkg.Response.iter_lines")).verdict is Verdict.RESOLVED

    def test_inherited_member(self, resolver: PythonResolver) -> None:
        # flask.Flask.route lives on a base class
        assert resolver.resolve(ref("mypkg.Response.close")).verdict is Verdict.RESOLVED

    def test_instance_attribute(self, resolver: PythonResolver) -> None:
        # Response.raw assigned in __init__
        assert resolver.resolve(ref("mypkg.Response.raw")).verdict is Verdict.RESOLVED

    def test_class_scoped_reference(self, resolver: PythonResolver) -> None:
        # docs write `Response.raw` without the package prefix
        assert resolver.resolve(ref("Response.raw")).verdict is Verdict.RESOLVED

    def test_module_basename_reference(self, resolver: PythonResolver) -> None:
        # docs write `models.Response`
        assert resolver.resolve(ref("models.Response")).verdict is Verdict.RESOLVED

    def test_external_package_unknown(self, resolver: PythonResolver) -> None:
        res = resolver.resolve(ref("werkzeug.Request.args"))
        assert res.verdict is Verdict.UNKNOWN
        assert res.boundary == "external:werkzeug"

    def test_dynamic_module_getattr_unknown(self, resolver: PythonResolver) -> None:
        res = resolver.resolve(ref("mypkg.dynamic.anything_at_all"))
        assert res.verdict is Verdict.UNKNOWN

    def test_foreign_callform_unknown_not_broken(self, resolver: PythonResolver) -> None:
        # fetch() / XMLHttpRequest() from a JS example paragraph
        assert resolver.resolve(ref("fetch", RefKind.SYMBOL_CALL)).verdict is Verdict.UNKNOWN


class TestTruePositives:
    def test_removed_symbol_is_broken(self, resolver: PythonResolver) -> None:
        # the httpx.Mounts shape: fully analyzed module, name absent
        res = resolver.resolve(ref("mypkg.Mounts"))
        assert res.verdict is Verdict.BROKEN

    def test_removed_method_is_broken(self, resolver: PythonResolver) -> None:
        res = resolver.resolve(ref("mypkg.Response.does_not_exist"))
        assert res.verdict is Verdict.BROKEN

    def test_removed_module_is_broken(self, resolver: PythonResolver) -> None:
        res = resolver.resolve(ref("mypkg.gone_module.thing"))
        assert res.verdict is Verdict.BROKEN


class TestOtherContracts:
    def test_local_callform_resolves(self, resolver: PythonResolver) -> None:
        assert resolver.resolve(ref("fetch_all", RefKind.SYMBOL_CALL)).verdict is (Verdict.RESOLVED)

    def test_module_exists_for_python_dash_m(self, resolver: PythonResolver) -> None:
        assert resolver.module_exists("mypkg.cli") is Verdict.RESOLVED
        assert resolver.module_exists("mypkg.nope") is Verdict.BROKEN
        assert resolver.module_exists("otherpkg.x") is Verdict.UNKNOWN

    def test_find_object_locates_source(self, resolver: PythonResolver) -> None:
        obj = resolver.find_object("mypkg.models.Response")
        assert obj is not None
        assert obj.lineno is not None
