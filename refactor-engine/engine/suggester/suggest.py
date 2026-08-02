"""Turns field-sharing clusters into human-readable Extract Class suggestions.

NAMING HEURISTIC (no ML, just string matching -- documented so it's easy to
replace later):

  1. Concept word: prefer the cluster's shared FIELDS over its method names,
     since a field name names the "thing" the methods operate on (e.g.
     "footer" is a better concept than "set"/"render"). Field names are
     stemmed by stripping common suffixes (_path, _id, _name, _text, ...)
     and, when a cluster shares more than one field, the token that recurs
     most often across the stemmed field names wins (ties broken
     alphabetically for determinism). If a cluster shares no fields at all
     (shouldn't happen given how clusters are built, but handled anyway),
     fall back to the most frequent non-verb token across method names.
  2. Role suffix: guessed from the cluster's method-name verb prefixes,
     checked in this priority order -- render* -> "Renderer" (the class
     mostly formats/outputs something), add/remove/clear/append ->
     "Collection" (the class manages a group of items), compute/calculate ->
     "Calculator", a majority of get_/set_ methods -> "Data", otherwise a
     generic "Handler".
  3. Base name: the source class name with a common God Class suffix
     (Manager, Handler, Service, Controller, Processor, Helper, Class)
     stripped, so "ReportManager" -> "Report".
  4. suggested_name = f"{base}{Concept}{Role}", e.g. "ReportManager" +
     footer/render_footer,set_footer -> "ReportFooterRenderer".

This is a heuristic, not a guarantee of a good name -- it exists to give a
human a starting point to rename, not a final answer.
"""

from __future__ import annotations

from collections import Counter

from engine.models import ClassInfo
from engine.suggester.cluster import CONSTRUCTOR_NAMES

FIELD_SUFFIXES_TO_STRIP = ("_path", "_id", "_name", "_text", "_value", "_count", "_flag")

CLASS_NAME_SUFFIXES_TO_STRIP = (
    "Manager",
    "Handler",
    "Controller",
    "Processor",
    "Service",
    "Helper",
    "Class",
)

VERB_PREFIXES = {
    "get",
    "set",
    "add",
    "remove",
    "clear",
    "compute",
    "calculate",
    "reset",
    "render",
    "update",
    "is",
    "has",
    "build",
    "make",
    "create",
    "delete",
}


def _stem_field(field_name: str) -> str:
    for suffix in FIELD_SUFFIXES_TO_STRIP:
        if field_name.endswith(suffix) and len(field_name) > len(suffix):
            return field_name[: -len(suffix)]
    return field_name


def _most_common_or_first(tokens: list[str]) -> str | None:
    if not tokens:
        return None
    counts = Counter(tokens)
    top = max(counts.values())
    winners = sorted(t for t, c in counts.items() if c == top)
    return winners[0]


def _concept_word(method_names: list[str], fields: list[str]) -> str:
    if fields:
        concept = _most_common_or_first([_stem_field(f) for f in fields])
        if concept:
            return concept

    tokens = [t for name in method_names for t in name.split("_") if t and t not in VERB_PREFIXES]
    return _most_common_or_first(tokens) or "Extracted"


def _role_suffix(method_names: list[str]) -> str:
    prefixes = [name.split("_", 1)[0] for name in method_names]

    if any(p == "render" for p in prefixes):
        return "Renderer"
    if any(p in ("add", "remove", "clear", "append") for p in prefixes):
        return "Collection"
    if any(p in ("compute", "calculate") for p in prefixes):
        return "Calculator"
    if sum(p in ("get", "set") for p in prefixes) > len(prefixes) / 2:
        return "Data"
    return "Handler"


def _strip_class_suffix(class_name: str) -> str:
    for suffix in CLASS_NAME_SUFFIXES_TO_STRIP:
        if class_name.endswith(suffix) and len(class_name) > len(suffix):
            return class_name[: -len(suffix)]
    return class_name


def _suggest_class_name(source_class_name: str, method_names: list[str], fields: list[str]) -> str:
    concept = _concept_word(method_names, fields)
    concept_title = "".join(part.capitalize() for part in concept.split("_"))
    role = _role_suffix(method_names)
    base = _strip_class_suffix(source_class_name)
    return f"{base}{concept_title}{role}"


def _cluster_fields(cluster: set[str], fields_by_method: dict[str, list[str]]) -> list[str]:
    fields = set()
    for method_name in cluster:
        fields.update(fields_by_method.get(method_name, []))
    return sorted(fields)


def _build_extract_suggestion(
    cls: ClassInfo, cluster: set[str], fields_by_method: dict[str, list[str]]
) -> dict:
    method_names = sorted(cluster)
    cluster_fields = _cluster_fields(cluster, fields_by_method)

    # Constructors are excluded from this comparison to mirror the clustering
    # policy (see engine/suggester/cluster.py): __init__ typically assigns
    # every field the class owns, so including it here would make every
    # field look "shared with the rest of the class" even when it is
    # genuinely exclusive to this cluster's business methods.
    other_fields = set()
    for name, method_fields in fields_by_method.items():
        if name not in cluster and name not in CONSTRUCTOR_NAMES:
            other_fields.update(method_fields)
    exclusive = all(f not in other_fields for f in cluster_fields)

    suggested_name = _suggest_class_name(cls.name, method_names, cluster_fields)

    if cluster_fields:
        field_list = ", ".join(cluster_fields)
        scope = "not used by the rest of the class" if exclusive else "used mostly within this group"
        rationale = (
            f"These methods share state ({field_list}) {scope}, "
            "indicating a separate responsibility."
        )
    else:
        rationale = (
            "These methods were grouped together, but share no common fields -- "
            "review this suggestion manually before acting on it."
        )

    return {
        "type": "extract_class",
        "source_class": cls.name,
        "methods_to_extract": method_names,
        "suggested_name": suggested_name,
        "shared_fields": cluster_fields,
        "rationale": rationale,
    }


def generate_suggestions(cls: ClassInfo, clusters: list[set[str]]) -> list[dict]:
    if len(clusters) < 2:
        return [
            {
                "type": "no_clear_split",
                "source_class": cls.name,
                "note": (
                    f"No clear extraction boundary found in {cls.name} via field-sharing "
                    "analysis. Its methods either form one interconnected group or lack "
                    "enough shared state to propose a split. This can still be a God "
                    "Class -- it just doesn't decompose cleanly along field-access lines."
                ),
            }
        ]

    fields_by_method = {m.name: m.fields_accessed for m in cls.methods}
    return [_build_extract_suggestion(cls, cluster, fields_by_method) for cluster in clusters]
