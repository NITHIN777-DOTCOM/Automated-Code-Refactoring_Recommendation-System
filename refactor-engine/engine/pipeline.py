"""End-to-end wiring: parse a repo, compute metrics, classify each class's
smell, and propose extraction suggestions for anything non-Clean (or an
explicit unsupported_smell_type suggestion for smells Phase 3 has no
strategy for yet).

Phase 1 (engine.parser, engine.metrics) -> Phase 2 (engine.ml.predict) ->
Phase 3 (engine.suggester). This module contains no analysis logic of its
own -- it only sequences the three phases and shapes their combined output.

analyze_repo()/analyze_file()/analyze_path() all return
{"classes": {class_name: {...}}, "unused_imports": {file_path: [...]}}.
Unused imports are a separate, pure-static (non-ML) check and are file-
scoped rather than class-scoped, so they live in a sibling top-level key
instead of inside any class's entry -- a file can have unused imports with
zero classes in it at all.
"""

from __future__ import annotations

import logging
import os

from engine.duplication import DEFAULT_SIMILARITY_THRESHOLD, find_duplicate_pairs
from engine.metrics import compute_all_metrics, parameter_count
from engine.ml.predict import predict_smell_with_confidence
from engine.parser import (
    find_unused_imports_detailed,
    iter_python_files,
    parse_file,
    parse_repo,
)
from engine.suggester import (
    suggest_method_extractions,
    suggest_method_moves,
    suggest_refactoring,
)

logger = logging.getLogger(__name__)

# Long Parameter List is NOT part of the trained classifier -- it's a plain
# threshold check layered on top of the ML result. Forcing a 6th label into
# the 5-class Random Forest would mean retraining on synthetic examples for
# yet another smell; a parameter count is unambiguous and doesn't need a
# model's opinion. This is simpler and more honest than pretending a
# threshold check needs machine learning.
LONG_PARAMETER_LIST_THRESHOLD = 5

_LONG_PARAMETER_LIST_NOTE = (
    "Long Parameter List detected — consider grouping related parameters into "
    "a single object/dataclass instead of passing them individually."
)

# Also not part of the trained classifier, and for a second reason on top of
# the one above: this check's own result is a heuristic. See engine.duplication
# -- it compares AST shape with names stripped, so it finds renamed copies but
# also flags methods that merely follow the same template. The note below is
# worded to say that outright, because a reader who takes a 0.9 here as
# "these are redundant" will delete working code.
_DUPLICATE_CODE_NOTE = (
    "Duplicate Code detected — these method pairs have near-identical structure "
    "once variable names are stripped. This is a structural similarity heuristic, "
    "not exact-text duplication: methods that merely follow the same shape (a run "
    "of parallel validators, several methods that each loop and accumulate) score "
    "high without being redundant. Read the pair before consolidating anything."
)

# Smells rooted in low cohesion get an Extract Class split, proposed by
# field-sharing clustering over the class's methods.
_COHESION_SMELL_TYPES = {"God Class", "Data Class"}

# Every smell the classifier emits now has a concrete strategy, so these
# notes are no longer "not implemented" placeholders -- each one is the
# fallback for a class where the strategy ran and found nothing conclusive.
_UNSUPPORTED_SMELL_NOTES = {
    "Feature Envy": (
        "Feature Envy detected, but this class's outbound calls don't concentrate "
        "on any one other class in the scan — there's no single destination to "
        "move a method into. The envied class may live outside the scanned path."
    ),
    "Long Method": (
        "Long Method detected, but its statements form one continuous data-flow "
        "chain with no clean split point — decomposing it will need a judgement "
        "call about where one step ends and the next begins."
    ),
}


def _extraction_suggestions(cls) -> list[dict]:
    """Only extract_class suggestions are surfaced here. suggest_refactoring()
    returns a single no_clear_split dict when there's no clean field-sharing
    boundary (including the common case of a class too small to cluster at
    all) -- that is a real, meaningful result, but per analyze_repo()'s
    contract it collapses to an empty suggestions list rather than being
    passed through as a suggestion to act on."""
    return [s for s in suggest_refactoring(cls) if s.get("type") == "extract_class"]


def _method_extraction_suggestions(cls) -> list[dict]:
    return [s for s in suggest_method_extractions(cls) if s.get("type") == "extract_method"]


def _method_move_suggestions(cls, all_classes) -> list[dict]:
    """Both outcomes are kept: a "move_method" naming a destination, and a
    "no_clear_envy_target" note where the data doesn't single one out. The
    soft note is the honest answer, not a failure to be swallowed."""
    return [
        s
        for s in suggest_method_moves(cls, all_classes)
        if s.get("type") in ("move_method", "no_clear_envy_target")
    ]


def _suggestions_for_smell(cls, predicted_smell: str, all_classes: list) -> list[dict]:
    if predicted_smell == "Long Method":
        # Fall back to the explanatory note only when no block split was
        # found -- an empty list would render as "no clear boundary", which
        # is class-extraction phrasing and wrong for a long method.
        found = _method_extraction_suggestions(cls)
        return found or _unsupported_smell_suggestion(predicted_smell)
    if predicted_smell == "Feature Envy":
        found = _method_move_suggestions(cls, all_classes)
        return found or _unsupported_smell_suggestion(predicted_smell)
    if predicted_smell in _COHESION_SMELL_TYPES:
        return _extraction_suggestions(cls)
    return _unsupported_smell_suggestion(predicted_smell)


def _unsupported_smell_suggestion(predicted_smell: str) -> list[dict]:
    note = _UNSUPPORTED_SMELL_NOTES.get(
        predicted_smell,
        f"{predicted_smell} detected — no suggestion strategy is implemented yet "
        "(only extract-class suggestions are supported).",
    )
    return [{"type": "unsupported_smell_type", "note": note}]


def _long_parameter_list_methods(cls) -> list[str]:
    return [m.name for m in cls.methods if parameter_count(m) >= LONG_PARAMETER_LIST_THRESHOLD]


def _long_parameter_list_suggestion(method_names: list[str]) -> dict:
    return {
        "type": "long_parameter_list",
        "note": _LONG_PARAMETER_LIST_NOTE,
        "methods": method_names,
    }


def _duplicate_code_suggestion(pairs: list[tuple[str, str, float]]) -> dict:
    return {
        "type": "duplicate_code",
        "note": _DUPLICATE_CODE_NOTE,
        "threshold": DEFAULT_SIMILARITY_THRESHOLD,
        "pairs": [
            {"methods": [name_a, name_b], "similarity": round(similarity, 3)}
            for name_a, name_b, similarity in pairs
        ],
    }


def _analyze_classes(classes: list) -> dict:
    if not classes:
        return {}

    # Computed once over the full class list: cbo/fan_in/fan_out are
    # relative to all_classes, so per-class calls would silently change
    # the coupling numbers.
    metrics_by_class = compute_all_metrics(classes)

    results = {}
    for cls in classes:
        # id(cls), not cls.name -- two classes in this file can share a name
        # (e.g. sibling models each with their own nested `class Meta:`),
        # and a name-keyed lookup would silently resolve to the wrong one.
        metrics = metrics_by_class.get(id(cls))
        if metrics is None:
            logger.warning("No metrics computed for %s in %s; skipping", cls.name, cls.file_path)
            continue

        try:
            predicted_smell, confidence = predict_smell_with_confidence(metrics)
        except Exception as exc:
            logger.warning("Could not classify %s in %s: %s", cls.name, cls.file_path, exc)
            continue

        suggestions = []
        if predicted_smell != "Clean":
            try:
                suggestions = _suggestions_for_smell(cls, predicted_smell, classes)
            except Exception as exc:
                # A class too small/degenerate to build a meaningful method
                # graph (e.g. 0-1 methods) is handled gracefully further down
                # the stack and simply yields no suggestions -- this catch is
                # a last-resort backstop so an unexpected failure here still
                # doesn't take down the rest of the scan.
                logger.warning(
                    "Could not generate suggestions for %s in %s: %s", cls.name, cls.file_path, exc
                )

        # Layered on top of the ML result, not part of it (see comment on
        # LONG_PARAMETER_LIST_THRESHOLD above). If the class wasn't already
        # flagged with something else, this becomes the flag; if it was,
        # the note is appended alongside whatever suggestions already exist.
        long_param_methods = _long_parameter_list_methods(cls)
        if long_param_methods:
            suggestions = suggestions + [_long_parameter_list_suggestion(long_param_methods)]
            if predicted_smell == "Clean":
                predicted_smell = "Long Parameter List"
                # Not a model confidence -- there's no model here. A
                # threshold check is either true or it isn't, so 1.0 is the
                # honest value, not a borrowed ML score.
                confidence = 1.0

        # Checked after Long Parameter List so that when a class trips both,
        # the unambiguous finding gets the headline label and the heuristic
        # one rides along as an extra note.
        try:
            duplicate_pairs = find_duplicate_pairs(cls)
        except Exception as exc:
            logger.warning(
                "Could not check %s in %s for duplicate code: %s", cls.name, cls.file_path, exc
            )
            duplicate_pairs = []

        if duplicate_pairs:
            suggestions = suggestions + [_duplicate_code_suggestion(duplicate_pairs)]
            if predicted_smell == "Clean":
                predicted_smell = "Duplicate Code"
                # The similarity of the strongest pair, NOT a model
                # probability and not the 1.0 a hard threshold check earns:
                # this number is exactly as soft as the finding it describes.
                confidence = duplicate_pairs[0][2]

        results[cls.name] = {
            "file_path": cls.file_path,
            "metrics": metrics,
            "predicted_smell": predicted_smell,
            "confidence": confidence,
            "suggestions": suggestions,
        }

    return results


def _unused_imports_by_file(file_paths: list[str]) -> dict[str, list[dict]]:
    """Unused-import findings, file-scoped rather than class-scoped -- a file
    with no classes at all (a pure script) still gets checked, and a file's
    entry here has nothing to do with what any class in it was flagged for.
    Only files with at least one finding are included."""
    unused_by_file: dict[str, list[dict]] = {}

    for file_path in file_paths:
        try:
            unused = find_unused_imports_detailed(file_path)
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            logger.warning("Skipping unused-import check for %s: %s", file_path, exc)
            continue
        if unused:
            unused_by_file[file_path] = unused

    return unused_by_file


def analyze_repo(repo_path: str, exclude: list[str] | None = None) -> dict:
    # parse_repo() already skips unparsable files (syntax errors, bad
    # encoding, unreadable files) with a logged warning rather than raising,
    # so a single broken file in the repo can't abort the whole scan. It also
    # skips venvs/build output/etc. by default (see DEFAULT_EXCLUDED_DIRS);
    # `exclude` adds further directory names to prune.
    classes = parse_repo(repo_path, exclude=exclude)
    file_paths = list(iter_python_files(repo_path, exclude=exclude))
    return {
        "classes": _analyze_classes(classes),
        "unused_imports": _unused_imports_by_file(file_paths),
    }


def analyze_file(file_path: str) -> dict:
    classes = parse_file(file_path)
    return {
        "classes": _analyze_classes(classes),
        "unused_imports": _unused_imports_by_file([file_path]),
    }


def analyze_path(path: str, exclude: list[str] | None = None) -> dict:
    """Dispatches to analyze_file() or analyze_repo() based on whether PATH
    is a single .py file or a directory. This is what the CLI calls -- it's
    the one function that doesn't care which kind of path it was given.
    `exclude` is ignored for a single file -- there's nothing to walk.

    Either way the return shape is {"classes": {...}, "unused_imports": {...}}
    -- unused imports are file-scoped, not class-scoped, so they can't live
    inside a per-class entry the way suggestions do."""
    if os.path.isfile(path):
        return analyze_file(path)
    return analyze_repo(path, exclude=exclude)
