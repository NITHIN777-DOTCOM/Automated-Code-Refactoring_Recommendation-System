"""Metric calculation functions operating on ClassInfo/MethodInfo objects."""

from __future__ import annotations

import ast
from collections import Counter

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


# ---------------------------------------------------------------------------
# Real ATFD / FDP (Lanza & Marinescu)
#
# These replace the old fan_out-derived "ATFD proxy". fan_out survives as its
# own metric -- it answers "how many other classes does this one call into",
# which is a useful coupling number in its own right -- but it is no longer
# presented as a stand-in for access-to-foreign-data, because a real one now
# exists: engine/parser.py records every non-self `x.attr` read, which the
# proxy never had access to.
#
# STAGE 1: name-based. Providers are grouped by the literal receiver variable
# name, with no type inference. See MethodInfo.foreign_accesses.
# ---------------------------------------------------------------------------


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def foreign_accesses(cls: ClassInfo) -> list[tuple[str, str]]:
    """This class's (receiver, attribute) foreign reads, after exclusions.

    Excluded, each for a reason that is about inheritance or plumbing rather
    than envy:

      * dunder attributes -- protocol machinery, not another class's data.
      * a receiver that is one of our own base class NAMES, e.g.
        `BaseOAuth2.setting(...)`: that is an explicit super-call, and
        reaching into the class you inherit from is what inheritance is for.

    Note what is NOT excluded any more. The old proxy had to drop any call
    whose name matched one of our own methods, because it only saw a bare
    call name and could not tell `self.save()` from `other.save()`. Receivers
    are known here, so `other.save()` is correctly counted as foreign even
    when this class also defines `save`.
    """
    base_names = set(cls.base_classes)
    return [
        (receiver, attribute)
        for method in cls.methods
        for receiver, attribute in method.foreign_accesses
        if not _is_dunder(attribute) and receiver not in base_names
    ]


def atfd(cls: ClassInfo) -> int:
    """Access To Foreign Data: distinct (provider, attribute) pairs reached.

    Pairs rather than bare attribute names, so a class that reads `.name` off
    three different providers scores 3 -- it is touching three separate pieces
    of foreign data, which is what the metric is meant to capture.
    """
    return len(set(foreign_accesses(cls)))


def foreign_data_providers(cls: ClassInfo) -> Counter:
    """How many distinct attributes this class reads off each provider."""
    per_provider: dict[str, set[str]] = {}
    for receiver, attribute in foreign_accesses(cls):
        per_provider.setdefault(receiver, set()).add(attribute)
    return Counter({receiver: len(attrs) for receiver, attrs in per_provider.items()})


def fdp(cls: ClassInfo) -> int:
    """Foreign Data Providers: how many distinct providers the reads land on.

    Lanza & Marinescu use FDP <= FEW to separate real Feature Envy -- a class
    fixated on ONE neighbour, which Move Method can fix -- from a class that
    touches a little of everything, which is a dispersed-coupling problem
    needing entirely different advice.
    """
    return len(foreign_data_providers(cls))


def fdp_concentration(cls: ClassInfo) -> float:
    """Share of foreign reads landing on the single most-read provider.

    1.0 = every foreign read goes to one provider (textbook envy); near 0 =
    scattered across many. Returns 0.0 when there is no foreign access at
    all, which is a "no concentration to speak of" sentinel rather than a
    measurement -- read it together with atfd, never alone.
    """
    providers = foreign_data_providers(cls)
    total = sum(providers.values())
    if not total:
        return 0.0
    return providers.most_common(1)[0][1] / total


def envy_metrics_for(cls: ClassInfo) -> dict:
    """The real ATFD/FDP/LAA triple for one class.

    LAA is own-attribute reads over own-plus-foreign. It is closer to the
    published metric than the old proxy was -- the foreign half is now a real
    foreign-data count rather than resolved call names -- but it is still
    aggregated across the whole class where Lanza & Marinescu define it per
    method, so it stays flagged as an approximation.
    """
    own = sum(len(m.fields_accessed) for m in cls.methods)
    foreign = atfd(cls)
    total = own + foreign
    return {
        "atfd": foreign,
        "fdp": fdp(cls),
        "fdp_concentration": round(fdp_concentration(cls), 4),
        "laa": (own / total) if total else 1.0,
    }


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
            # Real ATFD/FDP. Unlike cbo/fan_in/fan_out these need no
            # `classes` scope at all: a foreign attribute read is visible in
            # the class's own AST, which is precisely why it does not suffer
            # the scope sensitivity that made the old proxy swing 4 -> 405
            # between file and corpus scope.
            **envy_metrics_for(cls),
        }
        results[cls.name] = entry
        results[id(cls)] = entry

    return results
