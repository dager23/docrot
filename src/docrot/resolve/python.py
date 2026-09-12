"""Python symbol resolution — Gate 2, backed by griffe.

The contract is the tri-state verdict (PLAN.md §5.3): BROKEN is only
returned when the missing name's container is *fully analyzed* — no
``__getattr__``, no unexpanded wildcard imports, no unresolvable alias or
base class in the way. Anything dynamic degrades to UNKNOWN, never to a
finding. This asymmetry is what keeps precision high.
"""

from __future__ import annotations

import contextlib
import logging
from collections import defaultdict

import griffe

from docrot.discovery import PackageRoot
from docrot.model import Reference, Resolution, Verdict

logger = logging.getLogger(__name__)


class _Dynamic(Exception):
    """Resolution crossed a boundary we cannot analyze statically."""

    def __init__(self, boundary: str) -> None:
        self.boundary = boundary


class PythonResolver:
    def __init__(
        self,
        packages: list[PackageRoot],
        external_packages: tuple[str, ...] = (),
    ) -> None:
        self._modules: dict[str, griffe.Module] = {}
        self._by_class_name: dict[str, list[griffe.Class]] = defaultdict(list)
        self._by_module_basename: dict[str, list[griffe.Module]] = defaultdict(list)
        self._callables: set[str] = set()
        self.load_errors: list[str] = []

        search_paths = list({str(p.search_path) for p in packages})
        loader = griffe.GriffeLoader(search_paths=search_paths)
        for pkg in packages:
            try:
                mod = loader.load(pkg.name)
            except Exception as exc:  # loading must never break a run
                self.load_errors.append(f"{pkg.name}: {exc}")
                continue
            if isinstance(mod, griffe.Module):
                self._modules[pkg.name] = mod
        for name in external_packages:
            if name in self._modules:
                continue
            try:
                mod = loader.load(name)
            except Exception as exc:
                self.load_errors.append(f"{name} (external): {exc}")
                continue
            if isinstance(mod, griffe.Module):
                self._modules[name] = mod
        with contextlib.suppress(Exception):
            # implicit=True: internal imports like `from .sansio.app import
            # App` must resolve or base-class walks (Flask -> App) go blind
            loader.resolve_aliases(implicit=True, external=False)

        for mod in self._modules.values():
            self._walk_index(mod)

    # -- index ------------------------------------------------------------

    def _walk_index(self, obj: griffe.Object) -> None:
        for member in obj.members.values():
            if member.is_alias:
                continue
            assert isinstance(member, griffe.Object)
            if isinstance(member, griffe.Class):
                self._by_class_name[member.name].append(member)
                self._callables.add(member.name)
            elif isinstance(member, griffe.Module):
                self._by_module_basename[member.name].append(member)
            elif isinstance(member, griffe.Function):
                self._callables.add(member.name)
            if isinstance(member, (griffe.Module, griffe.Class)):
                self._walk_index(member)

    @property
    def local_package_names(self) -> frozenset[str]:
        return frozenset(self._modules)

    def top_level_module(self, name: str) -> griffe.Module | None:
        """The loaded package root, for callers that want to enumerate it."""
        return self._modules.get(name)

    def module_exists(self, dotted: str) -> Verdict:
        """For `python -m <dotted>` validation."""
        parts = dotted.split(".")
        if parts[0] not in self._modules:
            return Verdict.UNKNOWN
        node: griffe.Object | griffe.Alias = self._modules[parts[0]]
        for part in parts[1:]:
            try:
                obj = self._deref(node)
                node = self._lookup(obj, part)
            except _Dynamic:
                return Verdict.UNKNOWN
            except KeyError:
                return Verdict.BROKEN
        return Verdict.RESOLVED

    # -- resolution --------------------------------------------------------

    def resolve(self, ref: Reference) -> Resolution:
        target = ref.target
        parts = target.split(".")

        if len(parts) == 1:
            # bare call-form: existence anywhere is enough; absence proves
            # nothing (could be stdlib/external), so never BROKEN here.
            if target in self._callables:
                return Resolution(ref, Verdict.RESOLVED, resolved_as=target)
            return Resolution(ref, Verdict.UNKNOWN, boundary="bare-name")

        head = parts[0]
        if head in self._modules:
            return self._resolve_from(self._modules[head], parts[1:], ref, head)

        # Class-scoped reference: `Session.mount`, `Response.raw`
        candidates: list[tuple[str, griffe.Object | griffe.Alias]] = [
            (c.path, c) for c in self._by_class_name.get(head, [])
        ]
        # Module-basename reference: `sessions.Session`
        candidates += [(m.path, m) for m in self._by_module_basename.get(head, [])]

        if not candidates:
            return Resolution(ref, Verdict.UNKNOWN, boundary=f"external:{head}")

        saw_dynamic = False
        for path, obj in candidates:
            res = self._resolve_from(obj, parts[1:], ref, path)
            if res.verdict is Verdict.RESOLVED:
                return res
            if res.verdict is Verdict.UNKNOWN:
                saw_dynamic = True
        if saw_dynamic:
            return Resolution(ref, Verdict.UNKNOWN, boundary="dynamic:candidate")
        return Resolution(ref, Verdict.BROKEN)

    def _resolve_from(
        self,
        start: griffe.Object | griffe.Alias,
        parts: list[str],
        ref: Reference,
        prefix: str,
    ) -> Resolution:
        node = start
        walked = prefix
        for part in parts:
            try:
                node = self._deref(node)
            except _Dynamic as dyn:
                return Resolution(ref, Verdict.UNKNOWN, boundary=dyn.boundary)
            try:
                node = self._lookup(node, part)
            except _Dynamic as dyn:
                return Resolution(ref, Verdict.UNKNOWN, boundary=dyn.boundary)
            except KeyError:
                return Resolution(ref, Verdict.BROKEN, resolved_as=None, boundary=walked)
            walked = f"{walked}.{part}"
        resolved_as = None
        with contextlib.suppress(Exception):
            final = node.final_target if node.is_alias else node  # type: ignore[union-attr]
            resolved_as = final.path
        return Resolution(ref, Verdict.RESOLVED, resolved_as=resolved_as or walked)

    def find_object(self, target: str) -> griffe.Object | None:
        """Locate a module-rooted dotted target's final object (for anchors)."""
        parts = target.split(".")
        if parts[0] not in self._modules:
            return None
        node: griffe.Object | griffe.Alias = self._modules[parts[0]]
        for part in parts[1:]:
            try:
                obj = self._deref(node)
                node = self._lookup(obj, part)
            except (_Dynamic, KeyError):
                return None
        try:
            return self._deref(node)
        except _Dynamic:
            return None

    # -- griffe traversal helpers -------------------------------------------

    @staticmethod
    def _deref(node: griffe.Object | griffe.Alias) -> griffe.Object:
        seen = 0
        while node.is_alias:
            assert isinstance(node, griffe.Alias)
            seen += 1
            if seen > 10:
                raise _Dynamic("alias:cycle")
            try:
                node = node.final_target
            except Exception as exc:
                raise _Dynamic(f"alias:{type(exc).__name__}") from exc
        assert isinstance(node, griffe.Object)
        return node

    def _is_dynamic(self, obj: griffe.Object) -> bool:
        if isinstance(obj, griffe.Module):
            return "__getattr__" in obj.members or any(m.name == "*" for m in obj.members.values())
        return isinstance(obj, griffe.Attribute)

    def _lookup(self, obj: griffe.Object, name: str) -> griffe.Object | griffe.Alias:
        if isinstance(obj, griffe.Attribute):
            # value of unknown type (`request = LocalProxy(...)`): opaque
            raise _Dynamic("dynamic:attribute-value")
        if isinstance(obj, griffe.Function):
            raise _Dynamic("dynamic:function-attribute")

        members: dict[str, griffe.Object | griffe.Alias] = dict(obj.members)
        mro_incomplete = False
        if isinstance(obj, griffe.Class):
            try:
                mro = list(obj.mro())
                for base in mro:
                    for k, v in base.members.items():
                        members.setdefault(k, v)
                # griffe returns an empty mro instead of raising when base
                # expressions can't be resolved — that's incomplete, not final
                if obj.bases and not mro:
                    mro_incomplete = True
            except Exception:
                mro_incomplete = True

        if name in members:
            return members[name]
        if self._is_dynamic(obj) or mro_incomplete:
            raise _Dynamic(f"dynamic:{obj.path}")
        raise KeyError(name)
