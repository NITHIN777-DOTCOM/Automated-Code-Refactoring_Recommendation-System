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


def _parse_class(class_node: ast.ClassDef, file_path: str) -> ClassInfo:
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
    )


def parse_file(filepath: str) -> list[ClassInfo]:
    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source, filename=filepath)
    classes = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            classes.append(_parse_class(node, filepath))

    return classes


def parse_repo(repo_path: str, exclude: list[str] | None = None) -> list[ClassInfo]:
    excluded = DEFAULT_EXCLUDED_DIRS | set(exclude or [])
    all_classes = []

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if not is_excluded_dir(d, excluded)]

        for filename in files:
            if not filename.endswith(".py"):
                continue
            filepath = os.path.join(root, filename)
            try:
                all_classes.extend(parse_file(filepath))
            except (SyntaxError, UnicodeDecodeError, OSError) as exc:
                logger.warning("Skipping %s: could not parse (%s)", filepath, exc)

    return all_classes
