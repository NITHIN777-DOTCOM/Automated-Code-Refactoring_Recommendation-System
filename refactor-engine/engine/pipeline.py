"""End-to-end wiring: parse a repo, compute metrics, classify each class's
smell, and propose extraction suggestions for anything non-Clean (or an
explicit unsupported_smell_type suggestion for smells Phase 3 has no
strategy for yet).

Phase 1 (engine.parser, engine.metrics) -> Phase 2 (engine.ml.predict) ->
Phase 3 (engine.suggester). This module contains no analysis logic of its
own -- it only sequences the three phases and shapes their combined output.
"""

from __future__ import annotations

import logging
import os

from engine.metrics import compute_all_metrics
from engine.ml.predict import predict_smell_with_confidence
from engine.parser import parse_file, parse_repo
from engine.suggester import suggest_refactoring

logger = logging.getLogger(__name__)

# Phase 3's suggester only has a strategy for smells rooted in low cohesion
# (God Class, Data Class), where field-sharing clustering can propose an
# Extract Class split. Feature Envy (move method to where it's coupled) and
# Long Method (decompose the method) call for entirely different mechanics
# that don't exist yet -- those smells get an explicit unsupported_smell_type
# suggestion instead of silently returning an empty list, so the gap is
# visible rather than reading as "no refactoring needed."
_SUPPORTED_SMELL_TYPES = {"God Class", "Data Class"}

_UNSUPPORTED_SMELL_NOTES = {
    "Feature Envy": (
        "Feature Envy detected — suggests moving method(s) to the class they're "
        "most coupled with, which isn't implemented yet (only extract-class "
        "suggestions are supported)."
    ),
    "Long Method": (
        "Long Method detected — suggests decomposing the method into smaller "
        "steps, which isn't implemented yet (only extract-class suggestions "
        "are supported)."
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


def _unsupported_smell_suggestion(predicted_smell: str) -> list[dict]:
    note = _UNSUPPORTED_SMELL_NOTES.get(
        predicted_smell,
        f"{predicted_smell} detected — no suggestion strategy is implemented yet "
        "(only extract-class suggestions are supported).",
    )
    return [{"type": "unsupported_smell_type", "note": note}]


def _analyze_classes(classes: list) -> dict:
    if not classes:
        return {}

    # Computed once over the full class list: cbo/fan_in/fan_out are
    # relative to all_classes, so per-class calls would silently change
    # the coupling numbers.
    metrics_by_class = compute_all_metrics(classes)

    results = {}
    for cls in classes:
        metrics = metrics_by_class.get(cls.name)
        if metrics is None:
            logger.warning("No metrics computed for %s in %s; skipping", cls.name, cls.file_path)
            continue

        try:
            predicted_smell, confidence = predict_smell_with_confidence(metrics)
        except Exception as exc:
            logger.warning("Could not classify %s in %s: %s", cls.name, cls.file_path, exc)
            continue

        suggestions = []
        if predicted_smell != "Clean" and predicted_smell not in _SUPPORTED_SMELL_TYPES:
            suggestions = _unsupported_smell_suggestion(predicted_smell)
        elif predicted_smell != "Clean":
            try:
                suggestions = _extraction_suggestions(cls)
            except Exception as exc:
                # A class too small/degenerate to build a meaningful method
                # graph (e.g. 0-1 methods) is handled gracefully further down
                # the stack and simply yields no suggestions -- this catch is
                # a last-resort backstop so an unexpected failure here still
                # doesn't take down the rest of the scan.
                logger.warning(
                    "Could not generate suggestions for %s in %s: %s", cls.name, cls.file_path, exc
                )

        results[cls.name] = {
            "file_path": cls.file_path,
            "metrics": metrics,
            "predicted_smell": predicted_smell,
            "confidence": confidence,
            "suggestions": suggestions,
        }

    return results


def analyze_repo(repo_path: str, exclude: list[str] | None = None) -> dict:
    # parse_repo() already skips unparsable files (syntax errors, bad
    # encoding, unreadable files) with a logged warning rather than raising,
    # so a single broken file in the repo can't abort the whole scan. It also
    # skips venvs/build output/etc. by default (see DEFAULT_EXCLUDED_DIRS);
    # `exclude` adds further directory names to prune.
    classes = parse_repo(repo_path, exclude=exclude)
    return _analyze_classes(classes)


def analyze_file(file_path: str) -> dict:
    classes = parse_file(file_path)
    return _analyze_classes(classes)


def analyze_path(path: str, exclude: list[str] | None = None) -> dict:
    """Dispatches to analyze_file() or analyze_repo() based on whether PATH
    is a single .py file or a directory. This is what the CLI calls -- it's
    the one function that doesn't care which kind of path it was given.
    `exclude` is ignored for a single file -- there's nothing to walk."""
    if os.path.isfile(path):
        return analyze_file(path)
    return analyze_repo(path, exclude=exclude)
