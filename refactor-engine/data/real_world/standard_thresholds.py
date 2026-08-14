"""Standard (literature-derived) code-smell thresholds -- re-export shim.

The real table lives in engine/thresholds.py. It has to: pyproject.toml
ships only `engine*`, so anything under data/ is present in a git checkout
but absent from an installed `refactor-scan`, and the `why` command needs
these exact values at runtime to print measured-vs-published comparisons.
Keeping one copy in engine/ is what guarantees the CLI and the corpus
labeler can never drift apart on the numbers.

This module exists so that scripts and notebooks can use the path named in
the project brief:

    from data.real_world.standard_thresholds import THRESHOLDS, RULES

See engine/thresholds.py for the full provenance write-up: which values are
published (Lanza & Marinescu 2006, McCabe 1976), which are ordinary rules of
thumb, why Fontana et al. (2016) is cited for methodology rather than for
numbers, and why Sandouka & Aljamaan (2023) contributed no cutoffs.
"""

from __future__ import annotations

import os
import sys

# data/ is not a package and this file is often run from a script rather
# than imported, so make the project root importable before reaching into
# engine/.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.thresholds import (  # noqa: E402,F401
    CITATIONS,
    CLEAN_LABEL,
    RULES,
    THRESHOLDS,
    Benchmark,
    Rule,
    Threshold,
    accessor_metrics,
    as_manifest,
    benchmarks_for,
    derive_metrics,
    label_for,
    rule_for,
)

__all__ = [
    "CITATIONS",
    "CLEAN_LABEL",
    "RULES",
    "THRESHOLDS",
    "Benchmark",
    "Rule",
    "Threshold",
    "accessor_metrics",
    "as_manifest",
    "benchmarks_for",
    "derive_metrics",
    "label_for",
    "rule_for",
]
