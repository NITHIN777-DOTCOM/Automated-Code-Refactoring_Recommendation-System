"""Published code-smell metric thresholds, and the machinery to check a
class against them.

WHY THIS MODULE EXISTS
----------------------
Two consumers need the SAME numbers:

  1. data/real_world/label_corpus.py -- assigns labels to the real-world
     corpus by threshold rule, because we have no hand-verified ground
     truth for 4,600 scraped classes.
  2. engine/cli -- the `why` command reports each flagged class's measured
     value next to the published threshold, so a result can be defended as
     benchmarked against the literature rather than against the model's own
     opinion.

It lives in engine/ rather than data/ because pyproject.toml ships only
`engine*` (see [tool.setuptools.packages.find]). A threshold table under
data/ would exist in a git checkout but vanish from an installed
`refactor-scan`, so the CLI could not import it. data/real_world/
standard_thresholds.py re-exports this module for scripts that expect it
at that path.

PROVENANCE -- WHAT IS ACTUALLY PUBLISHED, AND WHAT WE DERIVED
-------------------------------------------------------------
Honesty about sourcing matters more here than a tidy table, so the
numbers below are tagged with how firmly they are attributable:

* Lanza & Marinescu (2006) is the primary numeric source. Their detection
  strategies are stated as metric conjunctions with concrete cutoffs
  (God Class: ATFD > 5 AND WMC >= 47 AND TCC < 1/3, etc.).

* Fontana et al. (2016) -- the paper most often cited for ML-based smell
  detection, and the one this project's brief names -- did NOT publish its
  own numeric cutoffs. Section 4.3 of that paper builds its oracle from
  "Advisors": iPlasma and PMD, plus rules from Marinescu (2002), followed
  by MANUAL labeling of the sampled candidates. iPlasma implements the
  Lanza & Marinescu strategies. So citing "Fontana et al." for a number
  really means citing Lanza & Marinescu via iPlasma, and this module says
  so rather than attributing invented values to Fontana.

* Sandouka & Aljamaan (2023) is the Python-specific work the brief asked
  about. We checked it: their Large Class / Long Method labels are taken
  from the PySmell dataset ("each instance was labeled based on the
  published labeled PySmell dataset"), and the paper does not disclose
  PySmell's numeric cutoffs. Their 18 features are Radon raw/Halstead
  metrics, which our AST engine does not compute. We therefore do NOT
  claim any threshold here as theirs. The citation is retained as context
  for the Python-specific framing, not as a source of numbers.

* McCabe (1976) supplies the cyclomatic-complexity > 10 cutoff.

* Two cutoffs are ordinary rules of thumb with no single canonical paper
  (method count > 10, method length > 30). They are tagged
  `source_key="common_practice"` and are never presented as if a specific
  study produced them.

METRIC MAPPING -- DIRECT vs PROXY
---------------------------------
Lanza & Marinescu's strategies use Java-oriented metrics that our Python
AST engine does not all compute. Each threshold below records whether our
metric is a DIRECT equivalent or a documented PROXY, and `why` prints the
proxy note so a reader is never shown a borrowed threshold as if it
applied to an identical measurement:

  WMC          -> direct. Sum of per-method cyclomatic complexity, which
                  is exactly WMC's definition under a CYCLO weight.
  TCC  < 1/3   -> direct-inverse. TCC is the fraction of method pairs that
                  DO share an attribute; engine/metrics.py's lcom() is the
                  fraction that do NOT. TCC < 1/3 is therefore lcom > 2/3.
                  (This also lands on the commonly quoted LCOM > 0.7.)
  LOC (class)  -> direct. class_length.
  LOC (method) -> direct. per-method length.
  WOC/NOAM/NOPA-> direct. Computed here from the AST (see accessor_metrics).
  ATFD > 5     -> PROXY (fan_out). We have no access-to-foreign-data
                  metric; fan_out counts distinct external classes/methods
                  a class reaches. Different measurement, same intent.
  LAA  < 1/3   -> PROXY. Ours is own-attribute accesses over own accesses
                  plus resolved foreign calls, aggregated per class rather
                  than per method.

References
----------
Lanza, M., & Marinescu, R. (2006). Object-Oriented Metrics in Practice:
    Using Software Metrics to Characterize, Evaluate, and Improve the
    Design of Object-Oriented Systems. Springer.
Arcelli Fontana, F., Mantyla, M. V., Zanoni, M., & Marino, A. (2016).
    Comparing and experimenting machine learning techniques for code smell
    detection. Empirical Software Engineering, 21(3), 1143-1191.
McCabe, T. J. (1976). A Complexity Measure. IEEE Transactions on Software
    Engineering, SE-2(4), 308-320.
Fowler, M. (1999). Refactoring: Improving the Design of Existing Code.
    Addison-Wesley.
Sandouka, R., & Aljamaan, H. (2023). Python code smells detection using
    conventional machine learning models. PeerJ Computer Science, 9, e1370.
Chen, Z., Chen, L., Ma, W., & Xu, B. (2016). Detecting Code Smells in
    Python Programs. SATE 2016, 18-23.
"""

from __future__ import annotations

import ast
import fnmatch
import os
from collections import Counter
from dataclasses import dataclass

from engine.metrics import foreign_accesses

# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------

CITATIONS: dict[str, dict[str, str]] = {
    "lanza_marinescu_2006": {
        "short": "Lanza & Marinescu (2006)",
        "full": (
            "Lanza, M., & Marinescu, R. (2006). Object-Oriented Metrics in Practice. Springer. "
            "Detection strategies, Ch. 4-5."
        ),
        "note": (
            "Primary numeric source. Implemented by the iPlasma tool, which Fontana et al. "
            "(2016) used as an Advisor when building their ML smell datasets."
        ),
    },
    "fontana_2016": {
        "short": "Fontana et al. (2016)",
        "full": (
            "Arcelli Fontana, F., Mantyla, M. V., Zanoni, M., & Marino, A. (2016). Comparing and "
            "experimenting machine learning techniques for code smell detection. Empirical "
            "Software Engineering, 21(3), 1143-1191."
        ),
        "note": (
            "Does not publish its own cutoffs: its oracle comes from Advisors (iPlasma, PMD, "
            "Marinescu 2002) plus manual labeling. Cited here for the methodology and for the "
            "God Class / Data Class / Feature Envy / Long Method label set we reuse; the numbers "
            "trace to Lanza & Marinescu via iPlasma."
        ),
    },
    "mccabe_1976": {
        "short": "McCabe (1976)",
        "full": (
            "McCabe, T. J. (1976). A Complexity Measure. IEEE Transactions on Software "
            "Engineering, SE-2(4), 308-320."
        ),
        "note": "Source of the classic 'cyclomatic complexity above 10' review trigger.",
    },
    "sandouka_aljamaan_2023": {
        "short": "Sandouka & Aljamaan (2023)",
        "full": (
            "Sandouka, R., & Aljamaan, H. (2023). Python code smells detection using conventional "
            "machine learning models. PeerJ Computer Science, 9, e1370."
        ),
        "note": (
            "Python-specific ML smell detection. Checked as a threshold source and deliberately "
            "NOT used for numbers: its Large Class / Long Method labels are inherited from the "
            "PySmell dataset rather than from disclosed numeric cutoffs, and its 18 features are "
            "Radon raw/Halstead metrics our AST engine does not compute."
        ),
    },
    "chen_2016_pysmell": {
        "short": "Chen et al. (2016), PySmell",
        "full": (
            "Chen, Z., Chen, L., Ma, W., & Xu, B. (2016). Detecting Code Smells in Python "
            "Programs. SATE 2016, 18-23."
        ),
        "note": (
            "The Python smell tool underlying Sandouka & Aljamaan's labels. Its thresholds are "
            "set by three strategies (experience / statistics / tuning machine) and live in the "
            "tool's configuration rather than in the paper, so no value here is attributed to it."
        ),
    },
    "common_practice": {
        "short": "common practice",
        "full": (
            "Widely repeated rule of thumb with no single canonical study; see Fowler, M. (1999). "
            "Refactoring: Improving the Design of Existing Code. Addison-Wesley, for the "
            "qualitative smell definitions these operationalize."
        ),
        "note": (
            "Flagged explicitly so these are never presented as a published measurement. Used "
            "only where a literature cutoff does not exist in a form our metrics can express."
        ),
    },
}


# ---------------------------------------------------------------------------
# Threshold records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Threshold:
    """One published cutoff, plus how our metric relates to the published one."""

    key: str
    metric: str  # key into the dict returned by derive_metrics()
    plain_name: str  # how `why` names it to a reader
    operator: str  # ">", ">=", "<", "<="
    value: float
    published_metric: str  # the rule as the source states it
    source_key: str
    is_proxy: bool = False
    mapping_note: str = ""

    @property
    def source_short(self) -> str:
        return CITATIONS[self.source_key]["short"]

    def exceeded_by(self, value: float | None) -> bool:
        """True when `value` is on the smelly side of this threshold."""
        if value is None:
            return False
        if self.operator == ">":
            return value > self.value
        if self.operator == ">=":
            return value >= self.value
        if self.operator == "<":
            return value < self.value
        if self.operator == "<=":
            return value <= self.value
        raise ValueError(f"Unknown operator {self.operator!r} on threshold {self.key!r}")


THRESHOLDS: dict[str, Threshold] = {
    t.key: t
    for t in [
        Threshold(
            key="lcom_high",
            metric="lcom",
            plain_name="Cohesion (LCOM)",
            operator=">",
            value=2 / 3,
            published_metric="TCC < 1/3",
            source_key="lanza_marinescu_2006",
            mapping_note=(
                "TCC counts method pairs that share an attribute; our LCOM counts pairs that "
                "do not, so TCC < 1/3 is exactly LCOM > 2/3 (~0.67). Consistent with the "
                "commonly cited LCOM > 0.7."
            ),
        ),
        Threshold(
            key="wmc_very_high",
            metric="wmc",
            plain_name="Weighted method count (WMC)",
            operator=">=",
            value=47,
            published_metric="WMC >= 47 (VERY HIGH)",
            source_key="lanza_marinescu_2006",
            mapping_note="Sum of per-method cyclomatic complexity -- WMC's own definition.",
        ),
        Threshold(
            key="wmc_below_31",
            metric="wmc",
            plain_name="Weighted method count (WMC)",
            operator="<",
            value=31,
            published_metric="WMC < 31",
            source_key="lanza_marinescu_2006",
            mapping_note="Data Class rule: too little behavior to justify the data it holds.",
        ),
        Threshold(
            key="wmc_below_47",
            metric="wmc",
            plain_name="Weighted method count (WMC)",
            operator="<",
            value=47,
            published_metric="WMC < 47",
            source_key="lanza_marinescu_2006",
            mapping_note="Data Class rule, relaxed complexity bound for the many-attribute case.",
        ),
        Threshold(
            key="class_length_high",
            metric="class_length",
            plain_name="Class length (lines)",
            operator=">",
            value=130,
            published_metric="LOC(class) > 130 (HIGH)",
            source_key="lanza_marinescu_2006",
        ),
        Threshold(
            key="method_count_high",
            metric="method_count",
            plain_name="Number of methods",
            operator=">",
            value=10,
            published_metric="NOM > 10",
            source_key="common_practice",
            mapping_note=(
                "Not a Lanza & Marinescu number -- their God Class rule uses WMC, not raw method "
                "count. Kept because a bloated manager class of many trivial methods carries low "
                "WMC and would otherwise be missed entirely."
            ),
        ),
        Threshold(
            key="method_length_long",
            metric="max_method_length",
            plain_name="Longest method (lines)",
            operator=">",
            value=65,
            published_metric="LOC(method) > 65",
            source_key="lanza_marinescu_2006",
            mapping_note="The Long Method rule as implemented in iPlasma, one of Fontana's Advisors.",
        ),
        Threshold(
            key="method_length_moderate",
            metric="max_method_length",
            plain_name="Longest method (lines)",
            operator=">",
            value=30,
            published_metric="LOC(method) > 30",
            source_key="common_practice",
            mapping_note="Paired with McCabe CC > 10 to catch dense-but-shorter methods.",
        ),
        Threshold(
            key="method_cc_high",
            metric="max_method_cc",
            plain_name="Peak cyclomatic complexity",
            operator=">",
            value=10,
            published_metric="v(G) > 10",
            source_key="mccabe_1976",
            mapping_note="Computed by engine/metrics.py:cyclomatic_complexity().",
        ),
        Threshold(
            key="woc_low",
            metric="woc",
            plain_name="Weight of class (WOC)",
            operator="<",
            value=1 / 3,
            published_metric="WOC < 1/3",
            source_key="lanza_marinescu_2006",
            mapping_note=(
                "Share of public methods that do real work rather than just reading or writing "
                "one attribute."
            ),
        ),
        Threshold(
            key="noam_nopa_gt_5",
            metric="noam_plus_nopa",
            plain_name="Exposed attributes + accessors",
            operator=">",
            value=5,
            published_metric="NOPA + NOAM > 5",
            source_key="lanza_marinescu_2006",
        ),
        Threshold(
            key="noam_nopa_gt_8",
            metric="noam_plus_nopa",
            plain_name="Exposed attributes + accessors",
            operator=">",
            value=8,
            published_metric="NOPA + NOAM > 8",
            source_key="lanza_marinescu_2006",
        ),
        Threshold(
            key="atfd_high",
            metric="atfd",
            plain_name="Foreign data reached (ATFD)",
            operator=">",
            value=5,
            published_metric="ATFD > 5 (FEW)",
            source_key="lanza_marinescu_2006",
            mapping_note=(
                "Direct, no longer a proxy. Counts distinct (provider, attribute) pairs the "
                "class reads off objects other than self, excluding dunder attributes and "
                "explicit base-class access. Providers are grouped by receiver VARIABLE NAME "
                "(stage 1, no type inference), so two same-named variables of different types "
                "would be conflated."
            ),
        ),
        Threshold(
            key="fdp_few",
            metric="fdp",
            plain_name="Distinct foreign providers (FDP)",
            operator="<=",
            value=5,
            published_metric="FDP <= 5 (FEW)",
            source_key="lanza_marinescu_2006",
            mapping_note=(
                "Now computable and no longer dropped from the rule. Distinguishes a class "
                "fixated on one or two neighbours (real Feature Envy, fixable by Move Method) "
                "from one touching a little of everything (dispersed coupling, a different "
                "problem). Same stage-1 receiver-name caveat as ATFD."
            ),
        ),
        Threshold(
            key="laa_low",
            metric="laa",
            plain_name="Attribute locality (LAA)",
            operator="<",
            value=1 / 3,
            published_metric="LAA < 1/3",
            source_key="lanza_marinescu_2006",
            is_proxy=True,
            mapping_note=(
                "APPROXIMATION: the foreign half is now a real ATFD rather than resolved call "
                "names, but this is still aggregated across the whole class where Lanza & "
                "Marinescu define LAA per method."
            ),
        ),
    ]
}


# ---------------------------------------------------------------------------
# Rules -- how thresholds combine into a label
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    """A smell's detection rule, as a readable formula plus a predicate.

    `threshold_keys` lists every threshold the rule consults, in the order
    `why` should display them. `formula` is the human-readable conjunction
    shown in reports and in the corpus manifest.
    """

    label: str
    formula: str
    threshold_keys: tuple[str, ...]
    rationale: str

    def matches(self, m: dict) -> bool:
        return _RULE_PREDICATES[self.label](m)


def _god_class(m: dict) -> bool:
    # Lanza & Marinescu: ATFD > FEW AND WMC >= VERY HIGH AND TCC < 1/3.
    # ATFD is dropped rather than proxied here: metrics are computed per
    # FILE (see label_corpus.py), which pins fan_out at 0 for the very
    # common single-class file, and an always-false conjunct would make the
    # whole rule dead. The size/complexity conjunct is widened to "WMC very
    # high OR simply a long class" so the bloated-but-simple manager class
    # -- many trivial methods, low WMC -- is still caught.
    return (
        THRESHOLDS["lcom_high"].exceeded_by(m.get("lcom"))
        and THRESHOLDS["method_count_high"].exceeded_by(m.get("method_count"))
        and (
            THRESHOLDS["wmc_very_high"].exceeded_by(m.get("wmc"))
            or THRESHOLDS["class_length_high"].exceeded_by(m.get("class_length"))
        )
    )


def _feature_envy(m: dict) -> bool:
    # Lanza & Marinescu, now VERBATIM and with no dropped conjunct:
    #   ATFD > FEW AND LAA < 1/3 AND FDP <= FEW
    # The FDP arm used to be dropped as needing type resolution. Grouping
    # foreign reads by receiver name makes it computable (engine.metrics.fdp),
    # and it is what stops a class that touches a dozen unrelated providers a
    # little -- dispersed coupling, not envy -- from being labelled Feature
    # Envy and handed a Move Method suggestion that cannot work.
    #
    # A class with no foreign access at all has fdp == 0, which satisfies
    # `<= 5` trivially; the ATFD arm is what excludes it, and must stay first.
    return (
        THRESHOLDS["atfd_high"].exceeded_by(m.get("atfd"))
        and THRESHOLDS["laa_low"].exceeded_by(m.get("laa"))
        and THRESHOLDS["fdp_few"].exceeded_by(m.get("fdp"))
    )


def _long_method(m: dict) -> bool:
    # Either arm alone is a published rule: iPlasma's LOC > 65, or McCabe's
    # v(G) > 10 qualified by a length floor so a short, branch-dense guard
    # clause doesn't read as a long method.
    if THRESHOLDS["method_length_long"].exceeded_by(m.get("max_method_length")):
        return True
    return THRESHOLDS["method_cc_high"].exceeded_by(m.get("max_method_cc")) and THRESHOLDS[
        "method_length_moderate"
    ].exceeded_by(m.get("max_method_length"))


def _data_class(m: dict) -> bool:
    # Lanza & Marinescu, verbatim:
    #   WOC < 1/3 AND ((NOPA+NOAM > 5 AND WMC < 31) OR (NOPA+NOAM > 8 AND WMC < 47))
    if not THRESHOLDS["woc_low"].exceeded_by(m.get("woc")):
        return False
    small = THRESHOLDS["noam_nopa_gt_5"].exceeded_by(m.get("noam_plus_nopa")) and THRESHOLDS[
        "wmc_below_31"
    ].exceeded_by(m.get("wmc"))
    large = THRESHOLDS["noam_nopa_gt_8"].exceeded_by(m.get("noam_plus_nopa")) and THRESHOLDS[
        "wmc_below_47"
    ].exceeded_by(m.get("wmc"))
    return small or large


_RULE_PREDICATES = {
    "God Class": _god_class,
    "Feature Envy": _feature_envy,
    "Long Method": _long_method,
    "Data Class": _data_class,
}

# Order matters: a class can satisfy several rules, and the label written to
# the dataset is the FIRST match here. Rationale for this particular order:
# God Class is a whole-class structural failure and subsumes the others when
# it fires; Feature Envy is a specific misplacement claim and is preferred
# over the generic "some method is long"; Data Class is last because it is an
# absence of behavior, the weakest of the four claims.
RULES: tuple[Rule, ...] = (
    Rule(
        label="God Class",
        formula="LCOM > 2/3 AND NOM > 10 AND (WMC >= 47 OR LOC(class) > 130)",
        threshold_keys=("lcom_high", "method_count_high", "wmc_very_high", "class_length_high"),
        rationale=(
            "Lanza & Marinescu's God Class strategy with ATFD dropped (not computable under "
            "per-file metric scope) and the size conjunct widened to catch low-complexity "
            "bloated managers."
        ),
    ),
    Rule(
        label="Feature Envy",
        formula="ATFD > 5 AND LAA < 1/3 AND FDP <= 5",
        threshold_keys=("atfd_high", "laa_low", "fdp_few"),
        rationale=(
            "Lanza & Marinescu's Feature Envy strategy, all three conjuncts, with a real "
            "ATFD measured from foreign attribute reads rather than proxied by fan_out. "
            "Providers are resolved by receiver variable name (stage 1, no type inference); "
            "LAA remains aggregated per class rather than per method."
        ),
    ),
    Rule(
        label="Long Method",
        formula="LOC(method) > 65 OR (v(G) > 10 AND LOC(method) > 30)",
        threshold_keys=("method_length_long", "method_cc_high", "method_length_moderate"),
        rationale="iPlasma's Long Method LOC rule, OR McCabe's complexity trigger with a length floor.",
    ),
    Rule(
        label="Data Class",
        formula="WOC < 1/3 AND ((NOPA+NOAM > 5 AND WMC < 31) OR (NOPA+NOAM > 8 AND WMC < 47))",
        threshold_keys=(
            "woc_low",
            "noam_nopa_gt_5",
            "wmc_below_31",
            "noam_nopa_gt_8",
            "wmc_below_47",
        ),
        rationale="Lanza & Marinescu's Data Class strategy, applied verbatim.",
    ),
)

CLEAN_LABEL = "Clean"


def label_for(metrics: dict) -> tuple[str, list[str]]:
    """The threshold label for a class, plus every rule it matched.

    Returns (label, all_matched_labels). The second element is what makes
    the precedence order auditable: a class that trips three rules is
    visible as such rather than silently collapsing to one.
    """
    matched = [rule.label for rule in RULES if rule.matches(metrics)]
    return (matched[0] if matched else CLEAN_LABEL), matched


# ---------------------------------------------------------------------------
# Derived metrics
# ---------------------------------------------------------------------------


def _strip_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _is_trivial_accessor(func_node: ast.AST | None) -> bool:
    """True for a one-line getter or setter -- `return self.x` / `self.x = v`.

    Deliberately strict: a "getter" that computes, validates, or reads more
    than one attribute is real behavior and must count toward WOC, or every
    slightly-defensive class would read as a Data Class.
    """
    if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False

    body = _strip_docstring(func_node.body)
    if len(body) != 1:
        return False
    stmt = body[0]

    if (
        isinstance(stmt, ast.Return)
        and isinstance(stmt.value, ast.Attribute)
        and isinstance(stmt.value.value, ast.Name)
    ):
        return True

    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        target = stmt.targets[0]
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and isinstance(stmt.value, ast.Name)
        ):
            return True

    return False


def accessor_metrics(cls) -> dict:
    """WOC, NOAM and NOPA for a ClassInfo -- the Data Class inputs.

    Dunder methods are excluded from the public interface: __init__ and
    friends are machinery, and counting __init__ as a functional method
    would let any class with a constructor clear the WOC bar.
    """
    public_methods = [m for m in cls.methods if not m.name.startswith("_")]
    accessors = [m for m in public_methods if _is_trivial_accessor(m.body)]
    public_attributes = [f for f in cls.fields if not f.startswith("_")]

    noam = len(accessors)
    nopa = len(public_attributes)

    if public_methods:
        woc = (len(public_methods) - noam) / len(public_methods)
    else:
        # No public methods at all. If the class still exposes public
        # attributes it is the purest form of a data holder (WOC 0.0);
        # if it exposes nothing it is not a data class, so 1.0 keeps it
        # out of the rule rather than dividing by zero.
        woc = 0.0 if nopa else 1.0

    return {"woc": woc, "noam": noam, "nopa": nopa, "noam_plus_nopa": noam + nopa}


def is_test_class(cls) -> bool:
    """True for a unit-test class.

    Test classes are excluded from threshold labeling because they break the
    cohesion metrics BY DESIGN, not by accident: a TestCase is dozens of
    independent test methods that deliberately share no instance state, so
    LCOM pins at 1.0 and method count runs high. Measured against a God
    Class rule they look like textbook god classes -- in the first labeling
    pass over this corpus, 37% of everything labeled God Class was a
    TestCase. That is a property of the xUnit pattern, not a design flaw a
    refactoring tool should report, and training on it would teach the
    model that "many independent methods" is the dominant God Class signal.

    Smell-detection research conventionally analyzes production code for the
    same reason (e.g. Fontana et al. sample from the Qualitas Corpus's
    application code).
    """
    name = cls.name
    if name.startswith("Test") or name.endswith(("Test", "Tests", "TestCase")):
        return True
    return any(base.endswith(("TestCase", "TestSuite")) for base in cls.base_classes)


# Directory names and filename globs that mark test code. Mirrors the style
# of engine/parser.py's DEFAULT_EXCLUDED_DIRS (fixed names plus fnmatch
# globs) but is deliberately NOT merged into it: that set controls what the
# shipped `refactor-scan analyze` walks, and silently refusing to report
# smells in a user's own test suite is a product decision, not a labeling
# one. This pair is consulted only when building training data, where test
# code is a known contaminant -- see is_test_path.
TEST_DIR_NAMES = frozenset({"test", "tests", "testing", "testsuite", "test_suite"})
TEST_FILE_PATTERNS = ("test_*.py", "*_test.py", "tests.py", "testing.py", "conftest.py")


def is_test_path(file_path: str) -> bool:
    """True when a path is test code by directory or filename convention.

    Complements is_test_class, which only sees the class. A helper named
    `FakeObject` sitting in `horizon/test/tests/tables.py` is a test fixture
    no matter what it is called, and 14 of the 37 Data Class rows in the
    previous labeling pass were exactly that.
    """
    normalized = file_path.replace("\\", "/")
    directories, _, basename = normalized.rpartition("/")

    if any(part.lower() in TEST_DIR_NAMES for part in directories.split("/") if part):
        return True
    return any(fnmatch.fnmatch(basename.lower(), pattern) for pattern in TEST_FILE_PATTERNS)


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


# ---------------------------------------------------------------------------
# Corpus-scale summaries
#
# Feature Envy and inherited-WOC both need to look BEYOND the file a class
# lives in, which means holding something for every class in the corpus at
# once. Holding ClassInfo objects would mean holding their ast.FunctionDef
# bodies -- several GB across ~3,600 classes. ClassSummary keeps only the
# strings those computations actually read, so a whole-corpus index costs
# tens of MB instead.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassSummary:
    """The parts of a ClassInfo that cross-class analysis needs, minus the AST."""

    name: str
    scope_key: str  # classes sharing this key can see each other
    file_path: str
    base_classes: tuple[str, ...]
    method_names: frozenset[str]
    public_methods: tuple[str, ...]
    accessor_methods: frozenset[str]
    public_fields: tuple[str, ...]
    calls: frozenset[str]
    own_field_accesses: int
    # Real ATFD/FDP input: (receiver, attribute) foreign reads, already
    # filtered by engine.metrics.foreign_accesses(). Carried on the summary
    # so corpus-scale labeling gets the real metric without holding ASTs.
    foreign_accesses: tuple[tuple[str, str], ...] = ()


def summarize_class(cls, scope_key: str = "", file_path: str = "") -> ClassSummary:
    public_methods = tuple(m.name for m in cls.methods if not m.name.startswith("_"))
    accessors = frozenset(
        m.name
        for m in cls.methods
        if not m.name.startswith("_") and _is_trivial_accessor(m.body)
    )
    calls: set[str] = set()
    for method in cls.methods:
        calls.update(method.calls_made)

    return ClassSummary(
        name=cls.name,
        scope_key=scope_key,
        file_path=file_path or getattr(cls, "file_path", ""),
        base_classes=tuple(cls.base_classes),
        method_names=frozenset(m.name for m in cls.methods),
        public_methods=public_methods,
        accessor_methods=accessors,
        public_fields=tuple(f for f in cls.fields if not f.startswith("_")),
        calls=frozenset(calls),
        own_field_accesses=sum(len(m.fields_accessed) for m in cls.methods),
        foreign_accesses=tuple(foreign_accesses(cls)),
    )


@dataclass
class CorpusIndex:
    """Name lookups over a set of summaries, built once and reused.

    `by_scope` answers "what can this class see?" -- the scope_key decides
    whether that is its file, its package, its repository, or the whole
    corpus. `by_name` answers "where is this base class defined?" and is
    always global, because an import can cross any of those boundaries.
    """

    by_scope: dict[str, dict]
    by_name: dict[str, list[ClassSummary]]


def build_index(summaries) -> CorpusIndex:
    by_scope: dict[str, dict] = {}
    by_name: dict[str, list[ClassSummary]] = {}

    for summary in summaries:
        scope = by_scope.setdefault(summary.scope_key, {"class_names": set(), "owners": {}})
        scope["class_names"].add(summary.name)
        for method_name in summary.method_names:
            scope["owners"].setdefault(method_name, set()).add(summary.name)
        by_name.setdefault(summary.name, []).append(summary)

    return CorpusIndex(by_scope=by_scope, by_name=by_name)


def envy_from_summary(summary: ClassSummary, index: CorpusIndex | None = None) -> dict:
    """REAL ATFD, FDP and LAA for the Feature Envy rule.

    WHAT CHANGED, AND WHY IT MATTERS
    --------------------------------
    This used to return an `atfd_proxy` derived from resolving the class's
    outbound CALL NAMES against every class in scope, because the parser
    discarded foreign attribute reads and there was nothing better available.
    engine/parser.py now records them, so this is the real metric: a count of
    the foreign DATA the class actually touches, taken straight from its own
    AST.

    Three consequences worth stating, because they are the whole point of the
    change:

      1. NO SCOPE SENSITIVITY. The proxy's value depended on how many other
         classes happened to be visible -- the same corpus yielded 4 matches
         at file scope and 405 at corpus scope, a 100x swing driven purely by
         coincidental name collisions between unrelated projects. A foreign
         attribute read is visible in the class's own body, so `index` is no
         longer consulted at all. It stays in the signature (unused) because
         every caller passes one and the corpus-scope machinery around it is
         still needed for WOC's base-class resolution.
      2. NO SELF/OWN-METHOD CONFUSION. The proxy had to discard any call
         whose name matched one of this class's own methods, because it could
         not tell `self.save()` from `other.save()`. Receivers are known now,
         so `other.save()` counts and `self.save()` does not.
      3. FDP IS COMPUTABLE. Lanza & Marinescu's rule has always been
         ATFD > FEW AND LAA < 1/3 AND FDP <= FEW; we previously dropped the
         FDP conjunct as needing type resolution. Grouping reads by receiver
         name gives a usable stage-1 FDP, so the rule is now implemented in
         full. See MethodInfo.foreign_accesses for the naming caveat.
    """
    pairs = summary.foreign_accesses
    per_provider: dict[str, set[str]] = {}
    for receiver, attribute in pairs:
        per_provider.setdefault(receiver, set()).add(attribute)

    counts = Counter({recv: len(attrs) for recv, attrs in per_provider.items()})
    atfd = sum(counts.values())
    total_accesses = atfd
    concentration = (
        counts.most_common(1)[0][1] / total_accesses if total_accesses else 0.0
    )

    total = summary.own_field_accesses + atfd
    return {
        "atfd": atfd,
        "fdp": len(counts),
        "fdp_concentration": round(concentration, 4),
        "laa": (summary.own_field_accesses / total) if total else 1.0,
    }


def envy_metrics(cls, all_classes=None) -> dict:
    """Real ATFD/FDP/LAA for one ClassInfo.

    `all_classes` is accepted and ignored -- kept so the existing call sites
    (CLI, reasoning) keep working unchanged now that the metric no longer
    needs a resolution scope at all.
    """
    return envy_from_summary(summarize_class(cls, "", getattr(cls, "file_path", "")))


# ---------------------------------------------------------------------------
# Inherited interface (WOC/NOAM/NOPA across a class hierarchy)
# ---------------------------------------------------------------------------

# How far up a hierarchy to walk. Deep chains are rare and a cap keeps a
# pathological or cyclic hierarchy from stalling a corpus-wide run.
_MAX_INHERITANCE_DEPTH = 10


def _pick_base(base_name: str, summary: ClassSummary, index: CorpusIndex):
    """Resolve one base-class name to a summary, or None if it is a guess.

    Same file wins outright -- that is where Python base classes usually
    live, and it is unambiguous. Otherwise a name is only accepted when the
    whole corpus defines it exactly once. Ten classes named `Base` across
    ten projects tell us nothing about which one this class extends, and
    picking one at random would silently fabricate an interface.
    """
    candidates = index.by_name.get(base_name)
    if not candidates:
        return None

    same_file = [c for c in candidates if c.file_path and c.file_path == summary.file_path]
    if len(same_file) == 1:
        return same_file[0]
    if len(candidates) == 1:
        return candidates[0]
    return None


def resolve_interface(summary: ClassSummary, index: CorpusIndex) -> dict:
    """The class's public interface including everything it inherits.

    Fixes a blind spot in body-scoped WOC: a class that defines only
    property accessors while inheriting fit/predict from a base reads as a
    pure data holder. sklearn's ComplementNB is exactly this shape, and 86%
    of the Data Class labels in the previous pass were classes with a base.

    Most-derived definition wins, so a subclass that overrides an inherited
    accessor with real logic is credited with the real logic.

    `status` reports honestly how much of the hierarchy we could actually
    see: "no_bases", "resolved" (all of it), "partial" (some bases resolved,
    some not), or "unresolved" (a base exists but none of it was findable).
    Callers that need to know whether WOC is trustworthy read this rather
    than being handed a confident-looking number built on a guess.
    """
    accessor_by_name: dict[str, bool] = {}
    fields = set(summary.public_fields)

    for name in summary.public_methods:
        accessor_by_name.setdefault(name, name in summary.accessor_methods)

    resolved_count = 0
    unresolved_count = 0
    seen = {summary.name}
    queue = list(summary.base_classes)
    depth = 0

    while queue and depth < _MAX_INHERITANCE_DEPTH:
        depth += 1
        next_queue: list[str] = []
        for base_name in queue:
            if base_name in seen:
                continue
            seen.add(base_name)

            base = _pick_base(base_name, summary, index)
            if base is None:
                unresolved_count += 1
                continue

            resolved_count += 1
            for name in base.public_methods:
                accessor_by_name.setdefault(name, name in base.accessor_methods)
            fields.update(base.public_fields)
            next_queue.extend(base.base_classes)
        queue = next_queue

    if not summary.base_classes:
        status = "no_bases"
    elif unresolved_count == 0:
        status = "resolved"
    elif resolved_count == 0:
        status = "unresolved"
    else:
        status = "partial"

    return {
        "public_methods": accessor_by_name,
        "public_fields": fields,
        "status": status,
    }


def woc_from_summary(summary: ClassSummary, index: CorpusIndex) -> dict:
    """WOC, NOAM and NOPA computed over the inherited interface.

    Falls back to the body-scoped numbers when nothing above the class could
    be resolved, and always reports which of the two happened via
    `inheritance_status` so a Data Class label built on an unresolvable
    hierarchy can be told apart from one built on a complete picture.
    """
    interface = resolve_interface(summary, index)

    if interface["status"] == "unresolved":
        # A base exists but we cannot see it. Guessing at an inherited
        # interface would be worse than admitting the body is all we have.
        body = _woc_from_parts(
            {name: name in summary.accessor_methods for name in summary.public_methods},
            set(summary.public_fields),
        )
        body["inheritance_status"] = "unresolved"
        return body

    result = _woc_from_parts(interface["public_methods"], interface["public_fields"])
    result["inheritance_status"] = interface["status"]
    return result


def body_scoped_woc(summary: ClassSummary) -> dict:
    """WOC ignoring inheritance -- the pre-fix behavior.

    Kept as a named function so the labeler can report exactly what the
    inheritance fix changed, rather than asking a reader to take the
    before/after counts on trust.
    """
    return _woc_from_parts(
        {name: name in summary.accessor_methods for name in summary.public_methods},
        set(summary.public_fields),
    )


def _woc_from_parts(accessor_by_name: dict, public_fields: set) -> dict:
    noam = sum(1 for is_accessor in accessor_by_name.values() if is_accessor)
    nopa = len(public_fields)

    if accessor_by_name:
        woc = (len(accessor_by_name) - noam) / len(accessor_by_name)
    else:
        # No public methods at all. If the class still exposes public
        # attributes it is the purest form of a data holder (WOC 0.0);
        # if it exposes nothing it is not a data class, so 1.0 keeps it
        # out of the rule rather than dividing by zero.
        woc = 0.0 if nopa else 1.0

    return {"woc": woc, "noam": noam, "nopa": nopa, "noam_plus_nopa": noam + nopa}


def derive_metrics(metrics: dict, cls=None, all_classes=None) -> dict:
    """Flatten one class's compute_all_metrics() entry into the flat metric
    namespace the thresholds are written against.

    `metrics` is the per-class dict produced by
    engine.metrics.compute_all_metrics(). `cls` is the matching ClassInfo;
    without it the accessor-derived metrics (WOC/NOAM/NOPA) cannot be
    computed and the Data Class rule does not fire. `all_classes` is the
    scope the metrics were computed over; without it the ATFD/LAA proxies
    are unavailable and the Feature Envy rule does not fire. In both cases
    the rule stays silent rather than guessing.
    """
    method_metrics = metrics.get("methods", {})
    complexities = [m["cyclomatic_complexity"] for m in method_metrics.values()]
    lengths = [m["length"] for m in method_metrics.values()]
    count = len(method_metrics)

    derived = {
        "lcom": metrics.get("lcom", 0.0),
        "class_length": metrics.get("class_length", 0),
        "cbo": metrics.get("cbo", 0),
        "fan_in": metrics.get("fan_in", 0),
        "fan_out": metrics.get("fan_out", 0),
        "dit": metrics.get("depth_of_inheritance", 0),
        "method_count": count,
        "wmc": sum(complexities),
        "max_method_cc": max(complexities) if complexities else 0,
        "max_method_length": max(lengths) if lengths else 0,
        "avg_method_length": (sum(lengths) / count) if count else 0.0,
        "avg_cyclomatic_complexity": (sum(complexities) / count) if count else 0.0,
    }

    if cls is not None:
        # ATFD/FDP/LAA need no resolution scope now that they are measured
        # from the class's own foreign attribute reads, so the envy metrics
        # are available whether or not `all_classes` was supplied. Only WOC
        # still needs an index, to resolve inherited methods across files.
        summary = summarize_class(cls, "_local", getattr(cls, "file_path", ""))
        derived.update(envy_from_summary(summary))

        if all_classes is None:
            derived.update(accessor_metrics(cls))
        else:
            summaries = [
                summarize_class(c, "_local", getattr(c, "file_path", "")) for c in all_classes
            ]
            derived.update(woc_from_summary(summary, build_index(summaries)))

    return derived


# ---------------------------------------------------------------------------
# Benchmarking -- what `why` prints
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Benchmark:
    """One measured value placed next to the published threshold it is judged against."""

    metric: str
    plain_name: str
    value: float
    threshold: float
    operator: str
    exceeds: bool
    published_metric: str
    source_short: str
    source_key: str
    is_proxy: bool
    mapping_note: str

    def as_sentence(self) -> str:
        """e.g. 'LCOM: 0.82 (published threshold: > 0.67, source: Lanza & Marinescu (2006))'."""
        return (
            f"{self.plain_name}: {_fmt_number(self.value)} "
            f"(published threshold: {self.operator} {_fmt_number(self.threshold)}, "
            f"source: {self.source_short})"
        )


def _fmt_number(value: float) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.2f}"
    return str(int(value))


# Shown when a class carries a label with no threshold rule of its own
# (Clean, Long Parameter List, Duplicate Code). These are the four headline
# measurements a reader is most likely to want benchmarked anyway.
_CORE_BENCHMARK_KEYS = ("lcom_high", "wmc_very_high", "class_length_high", "method_cc_high")

# The smell names the pipeline can emit, mapped to the rule whose thresholds
# explain them. Names not listed here fall back to the core set.
_SMELL_TO_RULE = {rule.label: rule for rule in RULES}


def benchmarks_for(derived: dict, smell: str | None = None) -> list[Benchmark]:
    """Measured-vs-published comparisons relevant to `smell`.

    Thresholds whose metric is missing from `derived` are skipped rather
    than reported against a fabricated value -- this is what keeps a Data
    Class benchmark from appearing when no ClassInfo was available to
    compute WOC.
    """
    rule = _SMELL_TO_RULE.get(smell or "")
    keys = rule.threshold_keys if rule else _CORE_BENCHMARK_KEYS

    results = []
    for key in keys:
        threshold = THRESHOLDS[key]
        value = derived.get(threshold.metric)
        if value is None:
            continue
        results.append(
            Benchmark(
                metric=threshold.metric,
                plain_name=threshold.plain_name,
                value=value,
                threshold=threshold.value,
                operator=threshold.operator,
                exceeds=threshold.exceeded_by(value),
                published_metric=threshold.published_metric,
                source_short=threshold.source_short,
                source_key=threshold.source_key,
                is_proxy=threshold.is_proxy,
                mapping_note=threshold.mapping_note,
            )
        )
    return results


def rule_for(smell: str) -> Rule | None:
    return _SMELL_TO_RULE.get(smell)


def as_manifest() -> dict:
    """The whole threshold table as plain data, for the corpus manifest."""
    return {
        "citations": CITATIONS,
        "thresholds": {
            key: {
                "our_metric": t.metric,
                "rule": f"{t.metric} {t.operator} {_fmt_number(t.value)}",
                "published_metric": t.published_metric,
                "source": t.source_short,
                "source_key": t.source_key,
                "is_proxy": t.is_proxy,
                "mapping_note": t.mapping_note,
            }
            for key, t in THRESHOLDS.items()
        },
        "rules": [
            {
                "label": r.label,
                "formula": r.formula,
                "rationale": r.rationale,
                "thresholds_used": list(r.threshold_keys),
            }
            for r in RULES
        ],
        "precedence": [r.label for r in RULES] + [CLEAN_LABEL],
    }
