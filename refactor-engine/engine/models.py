"""Dataclasses shared across the refactor-engine parser and metrics modules."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


@dataclass
class MethodInfo:
    name: str
    class_name: str
    start_line: int
    end_line: int
    params: list[str] = field(default_factory=list)
    body: ast.AST | None = None
    calls_made: list[str] = field(default_factory=list)
    fields_accessed: list[str] = field(default_factory=list)


@dataclass
class ClassInfo:
    name: str
    file_path: str
    methods: list[MethodInfo] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    base_classes: list[str] = field(default_factory=list)
    start_line: int = 0
    end_line: int = 0
    # Name of the class textually enclosing this one (e.g. "Meta" nested
    # inside a Django model), or None for a module-level class. Metrics and
    # labeling treat a nested class as an independent ClassInfo regardless --
    # this is metadata for callers that want to know it was nested.
    parent_class: str | None = None


@dataclass
class MetricResult:
    class_name: str
    metric_name: str
    value: float
    details: dict = field(default_factory=dict)
