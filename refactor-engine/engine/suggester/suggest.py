"""Turns analysis output into human-readable refactoring suggestions.

Three strategies live here:

  * "extract_class" -- built from the field-sharing method clusters produced
    by cluster.py. Naming heuristic documented immediately below.
  * "extract_method" -- built from the statement blocks produced by
    blocks.py, for methods flagged Long Method. Naming heuristic documented
    above generate_extract_method_suggestions().
  * "move_method" -- built from outbound-call attribution, for classes
    flagged Feature Envy. Documented above
    generate_move_method_suggestions(). Emits "no_clear_envy_target" instead
    of naming a destination when the coupling data doesn't single one out.

EXTRACT CLASS NAMING HEURISTIC (no ML, just string matching -- documented so
it's easy to replace later):

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

import ast

from engine.metrics import method_owner_index
from engine.models import ClassInfo, MethodInfo
from engine.suggester.blocks import MIN_BLOCK_STATEMENTS, is_local, split_into_blocks
from engine.suggester.cluster import CONSTRUCTOR_NAMES

# A method must be at least this many lines before its internals are worth
# picking apart -- a tight 8-line method with two logical halves does not
# need a helper extracted out of it.
LONG_METHOD_LINE_THRESHOLD = 15

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


# ---------------------------------------------------------------------------
# Extract Method
# ---------------------------------------------------------------------------


def _block_subject(block) -> str:
    """The one name a block is most 'about', used to name its helper.

    Preference order, most to least meaningful:
      1. a value the block returns -- that IS the block's purpose;
      2. a value it produces that outlives it and wasn't handed to it, i.e.
         something genuinely new rather than an incoming value being updated;
      3. the local it writes most often that isn't one of its inputs;
      4. any local it writes; then any input; then a generic fallback.

    Step 2's "wasn't handed to it" clause is what keeps two consecutive
    blocks that both end up assigning `total` from both being called
    _compute_total: the second one only updates a `total` it received, so it
    falls through to its own dominant local (`shipping_cost`) instead.
    """
    returned_locals = sorted(n for n in block.returned if is_local(n))
    if returned_locals:
        return returned_locals[0]

    fresh_outputs = [o for o in block.outputs if o not in block.external_reads]
    if fresh_outputs:
        return fresh_outputs[0]

    def _ranked(names):
        return sorted(names, key=lambda t: (-t[1], t[0]))

    internal = _ranked(
        (name, count)
        for name, count in block.write_counts.items()
        if is_local(name) and name not in block.external_reads
    )
    if internal:
        return internal[0][0]

    any_written = _ranked(
        (name, count) for name, count in block.write_counts.items() if is_local(name)
    )
    if any_written:
        return any_written[0][0]

    if block.suggested_params:
        return block.suggested_params[0]
    return "step"


def _describe_block(block) -> str:
    if block.has_raise:
        return "validation"

    subject = _block_subject(block)
    if block.returned:
        return f"{subject} construction"
    if block.has_loop or block.outputs:
        return f"the {subject} calculation"
    return f"{subject} handling"


def _suggest_helper_name(block) -> str:
    """Heuristic helper name: a verb chosen from what the block does
    structurally, plus the subject from _block_subject().

      raises        -> _validate_<input>   (or _validate_input when it
                                            checks more than one thing)
      returns       -> _build_<subject>
      loops/produces-> _compute_<subject>
      otherwise     -> _apply_<subject>
    """
    if block.has_raise:
        params = block.suggested_params
        return f"_validate_{params[0]}" if len(params) == 1 else "_validate_input"

    subject = _block_subject(block)
    if block.returned:
        verb = "build"
    elif block.has_loop or block.outputs:
        verb = "compute"
    else:
        verb = "apply"
    return f"_{verb}_{subject}"


def _block_line_range(block) -> tuple[int, int]:
    """Report the block's span, trimming a trailing `return`.

    The return statement has to stay behind in the original method -- only
    the work that produces the value moves into the helper -- so including
    its line in the suggested range would be misleading.
    """
    start, end = block.start_line, block.end_line
    if len(block.infos) > MIN_BLOCK_STATEMENTS and isinstance(block.infos[-1].node, ast.Return):
        previous = block.infos[-2].node
        end = getattr(previous, "end_lineno", previous.lineno)
    return start, end


def generate_extract_method_suggestions(
    cls: ClassInfo, min_method_lines: int = LONG_METHOD_LINE_THRESHOLD
) -> list[dict]:
    """Propose Extract Method refactorings for a class's long methods.

    A method qualifies if it spans at least `min_method_lines` AND its
    statements break into two or more data-cohesive blocks. One block means
    the method is a single continuous step, and "extract the whole method"
    is not a refactoring -- those return nothing rather than a bogus split.
    """
    suggestions: list[dict] = []

    for method in cls.methods:
        line_count = method.end_line - method.start_line + 1
        if line_count < min_method_lines:
            continue

        blocks = split_into_blocks(method)
        if len(blocks) < 2:
            continue

        used_names: dict[str, int] = {}
        for block in blocks:
            base_name = _suggest_helper_name(block)
            used_names[base_name] = used_names.get(base_name, 0) + 1
            name = base_name if used_names[base_name] == 1 else f"{base_name}_{used_names[base_name]}"

            start, end = _block_line_range(block)
            description = _describe_block(block)

            suggestions.append(
                {
                    "type": "extract_method",
                    "source_class": cls.name,
                    "source_method": method.name,
                    "method_line_count": line_count,
                    "lines": [start, end],
                    "suggested_name": name,
                    "suggested_params": list(block.suggested_params),
                    "returns": list(block.outputs),
                    "description": description,
                    "rationale": (
                        f"Method {method.name} is {line_count} lines long. Consider "
                        f"extracting lines {start}-{end} (which handle {description}) "
                        f"into a helper method like {name}()."
                    ),
                }
            )

    return suggestions


# ---------------------------------------------------------------------------
# Move Method (Feature Envy)
# ---------------------------------------------------------------------------

# A method needs at least this many attributable calls into another class
# before we'll name that class. A single outbound call is too thin a basis
# for telling someone to relocate their code.
MIN_EXTERNAL_REFS_FOR_MOVE = 2


def _own_data_uses(method: MethodInfo, cls: ClassInfo) -> int:
    """How much a method uses its OWN class.

    Counts distinct self.<field> accesses plus distinct calls to sibling
    methods. Sibling calls are included deliberately: a method that leans
    heavily on its own class's behaviour (rather than its raw fields) is not
    envious, and counting fields alone would flag it as though it were.
    """
    own_method_names = {m.name for m in cls.methods}
    own_calls = {c for c in method.calls_made if c in own_method_names and c != method.name}
    return len(set(method.fields_accessed)) + len(own_calls)


def _attribute_external_calls(
    method: MethodInfo, cls: ClassInfo, all_classes: list[ClassInfo]
) -> tuple[Counter, list[str]]:
    """Attribute a method's outbound calls to the classes they land on.

    Uses the same name-matching heuristic as cbo/fan_out in engine.metrics:
    a call matches either another class's NAME (construction, e.g.
    `PaymentProcessor()`) or a method name that another class defines.

    Calls whose name is defined by two or more classes are NOT counted --
    attributing them to any single class would be a guess. They're returned
    separately so the caller can decide whether the ambiguity is big enough
    to undermine the winner.
    """
    other_class_names = {c.name for c in all_classes if c.name != cls.name}
    owners = method_owner_index(all_classes, cls.name)

    counts: Counter = Counter()
    ambiguous: list[str] = []

    for call_name in method.calls_made:
        if call_name in other_class_names:
            counts[call_name] += 1
        elif call_name in owners:
            owning = owners[call_name]
            if len(owning) == 1:
                counts[next(iter(owning))] += 1
            else:
                ambiguous.append(call_name)

    return counts, ambiguous


def _times(n: int) -> str:
    return "1 time" if n == 1 else f"{n} times"


# ---------------------------------------------------------------------------
# Receiver-based envy attribution (C.2)
#
# The call-name attribution above can only name a destination that exists in
# the scanned path -- it resolves a bare call name against the other classes
# it can see. That is why the pre-C.2 report on ORCIDOAuth2 could only say
# "the envied class may live outside the scanned path".
#
# Real foreign accesses carry their RECEIVER, so envy can be attributed to
# the specific object being read even when its class is nowhere in the scan.
# Two outcomes, in order of usefulness:
#   1. the receiver's attribute set matches exactly one visible class -> name
#      the class, same as before but reachable in more cases;
#   2. no class matches -> name the RECEIVER (a parameter or local), which
#      still tells a reader precisely what the method is fixated on.
# ---------------------------------------------------------------------------

# Below this share of a method's foreign reads landing on one receiver, the
# reads are scattered and no single destination is defensible.
MIN_RECEIVER_CONCENTRATION = 0.6


def _receiver_attribute_sets(method: MethodInfo) -> dict[str, set[str]]:
    per_receiver: dict[str, set[str]] = {}
    for receiver, attribute in method.foreign_accesses:
        per_receiver.setdefault(receiver, set()).add(attribute)
    return per_receiver


def _resolve_receiver_class(attributes: set[str], cls: ClassInfo,
                            all_classes: list[ClassInfo]) -> str | None:
    """Name the class whose interface covers every attribute read off a receiver.

    Exactly one match is required. Zero means the provider is not in the scan
    (common, and handled by naming the receiver instead); more than one means
    the attribute names are too generic to attribute safely, which is the
    same ambiguity the call-name path already refuses to guess through.
    """
    if not attributes:
        return None

    matches = [
        other.name
        for other in all_classes
        if other.name != cls.name
        and attributes <= ({m.name for m in other.methods} | set(other.fields))
    ]
    return matches[0] if len(matches) == 1 else None


def _receiver_based_suggestion(
    method: MethodInfo, cls: ClassInfo, all_classes: list[ClassInfo]
) -> dict | None:
    """A move_method built from foreign attribute reads, or None."""
    per_receiver = _receiver_attribute_sets(method)
    if not per_receiver:
        return None

    counts = Counter({recv: len(attrs) for recv, attrs in per_receiver.items()})
    total = sum(counts.values())
    receiver, top_count = counts.most_common(1)[0]
    concentration = top_count / total

    if concentration < MIN_RECEIVER_CONCENTRATION:
        return _no_clear_target_note(
            cls,
            method,
            f"its {total} outside reads are spread across {len(counts)} different objects "
            f"({int(round(concentration * 100))}% on the largest), so no single one owns them",
            candidates=sorted(counts),
        )

    own_uses = _own_data_uses(method, cls)
    if top_count <= own_uses:
        return None
    if top_count < MIN_EXTERNAL_REFS_FOR_MOVE:
        return None

    target_class = _resolve_receiver_class(per_receiver[receiver], cls, all_classes)
    where = target_class or f"whatever '{receiver}' holds"

    return {
        "type": "move_method",
        "source_class": cls.name,
        "source_method": method.name,
        "target_class": target_class,
        "target_receiver": receiver,
        "external_references": top_count,
        "own_data_uses": own_uses,
        "envy_ratio": round(top_count / own_uses, 2) if own_uses else None,
        "concentration": round(concentration, 2),
        "attributes_read": sorted(per_receiver[receiver]),
        "rationale": (
            f"Method '{method.name}' reads {top_count} distinct thing"
            f"{'' if top_count == 1 else 's'} off '{receiver}' "
            f"({int(round(concentration * 100))}% of everything it reads outside itself) "
            f"but touches its own class's data {_times(own_uses)}. "
            f"Consider moving '{method.name}' into {where}."
            + (
                ""
                if target_class
                else f" The class behind '{receiver}' is not in the scanned path, so it is "
                "named by the variable rather than by class."
            )
        ),
    }


def _no_clear_target_note(cls: ClassInfo, method: MethodInfo, reason: str, candidates: list[str]) -> dict:
    return {
        "type": "no_clear_envy_target",
        "source_class": cls.name,
        "source_method": method.name,
        "candidates": candidates,
        "note": (
            f"Method '{method.name}' reaches outside {cls.name}, but {reason}. "
            "Naming a destination here would be a guess -- worth a manual look."
        ),
    }


def _move_method_suggestion_for(
    method: MethodInfo, cls: ClassInfo, all_classes: list[ClassInfo]
) -> dict | None:
    counts, ambiguous = _attribute_external_calls(method, cls, all_classes)

    if not counts:
        # Nothing resolved by call name. Real foreign accesses can still
        # attribute the envy -- and this is exactly the case that used to
        # produce "the envied class may live outside the scanned path".
        by_receiver = _receiver_based_suggestion(method, cls, all_classes)
        if by_receiver is not None:
            return by_receiver
        if ambiguous:
            return _no_clear_target_note(
                cls,
                method,
                f"the methods it calls ({', '.join(sorted(set(ambiguous)))}) are defined on "
                "more than one class, so the calls can't be attributed to a single one",
                candidates=[],
            )
        return None

    ranked = counts.most_common()
    target, top_count = ranked[0]
    runner_up_count = ranked[1][1] if len(ranked) > 1 else 0
    own_uses = _own_data_uses(method, cls)

    # Not actually envious: it leans on its own class at least as much as on
    # any other. The class-level ML label doesn't mean every method is guilty.
    if top_count <= own_uses:
        return None

    if top_count < MIN_EXTERNAL_REFS_FOR_MOVE:
        return None

    if top_count == runner_up_count:
        tied = [name for name, count in ranked if count == top_count]
        return _no_clear_target_note(
            cls,
            method,
            f"its calls are split evenly between {' and '.join(sorted(tied))}",
            candidates=sorted(tied),
        )

    # Ambiguous calls could close the gap between first and second place, so
    # the winner isn't safe to name.
    if ambiguous and len(ambiguous) >= (top_count - runner_up_count):
        return _no_clear_target_note(
            cls,
            method,
            f"{len(ambiguous)} of its calls are defined on more than one class, which is "
            f"enough to change which class comes out on top",
            candidates=sorted(counts),
        )

    return {
        "type": "move_method",
        "source_class": cls.name,
        "source_method": method.name,
        "target_class": target,
        "external_references": top_count,
        "own_data_uses": own_uses,
        "envy_ratio": round(top_count / own_uses, 2) if own_uses else None,
        "rationale": (
            f"Method '{method.name}' calls into {target} {_times(top_count)} but only "
            f"accesses its own class's data {_times(own_uses)}. "
            f"Consider moving '{method.name}' into {target}."
        ),
    }


def generate_move_method_suggestions(
    cls: ClassInfo, all_classes: list[ClassInfo]
) -> list[dict]:
    """Propose Move Method refactorings for a class flagged Feature Envy.

    Returns one entry per envious method: a "move_method" naming the class to
    move it into, or a "no_clear_envy_target" note when the coupling data
    doesn't single one out. Methods that aren't actually envious produce
    nothing -- a class-level Feature Envy label doesn't make every method in
    it guilty.
    """
    suggestions = []
    for method in cls.methods:
        if method.name in CONSTRUCTOR_NAMES:
            continue
        suggestion = _move_method_suggestion_for(method, cls, all_classes)
        if suggestion is not None:
            suggestions.append(suggestion)
    return suggestions
