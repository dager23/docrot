"""Historical resolution — did this reference resolve at a past commit?

Gate 1 of the two-gate design. Deliberately *strict*: it answers "did this
provably exist then" via direct AST evidence in single blobs (no checkout),
with a bounded re-export chase. Fiction (tutorial placeholders, external
names) never resolved, so it never produces drift findings.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from docrot.model import Reference, RefKind
from docrot.temporal.git import Git

_MAX_CHASE = 3


@dataclass(frozen=True)
class _ModuleNames:
    defs: frozenset[str]  # functions/classes/assigns defined here
    classes: frozenset[str]
    imports: dict[str, str]  # local name -> source module (relative resolved)
    star_imports: tuple[str, ...]
    class_members: dict[str, frozenset[str]]  # class -> methods/attrs incl. self.*
    class_bases: dict[str, tuple[str, ...]]


def _parse_module(source: str, module_dotted: str) -> _ModuleNames | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    defs: set[str] = set()
    classes: set[str] = set()
    imports: dict[str, str] = {}
    stars: list[str] = []
    class_members: dict[str, frozenset[str]] = {}
    class_bases: dict[str, tuple[str, ...]] = {}

    def resolve_relative(module: str | None, level: int) -> str:
        if level == 0:
            return module or ""
        parts = module_dotted.split(".")
        base = parts[: len(parts) - level] if level <= len(parts) else []
        return ".".join([*base, module] if module else base)

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs.add(node.name)
        elif isinstance(node, ast.ClassDef):
            defs.add(node.name)
            classes.add(node.name)
            members: set[str] = set()
            for sub in ast.walk(node):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    members.add(sub.name)
                elif isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            members.add(t.id)
                        elif (
                            isinstance(t, ast.Attribute)
                            and isinstance(t.value, ast.Name)
                            and t.value.id == "self"
                        ):
                            members.add(t.attr)
                elif isinstance(sub, ast.AnnAssign):
                    tgt = sub.target
                    if isinstance(tgt, ast.Name):
                        members.add(tgt.id)
                    elif (
                        isinstance(tgt, ast.Attribute)
                        and isinstance(tgt.value, ast.Name)
                        and tgt.value.id == "self"
                    ):
                        members.add(tgt.attr)
            class_members[node.name] = frozenset(members)
            class_bases[node.name] = tuple(b.id for b in node.bases if isinstance(b, ast.Name))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defs.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defs.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            src = resolve_relative(node.module, node.level)
            for a in node.names:
                if a.name == "*":
                    stars.append(src)
                else:
                    imports[a.asname or a.name] = src
        elif isinstance(node, ast.Import):
            for a in node.names:
                imports[a.asname or a.name.split(".")[0]] = a.name

    return _ModuleNames(
        frozenset(defs),
        frozenset(classes),
        imports,
        tuple(stars),
        class_members,
        class_bases,
    )


class HistoricalResolver:
    """Resolves references against the repository state at a given commit."""

    def __init__(self, git: Git, package_dirs: dict[str, str]) -> None:
        # package name -> repo-relative posix dir of the package
        self.git = git
        self.package_dirs = package_dirs
        self._module_cache: dict[tuple[str, str], _ModuleNames | None] = {}

    # -- module plumbing ----------------------------------------------------

    def _module_file(self, commit: str, dotted: str) -> str | None:
        parts = dotted.split(".")
        pkg_dir = self.package_dirs.get(parts[0])
        if pkg_dir is None:
            return None
        rel = "/".join(parts[1:])
        for candidate in (
            f"{pkg_dir}/{rel}.py" if rel else None,
            f"{pkg_dir}/{rel}/__init__.py" if rel else f"{pkg_dir}/__init__.py",
        ):
            if candidate and self.git.exists_at(commit, candidate):
                return candidate
        return None

    def _load(self, commit: str, dotted: str) -> _ModuleNames | None:
        key = (commit, dotted)
        if key in self._module_cache:
            return self._module_cache[key]
        path = self._module_file(commit, dotted)
        names = None
        if path is not None:
            blob = self.git.blob(commit, path)
            if blob is not None:
                names = _parse_module(blob, dotted)
        self._module_cache[key] = names
        return names

    # -- name lookup with bounded re-export chase ----------------------------

    def _name_in_module(self, commit: str, module: str, name: str, depth: int = 0) -> bool:
        if depth > _MAX_CHASE:
            return False
        names = self._load(commit, module)
        if names is None:
            return False
        if name in names.defs:
            return True
        if name in names.imports:
            # the name was importable here at that commit — sufficient evidence
            return True
        for star_src in names.star_imports:
            if star_src.split(".")[0] in self.package_dirs and self._name_in_module(
                commit, star_src, name, depth + 1
            ):
                return True
        return False

    def _member_in_class(
        self, commit: str, module: str, cls: str, member: str, depth: int = 0
    ) -> bool:
        if depth > _MAX_CHASE:
            return False
        names = self._load(commit, module)
        if names is None or cls not in names.class_members:
            return False
        if member in names.class_members[cls]:
            return True
        for base in names.class_bases.get(cls, ()):
            if base in names.class_members and self._member_in_class(
                commit, module, base, member, depth + 1
            ):
                return True
            base_src = names.imports.get(base)
            if (
                base_src
                and base_src.split(".")[0] in self.package_dirs
                and self._member_in_class(commit, base_src, base, member, depth + 1)
            ):
                return True
        return False

    # -- public API ----------------------------------------------------------

    def symbol_resolved_at(self, commit: str, target: str) -> bool:
        parts = target.split(".")
        head = parts[0]

        if head in self.package_dirs:
            # try every module/attr split, longest module first
            for k in range(len(parts), 0, -1):
                module = ".".join(parts[:k])
                if self._module_file(commit, module) is None:
                    continue
                rest = parts[k:]
                if not rest:
                    return True
                if len(rest) == 1:
                    return self._name_in_module(commit, module, rest[0])
                if len(rest) == 2:
                    names = self._load(commit, module)
                    if (
                        names
                        and rest[0] in names.classes
                        and self._member_in_class(commit, module, rest[0], rest[1])
                    ):
                        return True
                    # maybe rest[0] is a re-exported class: accept import evidence
                    if names and rest[0] in names.imports:
                        src = names.imports[rest[0]]
                        if src.split(".")[0] in self.package_dirs and (
                            self._member_in_class(commit, src, rest[0], rest[1])
                        ):
                            return True
                # deeper chains than module.Class.member: unsupported, treat
                # as unresolved-then (strict gate)
            # Module-rooted references get NO grep fallback: the structured
            # walk is authoritative for "resolves as written". Grep evidence
            # ("a def of that name existed somewhere") conflates existence
            # with addressability and mislabels never-valid references
            # (rich.ScreenContext.update lesson).
            return False

        # class-scoped / bare references: definition-level grep evidence
        return self._grep_definition(commit, parts[-1], class_hint=head)

    def _grep_definition(self, commit: str, name: str, class_hint: str | None = None) -> bool:
        """`git grep` for definition-level existed-then evidence.

        For class-scoped refs (`Session.mount`) both the class and the
        member must have existed; a member match alone proves nothing.
        """
        for pkg_dir in self.package_dirs.values():
            member_seen = self.git.grep_exists(f"def {name}", commit, pkg_dir) or (
                self.git.grep_exists(f"self.{name} =", commit, pkg_dir)
            )
            if not member_seen:
                # top-level def/class of that name?
                if class_hint is None and self.git.grep_exists(f"class {name}", commit, pkg_dir):
                    return True
                continue
            if class_hint is None:
                return True
            if class_hint[0].isupper() and self.git.grep_exists(
                f"class {class_hint}", commit, pkg_dir
            ):
                return True
        return False

    def path_resolved_at(self, commit: str, target: str, exact: bool = False) -> bool:
        if self.git.exists_at(commit, target):
            return True
        if exact:
            return False
        basename = target.rsplit("/", 1)[-1]
        return any(p.rsplit("/", 1)[-1] == basename for p in self.git.tree_paths(commit))

    def resolved_at(self, commit: str, ref: Reference, exact_path: bool = False) -> bool:
        if ref.kind in (RefKind.SYMBOL_DOTTED, RefKind.SYMBOL_CALL):
            return self.symbol_resolved_at(commit, ref.target)
        if ref.kind in (RefKind.PATH, RefKind.LINK):
            return self.path_resolved_at(commit, ref.target, exact=exact_path)
        return False
