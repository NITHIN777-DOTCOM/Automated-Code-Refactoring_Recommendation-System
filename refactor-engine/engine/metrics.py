"""Metric calculation functions operating on ClassInfo/MethodInfo objects."""

from __future__ import annotations

import ast

from engine.models import ClassInfo, MethodInfo, MetricResult


def method_count(class_info: ClassInfo) -> MetricResult:
    return MetricResult(
        class_name=class_info.name,
        metric_name="method_count",
        value=len(class_info.methods),
    )


def field_count(class_info: ClassInfo) -> MetricResult:
    return MetricResult(
        class_name=class_info.name,
        metric_name="field_count",
        value=len(class_info.fields),
    )


def average_method_length(class_info: ClassInfo) -> MetricResult:
    if not class_info.methods:
        return MetricResult(class_name=class_info.name, metric_name="average_method_length", value=0.0)

    lengths = [method_length(m) for m in class_info.methods]
    return MetricResult(
        class_name=class_info.name,
        metric_name="average_method_length",
        value=sum(lengths) / len(lengths),
    )


_DECISION_NODE_TYPES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler)


def cyclomatic_complexity(method: MethodInfo) -> int:
    if method.body is None:
        return 1

    complexity = 1
    for node in ast.walk(method.body):
        if isinstance(node, _DECISION_NODE_TYPES):
            complexity += 1
        elif isinstance(node, ast.BoolOp):
            complexity += len(node.values) - 1
        elif isinstance(node, ast.IfExp):
            complexity += 1

    return complexity


def method_length(method: MethodInfo) -> int:
    """Inclusive line count -- a single-line method measures 1, not 0."""
    return method.end_line - method.start_line + 1


def parameter_count(method: MethodInfo) -> int:
    return len([p for p in method.params if p != "self"])


def class_length(cls: ClassInfo) -> int:
    """Inclusive line count -- a single-line class measures 1, not 0."""
    return cls.end_line - cls.start_line + 1


def lcom(cls: ClassInfo) -> float:
    methods = cls.methods
    total_pairs = 0
    non_sharing_pairs = 0

    for i in range(len(methods)):
        for j in range(i + 1, len(methods)):
            total_pairs += 1
            fields_i = set(methods[i].fields_accessed)
            fields_j = set(methods[j].fields_accessed)
            if not (fields_i & fields_j):
                non_sharing_pairs += 1

    if total_pairs == 0:
        return 0.0

    return non_sharing_pairs / total_pairs


def cbo(cls: ClassInfo, all_classes: list[ClassInfo]) -> int:
    other_class_names = {c.name for c in all_classes if c.name != cls.name}
    referenced = set()

    for method in cls.methods:
        for call_name in method.calls_made:
            if call_name in other_class_names:
                referenced.add(call_name)

    return len(referenced)


def method_owner_index(all_classes: list[ClassInfo], exclude_name: str) -> dict[str, set[str]]:
    """Map every method name in the codebase to the set of classes defining it.

    Public because the Feature Envy suggester attributes a method's outbound
    calls with exactly this heuristic -- sharing the function keeps the
    suggestion consistent with the fan_out/cbo numbers reported alongside it.
    A name mapping to more than one class is genuinely ambiguous: the callers
    here decide what to do about that.
    """
    index: dict[str, set[str]] = {}
    for c in all_classes:
        if c.name == exclude_name:
            continue
        for m in c.methods:
            index.setdefault(m.name, set()).add(c.name)
    return index


def fan_out(cls: ClassInfo, all_classes: list[ClassInfo]) -> int:
    other_class_names = {c.name for c in all_classes if c.name != cls.name}
    method_owners = method_owner_index(all_classes, cls.name)
    external_refs = set()

    for method in cls.methods:
        for call_name in method.calls_made:
            if call_name in other_class_names:
                external_refs.add(("class", call_name))
            elif call_name in method_owners:
                for owner in method_owners[call_name]:
                    external_refs.add(("method", owner, call_name))

    return len(external_refs)


def fan_in(cls: ClassInfo, all_classes: list[ClassInfo]) -> int:
    cls_method_names = {m.name for m in cls.methods}
    callers = set()

    for other in all_classes:
        if other.name == cls.name:
            continue
        for method in other.methods:
            for call_name in method.calls_made:
                if call_name == cls.name or call_name in cls_method_names:
                    callers.add(other.name)
                    break

    return len(callers)


def depth_of_inheritance(cls: ClassInfo, all_classes: list[ClassInfo]) -> int:
    class_map = {c.name: c for c in all_classes}
    depth = 0
    current = cls
    visited = {cls.name}

    while current.base_classes:
        base_name = current.base_classes[0]
        if base_name not in class_map or base_name in visited:
            break
        depth += 1
        visited.add(base_name)
        current = class_map[base_name]

    return depth


def compute_all_metrics(classes: list[ClassInfo]) -> dict:
    """Per-class metrics, keyed by both `cls.name` AND `id(cls)`.

    Two classes sharing a name in the same file are routine now that nested
    classes are parsed as independent entities -- e.g. five Django models in
    one models.py each carrying their own `class Meta:`. Keying by name alone
    would let the later same-named class silently overwrite the earlier
    one's entry, dropping it from every downstream lookup.

    The name key is kept, unchanged, for existing single-class-per-name
    callers (tests, the synthetic dataset generator) that look a class up by
    its name string. Callers iterating a class list where a name collision
    is possible should look up by `id(cls)` instead -- guaranteed unique
    within one call, and resolvable because they already hold the ClassInfo
    object.
    """
    results = {}

    for cls in classes:
        method_metrics = {
            m.name: {
                "cyclomatic_complexity": cyclomatic_complexity(m),
                "length": method_length(m),
            }
            for m in cls.methods
        }

        entry = {
            "class_length": class_length(cls),
            "lcom": lcom(cls),
            "cbo": cbo(cls, classes),
            "fan_in": fan_in(cls, classes),
            "fan_out": fan_out(cls, classes),
            "depth_of_inheritance": depth_of_inheritance(cls, classes),
            "methods": method_metrics,
        }
        results[cls.name] = entry
        results[id(cls)] = entry

    return results
