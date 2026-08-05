"""The reasoning behind a class's result, assembled into one record.

`analyze_path()` answers *what* the pipeline concluded. This module answers
*why*, by re-running the same three stages and keeping the working -- the
metrics that were measured, the model's local attribution (engine.ml.explain),
the method graph and its clusters, and the suggestion those produced -- rather
than only their conclusion.

It contains no rendering. Everything here is plain data plus plain-language
strings, so the terminal summary and the HTML report can present the identical
reasoning at different levels of detail without either of them re-deriving it.

WRITING RULE FOR EVERYTHING IN THIS FILE: the sentence comes first and the
number comes second. A reader who has never heard of LCOM should be able to
follow every finding here; the raw metric exists to let a reader who *has*
heard of it check the claim. That ordering is deliberate and matches the
--audience simple report -- see engine/cli/explanations.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from engine.cli.explanations import SMELL_EXPLANATIONS
from engine.metrics import compute_all_metrics
from engine.ml.explain import explain_prediction
from engine.models import ClassInfo
from engine.parser import parse_file
from engine.pipeline import analyze_file
from engine.suggester.cluster import CONSTRUCTOR_NAMES, analyze_extraction_clusters
from engine.suggester.graph import build_method_graph

# Above/below this many standard deviations from the training mean, a metric
# is described as notable rather than ordinary. 0.5 sigma is deliberately
# loose: this threshold only picks which *sentence* to print, it never feeds
# back into the prediction.
NOTABLE_Z = 0.5


# ---------------------------------------------------------------------------
# Plain-language vocabulary for the eight metrics
# ---------------------------------------------------------------------------

def _fmt(value: float, places: int = 0) -> str:
    return f"{value:.{places}f}"


_METRIC_LANGUAGE = {
    "lcom": {
        "plain_name": "How much its methods have in common",
        "what_it_is": (
            "Whether the methods in this class all work on the same data, or each "
            "go off and touch their own separate things."
        ),
        "high": (
            "This class mixes several unrelated jobs -- most pairs of its methods "
            "have no data in common with each other."
        ),
        "typical": (
            "Some of its methods share data with each other and some don't, which "
            "is an ordinary mix."
        ),
        "low": (
            "Its methods consistently work on the same data, which is what a "
            "focused, single-purpose class looks like."
        ),
        "detail": lambda v, t: (
            f"LCOM {_fmt(v, 2)} on a 0-1 scale, where 0 means every pair of methods "
            f"shares data and 1 means no pair does. Typical class in the model's "
            f"training data: {_fmt(t, 2)}."
        ),
    },
    "class_length": {
        "plain_name": "How big the class is",
        "what_it_is": "How many lines the class body spans, top to bottom.",
        "high": "The class is long -- a lot of code lives in this one place.",
        "typical": "The class is an ordinary size.",
        "low": "The class is short.",
        "detail": lambda v, t: (
            f"{_fmt(v)} lines. Typical class in the model's training data: {_fmt(t)} lines."
        ),
    },
    "avg_method_length": {
        "plain_name": "How long its methods are",
        "what_it_is": "The average number of lines in each of its methods.",
        "high": "Its methods are long -- each one does a lot of work before it finishes.",
        "typical": "Its methods are an ordinary length.",
        "low": "Its methods are short -- mostly a line or two each.",
        "detail": lambda v, t: (
            f"{_fmt(v, 1)} lines per method on average. Typical: {_fmt(t, 1)} lines."
        ),
    },
    "avg_cyclomatic_complexity": {
        "plain_name": "How much decision-making its methods do",
        "what_it_is": (
            "How many decision points -- ifs, loops, and/or conditions -- its methods "
            "contain. Roughly: how many different paths the code can take."
        ),
        "high": (
            "Its methods contain a lot of branching, so there are many different "
            "routes through the code and many cases to test."
        ),
        "typical": "Its methods branch about as much as most methods do.",
        "low": "Its methods are mostly straight-line code with few decisions in them.",
        "detail": lambda v, t: (
            f"{_fmt(v, 1)} decision points per method on average. Typical: {_fmt(t, 1)}."
        ),
    },
    "fan_out": {
        "plain_name": "How much it reaches into other classes",
        "what_it_is": (
            "How often this class calls out to other classes instead of doing the "
            "work itself."
        ),
        "high": (
            "This class leans heavily on other classes -- a lot of what it does is "
            "really driving code that lives somewhere else."
        ),
        "typical": "It uses other classes about as much as most classes do.",
        "low": "It mostly keeps to itself rather than reaching into other classes.",
        "detail": lambda v, t: (
            f"{_fmt(v)} distinct references out to other classes in this file. "
            f"Typical: {_fmt(t, 1)}."
        ),
    },
    "cbo": {
        "plain_name": "How many other classes it depends on",
        "what_it_is": "The number of different other classes this one uses.",
        "unused": lambda v: f"It depends on {_fmt(v)} other class{'' if v == 1 else 'es'}.",
        "detail": lambda v, t: f"CBO {_fmt(v)}.",
    },
    "fan_in": {
        "plain_name": "How many other classes rely on it",
        "what_it_is": "The number of other classes that call into this one.",
        "unused": lambda v: (
            f"{_fmt(v)} other class{' uses' if v == 1 else 'es use'} it."
        ),
        "detail": lambda v, t: f"Fan-in {_fmt(v)}.",
    },
    "dit": {
        "plain_name": "How deep its inheritance goes",
        "what_it_is": "How many parent classes sit above this one in the inheritance chain.",
        "unused": lambda v: (
            "It doesn't inherit from anything in this file."
            if v == 0
            else f"It sits {_fmt(v)} level{'' if v == 1 else 's'} down an inheritance chain."
        ),
        "detail": lambda v, t: f"Depth of inheritance {_fmt(v)}.",
    },
}

_UNUSED_FEATURE_NOTE = (
    "The model's training data contained no variation in this measurement at all, "
    "so the model never learned to use it and it played no part in the decision. "
    "It's shown here because it still describes the class."
)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass
class Measurement:
    """One metric, described in words first and numbers second."""

    metric: str
    plain_name: str
    what_it_is: str
    finding: str
    detail: str
    value: float
    typical: float | None
    level: str  # "high" | "typical" | "low" | "unused"
    used_by_model: bool


@dataclass
class Factor:
    """One metric's measured pull on THIS class's prediction."""

    metric: str
    plain_name: str
    finding: str
    detail: str
    level: str  # "high" | "typical" | "low"
    support: float  # confidence lost if this measurement were unremarkable
    importance: float  # the model's global weight on the metric
    direction: str  # "for" | "against" | "neutral"
    sentence: str  # the whole thing, as one plain-language claim


@dataclass
class ModelReasoning:
    decided_by_model: bool
    model_label: str
    model_confidence: float
    single_factor: bool
    factors: list[Factor] = field(default_factory=list)
    unused: list[Measurement] = field(default_factory=list)
    # Set when the printed label did NOT come from the classifier -- the
    # threshold and similarity checks in the pipeline can overrule a Clean.
    non_model_reason: str | None = None


@dataclass
class GraphReasoning:
    """The method graph and whatever was done with it.

    `strategy` names which analysis actually produced the suggestion, because
    the graph is built for every class but only *clustered* for the cohesion
    smells -- claiming otherwise in the report would be a lie about the
    pipeline.
    """

    strategy: str  # "clusters" | "calls" | "blocks" | "structure"
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    clusters: list[dict] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    unclustered: list[str] = field(default_factory=list)
    note: str = ""
    call_attribution: list[dict] = field(default_factory=list)
    blocks: list[dict] = field(default_factory=list)
    how_built: str = ""


@dataclass
class ClassReasoning:
    name: str
    file_path: str
    smell: str
    confidence: float
    headline_metric: str
    is_flagged: bool
    what_the_smell_is: str
    top_reason: str
    measurements: list[Measurement]
    model: ModelReasoning
    graph: GraphReasoning | None
    suggestion_sentences: list[str]
    short_suggestion: str
    suggestions: list[dict]


@dataclass
class FileReasoning:
    path: str
    classes: list[ClassReasoning]
    total_classes: int
    flagged_count: int


class ClassNotFoundError(LookupError):
    """Raised when --class names something the file doesn't define."""

    def __init__(self, class_name: str, available: list[str]):
        self.class_name = class_name
        self.available = available
        super().__init__(class_name)


# ---------------------------------------------------------------------------
# Measurements
# ---------------------------------------------------------------------------


def _level_for(z_score: float) -> str:
    if z_score >= NOTABLE_Z:
        return "high"
    if z_score <= -NOTABLE_Z:
        return "low"
    return "typical"


def _measurement(contribution: dict) -> Measurement:
    metric = contribution["feature"]
    language = _METRIC_LANGUAGE[metric]
    value = contribution["value"]
    typical = contribution["typical"]

    if not contribution["used_by_model"]:
        return Measurement(
            metric=metric,
            plain_name=language["plain_name"],
            what_it_is=language["what_it_is"],
            finding=language["unused"](value),
            detail=language["detail"](value, typical),
            value=value,
            typical=None,
            level="unused",
            used_by_model=False,
        )

    level = _level_for(contribution["z_score"])
    return Measurement(
        metric=metric,
        plain_name=language["plain_name"],
        what_it_is=language["what_it_is"],
        finding=language[level],
        detail=language["detail"](value, typical),
        value=value,
        typical=typical,
        level=level,
        used_by_model=True,
    )


# ---------------------------------------------------------------------------
# Model reasoning
# ---------------------------------------------------------------------------


def _factor_sentence(measurement: Measurement, contribution: dict, label: str, rank: int) -> str:
    """One factor, stated as a claim a non-specialist can check.

    The measurement leads; what it did to the prediction follows. A factor
    that pulled *against* the printed label is said so outright -- it is the
    single most useful thing in a low-confidence explanation, and hiding it
    would make the report a summary of the answer rather than of the
    reasoning.
    """
    support = contribution["support"]
    percent = abs(support) * 100

    if support > 0:
        lead = "the strongest single factor" if rank == 0 else "a supporting factor"
        return (
            f"{measurement.finding} This was {lead} behind the {label} label: if this "
            f"one measurement were ordinary instead, the model's confidence in {label} "
            f"would fall by about {percent:.0f} points."
        )
    if support < 0:
        return (
            f"{measurement.finding} This measurement argued against {label} rather than for "
            f"it -- on its own it points somewhere else, and the model reached {label} in "
            f"spite of it. That is part of why the confidence isn't higher."
        )
    return (
        f"{measurement.finding} On its own this made no difference to the outcome -- "
        f"the model's answer is the same with or without it."
    )


def _direction(support: float) -> str:
    if support > 0:
        return "for"
    if support < 0:
        return "against"
    return "neutral"


def _non_model_reason(smell: str, model_label: str, suggestions: list[dict]) -> str:
    """Why the printed label isn't the classifier's label.

    Two checks in the pipeline sit outside the model entirely (see
    engine/pipeline.py): a parameter-count threshold and a structural
    similarity comparison. When either fires on a class the model called
    Clean, the model is not the thing that needs explaining -- saying so is
    the whole point of this command.
    """
    if smell == "Long Parameter List":
        methods = [m for s in suggestions if s["type"] == "long_parameter_list" for m in s["methods"]]
        listed = ", ".join(f"{m}()" for m in methods)
        takes = "takes" if len(methods) == 1 else "take"
        return (
            f"The classifier is not what flagged this class -- it looked at the eight "
            f"measurements below and called the class {model_label}. The finding comes "
            f"from a plain counting rule applied afterwards: {listed} {takes} five or more "
            f"parameters. There is no model opinion involved and no confidence score to "
            f"report -- a parameter count is either over the line or it isn't."
        )
    if smell == "Duplicate Code":
        pairs = [p for s in suggestions if s["type"] == "duplicate_code" for p in s["pairs"]]
        best = max((p["similarity"] for p in pairs), default=0.0)
        return (
            f"The classifier is not what flagged this class -- it looked at the eight "
            f"measurements below and called the class {model_label}. The finding comes "
            f"from a separate structural comparison run afterwards, which strips every "
            f"variable name out of each method and checks whether what's left has the "
            f"same shape. The closest pair here is {best:.0%} alike. That number is a "
            f"similarity score, not a confidence -- and similar shape is not proof of "
            f"duplicated meaning."
        )
    return (
        f"The printed label came from a check outside the classifier; the classifier "
        f"itself called this class {model_label}."
    )


def _model_reasoning(metrics: dict, smell: str, suggestions: list[dict]) -> tuple[ModelReasoning, list[Measurement]]:
    attribution = explain_prediction(metrics)
    model_label = attribution["label"]

    measurements = [_measurement(c) for c in attribution["contributions"]]
    by_metric = {m.metric: m for m in measurements}

    factors: list[Factor] = []
    used = [c for c in attribution["contributions"] if c["used_by_model"]]
    for rank, contribution in enumerate(used):
        measurement = by_metric[contribution["feature"]]
        factors.append(
            Factor(
                metric=measurement.metric,
                plain_name=measurement.plain_name,
                finding=measurement.finding,
                detail=measurement.detail,
                level=measurement.level,
                support=contribution["support"],
                importance=contribution["importance"],
                direction=_direction(contribution["support"]),
                sentence=_factor_sentence(measurement, contribution, model_label, rank),
            )
        )

    decided_by_model = smell == model_label
    return (
        ModelReasoning(
            decided_by_model=decided_by_model,
            model_label=model_label,
            model_confidence=attribution["confidence"],
            single_factor=attribution["single_factor"],
            factors=factors,
            unused=[m for m in measurements if not m.used_by_model],
            non_model_reason=(
                None if decided_by_model else _non_model_reason(smell, model_label, suggestions)
            ),
        ),
        measurements,
    )


# ---------------------------------------------------------------------------
# Graph reasoning
# ---------------------------------------------------------------------------

_HOW_BUILT = (
    "Every method in the class becomes a dot. Two dots are joined by a solid line "
    "when both methods touch the same piece of the class's data (self.something) -- "
    "the thicker the line, the more data they have in common. A dashed line means "
    "one method simply calls the other. Nothing here reads what the code means; it "
    "only tracks which methods touch which data."
)


def _graph_nodes_and_edges(cls: ClassInfo) -> tuple[list[dict], list[dict]]:
    graph = build_method_graph(cls)
    fields_by_method = {m.name: set(m.fields_accessed) for m in cls.methods}

    nodes = [
        {
            "name": m.name,
            "fields": sorted(fields_by_method[m.name]),
            "is_constructor": m.name in CONSTRUCTOR_NAMES,
        }
        for m in cls.methods
    ]

    edges = []
    for source, target, data in graph.edges(data=True):
        edge_types = data.get("edge_type", set())
        shared = sorted(fields_by_method.get(source, set()) & fields_by_method.get(target, set()))
        edges.append(
            {
                "source": source,
                "target": target,
                "shared_fields": shared,
                "weight": data.get("weight", 0),
                "shares_fields": "shares_fields" in edge_types,
                "calls": "calls" in edge_types,
            }
        )
    return nodes, edges


def _cluster_records(cls: ClassInfo, suggestions: list[dict]) -> tuple[list[dict], list[str], list[str], str]:
    analysis = analyze_extraction_clusters(build_method_graph(cls))
    by_methods = {
        frozenset(s["methods_to_extract"]): s for s in suggestions if s["type"] == "extract_class"
    }

    clusters = []
    for cluster in analysis.clusters:
        suggestion = by_methods.get(frozenset(cluster))
        shared = suggestion["shared_fields"] if suggestion else []
        clusters.append(
            {
                "methods": sorted(cluster),
                "shared_fields": shared,
                "suggested_name": suggestion["suggested_name"] if suggestion else None,
                "why": (
                    f"These {len(cluster)} methods were grouped together because they all "
                    f"work on the same data ({', '.join(shared)}), and the grouping step "
                    f"found more data shared inside this group than between it and the "
                    f"rest of the class."
                    if shared
                    else (
                        "These methods were grouped together by the shape of the graph, but "
                        "they share no common data -- treat this group as a weak suggestion."
                    )
                ),
            }
        )
    return clusters, sorted(analysis.excluded_methods), sorted(analysis.unclustered_methods), analysis.note


def _graph_reasoning(cls: ClassInfo, smell: str, suggestions: list[dict]) -> GraphReasoning:
    nodes, edges = _graph_nodes_and_edges(cls)
    kinds = {s["type"] for s in suggestions}

    if "extract_class" in kinds:
        clusters, excluded, unclustered, note = _cluster_records(cls, suggestions)
        return GraphReasoning(
            strategy="clusters",
            nodes=nodes,
            edges=edges,
            clusters=clusters,
            excluded=excluded,
            unclustered=unclustered,
            note=note,
            how_built=_HOW_BUILT,
        )

    if kinds & {"move_method", "no_clear_envy_target"}:
        attribution = [
            {
                "method": s["source_method"],
                "target": s.get("target_class"),
                "external_references": s.get("external_references"),
                "own_data_uses": s.get("own_data_uses"),
                "note": s.get("note"),
            }
            for s in suggestions
            if s["type"] in ("move_method", "no_clear_envy_target")
        ]
        return GraphReasoning(
            strategy="calls",
            nodes=nodes,
            edges=edges,
            call_attribution=attribution,
            note=(
                "For a class that reaches outward, rather than one with too much packed "
                "inside it, the useful question isn't which methods belong together -- it's "
                "where each method's calls actually land. Those counts are below, and they "
                "are what the suggestion is built from."
            ),
            how_built=_HOW_BUILT,
        )

    if "extract_method" in kinds:
        blocks = [
            {
                "method": s["source_method"],
                "method_line_count": s["method_line_count"],
                "lines": s["lines"],
                "suggested_name": s["suggested_name"],
                "description": s["description"],
                "params": s["suggested_params"],
            }
            for s in suggestions
            if s["type"] == "extract_method"
        ]
        return GraphReasoning(
            strategy="blocks",
            nodes=nodes,
            edges=edges,
            blocks=blocks,
            note=(
                "The problem here sits inside a single method rather than across the class, "
                "so the deciding step was a different one: the method's own statements were "
                "split into runs that hand data to each other, and each run became a "
                "candidate helper. Those runs are below."
            ),
            how_built=_HOW_BUILT,
        )

    clusters, excluded, unclustered, note = _cluster_records(cls, suggestions)
    return GraphReasoning(
        strategy="structure",
        nodes=nodes,
        edges=edges,
        clusters=clusters,
        excluded=excluded,
        unclustered=unclustered,
        note=note,
        how_built=_HOW_BUILT,
    )


# ---------------------------------------------------------------------------
# The suggestion, restated
# ---------------------------------------------------------------------------


def _and_list(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def suggestion_sentences(entry: dict) -> list[str]:
    """The suggestions for one class, as plain sentences.

    Deliberately not shared with engine/cli/render.py: that builds rich markup
    for a terminal panel, this builds bare strings that have to survive being
    dropped into HTML. Same findings, different medium.
    """
    sentences: list[str] = []

    for s in entry["suggestions"]:
        kind = s["type"]

        if kind == "extract_class":
            methods = _and_list([f"{m}()" for m in s["methods_to_extract"]])
            fields = _and_list(s["shared_fields"])
            sentences.append(
                f"Move {methods} out into a new class of their own -- something like "
                f"{s['suggested_name']}. They are the methods that work on {fields}, and "
                f"pulling them out leaves both halves smaller and easier to reason about."
                if fields
                else f"Move {methods} out into a new class of their own -- something like "
                f"{s['suggested_name']}."
            )
        elif kind == "move_method":
            sentences.append(
                f"Move {s['source_method']}() into {s['target_class']}. It uses "
                f"{s['target_class']}'s behaviour {s['external_references']} times but its "
                f"own class's data only {s['own_data_uses']} time"
                f"{'' if s['own_data_uses'] == 1 else 's'} -- it is really "
                f"{s['target_class']}'s work being done in the wrong place."
            )
        elif kind == "no_clear_envy_target":
            sentences.append(s["note"])
        elif kind == "extract_method":
            params = ", ".join(s["suggested_params"])
            sentences.append(
                f"Inside {s['source_method']}() ({s['method_line_count']} lines), lines "
                f"{s['lines'][0]}-{s['lines'][1]} handle {s['description']} and nothing else. "
                f"Lift them into their own small method -- {s['suggested_name']}({params}) -- "
                f"so the original method reads as a list of steps."
            )
        elif kind == "long_parameter_list":
            methods = _and_list([f"{m}()" for m in s["methods"]])
            sentences.append(
                f"{methods} take five or more parameters. Parameters that always travel "
                f"together are usually one idea that hasn't been given a name yet -- group "
                f"them into a single object and pass that instead."
            )
        elif kind == "duplicate_code":
            for pair in s["pairs"]:
                a, b = pair["methods"]
                sentences.append(
                    f"{a}() and {b}() are written almost identically -- {pair['similarity']:.0%} "
                    f"the same shape once the variable names inside them are ignored. Read both "
                    f"before merging them: matching shape is not proof they do the same job."
                )
        elif kind == "unsupported_smell_type":
            sentences.append(s["note"])

    if not sentences:
        sentences.append(
            "No concrete change is being suggested. The measurements flagged this class, but "
            "its methods don't separate cleanly along the data they use, so any split would be "
            "an arbitrary one -- this is a place to look, not a place to follow instructions."
        )
    return sentences


def short_suggestion(entry: dict) -> str:
    """The same finding as suggestion_sentences()[0], compressed to one line.

    The terminal summary has a hard three-line-per-class budget and the full
    sentences above run to two or three wrapped lines each. This is the only
    reason both exist: same finding, one written to be read on a screen full
    of other classes and one written to be read on its own.
    """
    suggestions = entry["suggestions"]

    def of_type(kind):
        return [s for s in suggestions if s["type"] == kind]

    extract = of_type("extract_class")
    if extract:
        first = extract[0]
        more = f" (+{len(extract) - 1} more group{'' if len(extract) == 2 else 's'})" if len(extract) > 1 else ""
        count = len(first["methods_to_extract"])
        return f"extract {count} method{'' if count == 1 else 's'} into {first['suggested_name']}{more}"

    moves = of_type("move_method")
    if moves:
        more = f" (+{len(moves) - 1} more)" if len(moves) > 1 else ""
        return f"move {moves[0]['source_method']}() into {moves[0]['target_class']}{more}"

    methods = of_type("extract_method")
    if methods:
        by_method = {s["source_method"] for s in methods}
        name = sorted(by_method)[0]
        parts = sum(1 for s in methods if s["source_method"] == name)
        more = f" (+{len(by_method) - 1} more method{'' if len(by_method) == 2 else 's'})" if len(by_method) > 1 else ""
        return f"break {name}() into {parts} smaller methods{more}"

    duplicates = of_type("duplicate_code")
    if duplicates:
        pair = duplicates[0]["pairs"][0]
        a, b = pair["methods"]
        return f"read {a}() and {b}() together -- {pair['similarity']:.0%} the same shape"

    long_params = of_type("long_parameter_list")
    if long_params:
        listed = ", ".join(f"{m}()" for m in long_params[0]["methods"])
        return f"group the parameters of {listed} into one object"

    if of_type("no_clear_envy_target"):
        return "reaches outside the class, but no single destination stands out -- needs a manual look"

    if of_type("unsupported_smell_type"):
        return "flagged, but no clean split point was found -- needs a manual look"

    return "no clean split found along the data its methods use -- worth a manual look"


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _headline_metric(smell: str, confidence: float) -> str:
    """What the number next to the label actually is.

    Only the classifier produces a confidence. Calling a similarity score or a
    threshold check "confidence" would attach a claim of certainty to the two
    findings that have the least of it -- the same reasoning as
    _dev_headline_metric() in engine/cli/render.py.
    """
    if smell == "Duplicate Code":
        return f"{confidence:.0%} structural match"
    if smell == "Long Parameter List":
        return "rule-based check, no model score"
    return f"{confidence:.0%} confidence"


def _top_reason(model: ModelReasoning, smell: str) -> str:
    if not model.decided_by_model:
        if smell == "Long Parameter List":
            return "a plain parameter count, not the classifier -- one of its methods takes five or more."
        if smell == "Duplicate Code":
            return (
                "two of its methods are written in almost exactly the same shape, which a "
                "similarity check spotted -- the classifier itself saw nothing wrong."
            )
        return model.non_model_reason or "a check outside the classifier."

    if not model.factors:
        return "no single measurement stood out; the label came from the combination of all of them."

    top = model.factors[0]
    if not model.single_factor:
        return f"no single measurement decided this. The closest is: {top.finding}"
    if top.direction == "against":
        return f"{top.finding} (though this one pointed the other way -- a genuinely borderline call.)"
    # A high reading supporting a smell explains itself; a low or ordinary one
    # supporting it does not, and left bare it reads as a non sequitur. Only
    # the surprising case gets the extra clause.
    if top.level in ("low", "typical"):
        return f"{top.finding} That is the pattern the model most associates with {smell}."
    return top.finding


_CLEAN_VERDICT = (
    "Nothing to change here. The measurements came back unremarkable -- the class is a "
    "reasonable size, its methods work on shared data rather than going their own separate "
    "ways, and it isn't unusually complex or tangled up with other classes."
)


def _reasoning_for_class(cls: ClassInfo, entry: dict) -> ClassReasoning:
    smell = entry["predicted_smell"]
    suggestions = entry["suggestions"]

    model, measurements = _model_reasoning(entry["metrics"], smell, suggestions)
    graph = _graph_reasoning(cls, smell, suggestions) if cls.methods else None

    # Only reachable via --class: a Clean class has no finding to justify, so
    # "no clean split was found" would be answering a question nobody asked.
    if smell == "Clean":
        return ClassReasoning(
            name=cls.name,
            file_path=entry["file_path"],
            smell=smell,
            confidence=entry["confidence"],
            headline_metric=_headline_metric(smell, entry["confidence"]),
            is_flagged=False,
            what_the_smell_is=SMELL_EXPLANATIONS["Clean"]["what"],
            top_reason=_top_reason(model, smell),
            measurements=measurements,
            model=model,
            graph=graph,
            suggestion_sentences=[_CLEAN_VERDICT],
            short_suggestion="nothing to change -- no structural red flags in this one",
            suggestions=suggestions,
        )

    return ClassReasoning(
        name=cls.name,
        file_path=entry["file_path"],
        smell=smell,
        confidence=entry["confidence"],
        headline_metric=_headline_metric(smell, entry["confidence"]),
        is_flagged=smell != "Clean",
        what_the_smell_is=SMELL_EXPLANATIONS.get(smell, {}).get("what", ""),
        top_reason=_top_reason(model, smell),
        measurements=measurements,
        model=model,
        graph=graph,
        suggestion_sentences=suggestion_sentences(entry),
        short_suggestion=short_suggestion(entry),
        suggestions=suggestions,
    )


def explain_file(
    path: str, class_name: str | None = None, clean_fallback: bool = False
) -> FileReasoning:
    """Build the reasoning record for every class worth explaining in `path`.

    Without `class_name` the scope is the flagged classes -- a Clean class has
    no finding to justify. With `class_name` the scope is exactly that class,
    flagged or not: someone who names a class is asking about that class, and
    "why is this one fine?" is a fair question.

    `clean_fallback` widens the scope to every class when nothing is flagged.
    The CLI sets it when writing to a file, so that a command asked to produce
    a report produces one even on a clean file -- a scripted step that writes
    nothing on success is a broken step.

    Raises ClassNotFoundError if `class_name` isn't defined in the file.
    """
    analysis = analyze_file(path)
    results = analysis["classes"]

    # Re-parsed rather than threaded out of analyze_file(): the pipeline's
    # contract is metrics-and-findings, and widening it to hand back AST-backed
    # ClassInfo objects would make every consumer pay for this command's needs.
    classes_by_name = {cls.name: cls for cls in parse_file(path)}

    if class_name is not None:
        if class_name not in classes_by_name:
            raise ClassNotFoundError(class_name, sorted(classes_by_name))
        selected = [class_name] if class_name in results else []
    else:
        selected = [name for name, entry in results.items() if entry["predicted_smell"] != "Clean"]
        if not selected and clean_fallback:
            selected = list(results)

    ordered = [name for name in classes_by_name if name in selected]
    reasoning = [_reasoning_for_class(classes_by_name[name], results[name]) for name in ordered]

    return FileReasoning(
        path=os.path.abspath(path),
        classes=reasoning,
        total_classes=len(results),
        flagged_count=sum(1 for e in results.values() if e["predicted_smell"] != "Clean"),
    )
