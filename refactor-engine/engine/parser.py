"""AST walking logic to extract ClassInfo/MethodInfo from Python source files."""

from __future__ import annotations

import ast
import fnmatch
import logging
import os

from engine.models import ClassInfo, MethodInfo

logger = logging.getLogger(__name__)

# Directories that are never source code worth analyzing: virtual envs,
# installed dependencies, VCS metadata, build output. Skipped by default so
# a scan of a project root doesn't recurse into a venv/ sitting next to it
# and choke on non-UTF-8 / vendored files that were never the user's code.
DEFAULT_EXCLUDED_DIRS = frozenset(
    {
        "venv",
        ".venv",
        "env",
        "test_env",
        "__pycache__",
        "site-packages",
        ".git",
        "node_modules",
        "build",
        "dist",
    }
)
# Matched separately since it's a glob pattern (e.g. "foo.egg-info"), not a
# fixed name.
_EGG_INFO_PATTERN = "*.egg-info"


def is_excluded_dir(dirname: str, excluded: frozenset[str]) -> bool:
    return dirname in excluded or fnmatch.fnmatch(dirname, _EGG_INFO_PATTERN)


def iter_python_files(repo_path: str, exclude: list[str] | None = None):
    """Yield every .py file under repo_path, pruning excluded directories.

    Shared by parse_repo() (which needs the classes) and the pipeline's
    unused-import check (which needs every file, including ones with no
    classes at all -- a pure-script file is exactly the kind of file that
    tends to accumulate unused imports).
    """
    excluded = DEFAULT_EXCLUDED_DIRS | set(exclude or [])

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if not is_excluded_dir(d, excluded)]
        for filename in files:
            if filename.endswith(".py"):
                yield os.path.join(root, filename)


def _extract_calls_and_fields(node: ast.AST, self_name: str | None) -> tuple[list[str], list[str]]:
    calls = []
    fields = []
    call_func_ids = set()

    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            call_func_ids.add(id(func))
            if isinstance(func, ast.Name):
                calls.append(func.id)
            elif isinstance(func, ast.Attribute):
                calls.append(func.attr)

    for child in ast.walk(node):
        if (
            isinstance(child, ast.Attribute)
            and isinstance(child.value, ast.Name)
            and self_name is not None
            and child.value.id == self_name
            and id(child) not in call_func_ids
        ):
            fields.append(child.attr)

    seen = set()
    unique_fields = []
    for name in fields:
        if name not in seen:
            seen.add(name)
            unique_fields.append(name)

    return calls, unique_fields


def _self_param_name(func_node: ast.FunctionDef) -> str | None:
    args = func_node.args.args
    if args:
        return args[0].arg
    return None


def _parse_method(func_node: ast.FunctionDef, class_name: str) -> MethodInfo:
    self_name = _self_param_name(func_node)
    params = [a.arg for a in func_node.args.args]
    calls_made, fields_accessed = _extract_calls_and_fields(func_node, self_name)

    return MethodInfo(
        name=func_node.name,
        class_name=class_name,
        start_line=func_node.lineno,
        end_line=getattr(func_node, "end_lineno", func_node.lineno),
        params=params,
        body=func_node,
        calls_made=calls_made,
        fields_accessed=fields_accessed,
    )


def _base_class_names(class_node: ast.ClassDef) -> list[str]:
    names = []
    for base in class_node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


def _parse_class(class_node: ast.ClassDef, file_path: str, parent_class: str | None) -> ClassInfo:
    """Build a ClassInfo from class_node's OWN direct body only.

    A nested class living in class_node.body is a ClassDef, not a
    FunctionDef/AsyncFunctionDef, so the loop below already skips it -- its
    methods and lines are never folded into this class's own methods/fields.
    parse_file() parses that nested class separately, as its own independent
    ClassInfo, so nothing here needs to know it exists.
    """
    methods = []
    fields = set()

    for item in class_node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            method_info = _parse_method(item, class_node.name)
            methods.append(method_info)
            fields.update(method_info.fields_accessed)

    return ClassInfo(
        name=class_node.name,
        file_path=file_path,
        methods=methods,
        fields=sorted(fields),
        base_classes=_base_class_names(class_node),
        start_line=class_node.lineno,
        end_line=getattr(class_node, "end_lineno", class_node.lineno),
        parent_class=parent_class,
    )


def _enclosing_class_names(tree: ast.Module) -> dict[int, str]:
    """Map id(class_node) -> name of the nearest ClassDef textually
    enclosing it (at any depth, through methods/if-blocks/etc.), for every
    ClassDef in the tree. A module-level class has no entry.

    Built with one parent-pointer pass rather than tracked during a
    recursive descent -- ast has no parent links, and this is the standard
    way to get them without hand-rolling recursion over every node type
    (FunctionDef, If, For, Try, ...) that could contain a nested class.
    """
    parent_of: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent_of[id(child)] = node

    enclosing: dict[int, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        ancestor = parent_of.get(id(node))
        while ancestor is not None and not isinstance(ancestor, ast.ClassDef):
            ancestor = parent_of.get(id(ancestor))
        if ancestor is not None:
            enclosing[id(node)] = ancestor.name

    return enclosing


def parse_file(filepath: str) -> list[ClassInfo]:
    """Every class in a file, at ANY nesting depth, each as its own
    independent ClassInfo -- module-level classes and classes nested inside
    another class (e.g. Django's `class Meta:`) or inside a function/method.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source, filename=filepath)
    enclosing = _enclosing_class_names(tree)
    classes = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            classes.append(_parse_class(node, filepath, enclosing.get(id(node))))

    return classes


def parse_repo(repo_path: str, exclude: list[str] | None = None) -> list[ClassInfo]:
    all_classes = []

    for filepath in iter_python_files(repo_path, exclude=exclude):
        try:
            all_classes.extend(parse_file(filepath))
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            logger.warning("Skipping %s: could not parse (%s)", filepath, exc)

    return all_classes


# ---------------------------------------------------------------------------
# Unused imports
#
# A pure static check, not ML-based: an imported name is either referenced
# somewhere in the file's AST or it isn't. Deliberately module-level-only
# (imports inside a function/class body are not tracked) -- that covers the
# overwhelming majority of real unused imports and keeps the scope check
# simple; a name-shadowing edge case (a later local variable reusing an
# import's name) or a `__all__` re-export would both read as "used" here
# even when a stricter tool like pyflakes might flag them differently.
# ---------------------------------------------------------------------------


def _import_bindings(tree: ast.Module) -> list[dict]:
    """Every name a file's top-level import statements bind, in source order."""
    bindings = []

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound_name = alias.asname or alias.name.split(".")[0]
                display = alias.name if not alias.asname else f"{alias.name} as {alias.asname}"
                bindings.append({"name": bound_name, "line": node.lineno, "display": display})
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                module = "." * node.level + (node.module or "")
            else:
                module = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    # A star import's bound names aren't statically knowable,
                    # so there's nothing to check usage of.
                    continue
                bound_name = alias.asname or alias.name
                imported = alias.name if not alias.asname else f"{alias.name} as {alias.asname}"
                bindings.append(
                    {"name": bound_name, "line": node.lineno, "display": f"from {module} import {imported}"}
                )

    return bindings


def _referenced_names(tree: ast.Module) -> set[str]:
    """Every plain name loaded anywhere in the file. Import aliases are
    plain strings in the AST (ast.alias), never ast.Name nodes, so a walk
    over the whole tree can't accidentally count an import statement as a
    use of its own binding."""
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}


def find_unused_imports_detailed(file_path: str) -> list[dict]:
    """Unused top-level imports in a file, each as {"name", "line", "display"},
    in source order. find_unused_imports() below returns just the names --
    use this version when line numbers matter (the CLI report needs them)."""
    with open(file_path, "r", encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source, filename=file_path)
    used = _referenced_names(tree)

    return [binding for binding in _import_bindings(tree) if binding["name"] not in used]


def find_unused_imports(file_path: str) -> list[str]:
    return [binding["name"] for binding in find_unused_imports_detailed(file_path)]
