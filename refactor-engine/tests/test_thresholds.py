"""Tests for the published-threshold table and the labeling rules built on it.

These guard the two things that make a threshold label defensible: that the
NUMBERS match what the cited sources actually say, and that the metric
artifacts found while labeling the real-world corpus stay fixed.
"""

from __future__ import annotations

import os
import tempfile

from engine.metrics import compute_all_metrics
from engine.parser import parse_file
from engine.thresholds import (
    CITATIONS,
    RULES,
    THRESHOLDS,
    accessor_metrics,
    benchmarks_for,
    body_scoped_woc,
    build_index,
    derive_metrics,
    envy_from_summary,
    envy_metrics,
    is_test_class,
    is_test_path,
    label_for,
    summarize_class,
    woc_from_summary,
)


def _classes_from_source(source: str):
    """Parse a source string through the real parser, as the labeler does."""
    tmp = tempfile.mkdtemp(prefix="refactor_thresholds_test_")
    path = os.path.join(tmp, "sample.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(source)
    return parse_file(path)


def _derived(source: str, class_name: str) -> dict:
    classes = _classes_from_source(source)
    metrics = compute_all_metrics(classes)
    cls = next(c for c in classes if c.name == class_name)
    return derive_metrics(metrics[class_name], cls, classes)


# ---------------------------------------------------------------------------
# The published values themselves
# ---------------------------------------------------------------------------


def test_thresholds_match_their_published_values():
    """The whole claim of this module is that these are literature values, so
    a silent edit to one is a correctness bug, not a tuning decision."""
    assert THRESHOLDS["wmc_very_high"].value == 47  # Lanza & Marinescu, VERY HIGH
    assert THRESHOLDS["wmc_below_31"].value == 31
    assert THRESHOLDS["class_length_high"].value == 130
    assert THRESHOLDS["method_length_long"].value == 65
    assert THRESHOLDS["method_cc_high"].value == 10  # McCabe
    assert THRESHOLDS["atfd_high"].value == 5  # ATFD > FEW
    assert THRESHOLDS["noam_nopa_gt_5"].value == 5
    assert THRESHOLDS["noam_nopa_gt_8"].value == 8
    # Fractions, stored exactly rather than rounded.
    assert THRESHOLDS["woc_low"].value == 1 / 3
    assert THRESHOLDS["laa_low"].value == 1 / 3
    # TCC < 1/3 inverted onto our LCOM.
    assert THRESHOLDS["lcom_high"].value == 2 / 3


def test_every_threshold_cites_a_known_source():
    for key, threshold in THRESHOLDS.items():
        assert threshold.source_key in CITATIONS, f"{key} cites an unknown source"


def test_rule_of_thumb_values_are_not_attributed_to_a_paper():
    """A number nobody published must not borrow a citation. These two are
    ordinary practice and are tagged as such."""
    assert THRESHOLDS["method_count_high"].source_key == "common_practice"
    assert THRESHOLDS["method_length_moderate"].source_key == "common_practice"


def test_proxy_metrics_are_flagged_and_explained():
    for key in ("atfd_high", "laa_low"):
        threshold = THRESHOLDS[key]
        assert threshold.is_proxy
        assert threshold.mapping_note, f"{key} is a proxy but explains nothing"


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


GOD_CLASS_SOURCE = "class Big:\n    def __init__(self):\n" + "".join(
    f"        self.f{i} = {i}\n" for i in range(14)
) + "".join(
    # 14 methods, each touching only its own field -> LCOM 1.0, and long
    # enough in total to clear LOC > 130.
    f"    def m{i}(self, v):\n"
    + "".join(f"        x{j} = v + {j}\n" for j in range(8))
    + f"        self.f{i} = v\n"
    + f"        return self.f{i}\n"
    for i in range(14)
)


def test_god_class_rule_fires_on_a_large_incohesive_class():
    derived = _derived(GOD_CLASS_SOURCE, "Big")
    assert derived["method_count"] > 10
    assert derived["lcom"] > 2 / 3
    assert derived["class_length"] > 130
    assert label_for(derived)[0] == "God Class"


def test_clean_small_cohesive_class_is_not_flagged():
    source = (
        "class Counter:\n"
        "    def __init__(self):\n"
        "        self.value = 0\n"
        "    def increment(self):\n"
        "        self.value += 1\n"
        "        return self.value\n"
        "    def reset(self):\n"
        "        self.value = 0\n"
        "        return self.value\n"
    )
    assert label_for(_derived(source, "Counter"))[0] == "Clean"


def test_long_method_rule_fires_on_mccabe_complexity():
    # 16 two-line branches -> CC 17 and 33 lines, clearing both conjuncts of
    # the McCabe arm (v(G) > 10 AND length > 30) without reaching LOC > 65.
    body = "".join(
        f"        if v == {i}:\n            v += {i}\n" for i in range(16)
    )
    source = f"class Proc:\n    def run(self, v):\n{body}        return v\n"
    derived = _derived(source, "Proc")
    assert derived["max_method_cc"] > 10
    assert derived["max_method_length"] > 30
    assert label_for(derived)[0] == "Long Method"


def test_data_class_rule_fires_on_accessor_only_class():
    fields = "".join(f"        self.a{i} = None\n" for i in range(7))
    accessors = "".join(
        f"    def get_a{i}(self):\n        return self.a{i}\n" for i in range(7)
    )
    source = f"class Record:\n    def __init__(self):\n{fields}{accessors}"
    derived = _derived(source, "Record")
    assert derived["woc"] < 1 / 3
    assert derived["noam_plus_nopa"] > 5
    assert label_for(derived)[0] == "Data Class"


def test_precedence_is_stable_and_reports_every_match():
    """A class can trip several rules; the label is the first by precedence
    and the rest stay visible for auditing."""
    label, matched = label_for(_derived(GOD_CLASS_SOURCE, "Big"))
    assert label == "God Class"
    assert matched[0] == "God Class"
    assert [r.label for r in RULES].index("God Class") == 0


# ---------------------------------------------------------------------------
# Accessor detection (WOC/NOAM/NOPA)
# ---------------------------------------------------------------------------


def test_a_getter_with_real_logic_is_not_a_trivial_accessor():
    """WOC has to count validation as real work, or every defensive class
    reads as a Data Class."""
    source = (
        "class Guarded:\n"
        "    def __init__(self):\n"
        "        self.value = None\n"
        "    def get_value(self):\n"
        "        if self.value is None:\n"
        "            raise ValueError('unset')\n"
        "        return self.value\n"
    )
    classes = _classes_from_source(source)
    cls = next(c for c in classes if c.name == "Guarded")
    assert accessor_metrics(cls)["noam"] == 0


def test_docstring_only_getter_still_counts_as_an_accessor():
    source = (
        "class Rec:\n"
        "    def __init__(self):\n"
        "        self.value = 1\n"
        "    def get_value(self):\n"
        '        """The value."""\n'
        "        return self.value\n"
    )
    classes = _classes_from_source(source)
    cls = next(c for c in classes if c.name == "Rec")
    assert accessor_metrics(cls)["noam"] == 1


# ---------------------------------------------------------------------------
# Regressions found while labeling the real corpus
# ---------------------------------------------------------------------------


def test_sibling_exception_classes_do_not_read_as_feature_envy():
    """Regression: a file of exception subclasses gave a SIX-LINE class an
    ATFD of 41, purely because every sibling defines __init__ and name-based
    resolution mapped the constructor call onto all of them. Dunder calls are
    inheritance, not envy."""
    siblings = "".join(
        f"class Err{i}(Base):\n"
        f"    def __init__(self, value):\n"
        f"        self.value = value\n"
        f"        Base.__init__(self, value)\n"
        f"    def __str__(self):\n"
        f"        return repr(self.value)\n"
        for i in range(12)
    )
    source = "class Base:\n    def __init__(self, value):\n        self.value = value\n" + siblings

    classes = _classes_from_source(source)
    metrics = compute_all_metrics(classes)
    cls = next(c for c in classes if c.name == "Err3")

    envy = envy_metrics(cls, classes)
    assert envy["atfd_proxy"] <= 5
    assert label_for(derive_metrics(metrics["Err3"], cls, classes))[0] != "Feature Envy"


def test_self_calls_do_not_count_as_foreign():
    """A call to your own method resolves outward under name matching if any
    other class defines the same name."""
    source = (
        "class A:\n"
        "    def helper(self):\n"
        "        return 1\n"
        "    def run(self):\n"
        "        return self.helper()\n"
        "class B:\n"
        "    def helper(self):\n"
        "        return 2\n"
    )
    classes = _classes_from_source(source)
    cls = next(c for c in classes if c.name == "A")
    assert envy_metrics(cls, classes)["atfd_proxy"] == 0


def test_genuine_envy_is_still_detected():
    """The dunder/self/base exclusions must not silence real envy."""
    provider = (
        "class Provider:\n"
        "    def __init__(self):\n"
        + "".join(f"        self.v{i} = {i}\n" for i in range(8))
        + "".join(f"    def get_v{i}(self):\n        return self.v{i}\n" for i in range(8))
    )
    envier = (
        "class Envier:\n"
        "    def process(self, p):\n"
        "        total = 0\n"
        + "".join(f"        total += p.get_v{i}()\n" for i in range(8))
        + "        return total\n"
    )
    classes = _classes_from_source(provider + envier)
    cls = next(c for c in classes if c.name == "Envier")
    envy = envy_metrics(cls, classes)
    assert envy["atfd_proxy"] > 5
    assert envy["laa"] < 1 / 3


def test_test_paths_are_recognised_by_directory_and_filename():
    """Regression: 14 of 37 Data Class rows were test fixtures whose CLASS
    names look ordinary (`FakeObject`) but whose paths do not."""
    assert is_test_path("horizon/test/tests/tables.py")
    assert is_test_path("pkg/tests/helpers.py")
    assert is_test_path("pkg/test_client.py")
    assert is_test_path("pkg/client_test.py")
    assert is_test_path("pkg/conftest.py")
    assert is_test_path(r"pkg\testing\fixtures.py")  # windows separators

    assert not is_test_path("pkg/client.py")
    assert not is_test_path("pkg/latest/protest.py")  # substring, not a path part
    assert not is_test_path("contest/manager.py")


def test_widening_scope_makes_cross_file_envy_visible():
    """Fix 1: at file scope an envier and its provider in DIFFERENT files
    cannot see each other, which is why the rule found almost nothing."""
    provider_classes = _classes_from_source(
        "class Provider:\n"
        "    def __init__(self):\n"
        + "".join(f"        self.v{i} = {i}\n" for i in range(8))
        + "".join(f"    def get_v{i}(self):\n        return self.v{i}\n" for i in range(8))
    )
    envier_classes = _classes_from_source(
        "class Envier:\n"
        "    def process(self, p):\n"
        "        total = 0\n"
        + "".join(f"        total += p.get_v{i}()\n" for i in range(8))
        + "        return total\n"
    )
    envier = next(c for c in envier_classes if c.name == "Envier")

    # File scope: the provider is invisible, so nothing resolves.
    narrow = build_index([summarize_class(c, "envier_file") for c in envier_classes])
    assert envy_from_summary(summarize_class(envier, "envier_file"), narrow)["atfd_proxy"] == 0

    # Shared scope: the same calls now resolve to the provider.
    wide = build_index(
        [summarize_class(c, "corpus") for c in provider_classes + envier_classes]
    )
    assert envy_from_summary(summarize_class(envier, "corpus"), wide)["atfd_proxy"] > 5


def test_atfd_counts_distinct_names_not_owner_pairs():
    """Widening scope must not turn one call into +N ATFD just because N
    unrelated projects define a method by that name."""
    crowd = []
    for i in range(30):
        crowd.extend(
            _classes_from_source(f"class Holder{i}:\n    def process(self):\n        return {i}\n")
        )
    caller = _classes_from_source(
        "class Caller:\n    def go(self, other):\n        return other.process()\n"
    )
    cls = next(c for c in caller if c.name == "Caller")

    index = build_index([summarize_class(c, "corpus") for c in crowd + caller])
    # One distinct foreign name, not thirty owner pairs.
    assert envy_from_summary(summarize_class(cls, "corpus"), index)["atfd_proxy"] == 1


def test_inherited_behaviour_lifts_woc_off_the_data_class_rule():
    """Fix 2: sklearn's ComplementNB defines only property accessors while
    fit/predict come from its base, and body-scoped WOC called it a Data
    Class."""
    source = (
        "class Base:\n"
        "    def fit(self, x, y):\n"
        "        for i in x:\n"
        "            if i:\n"
        "                y += i\n"
        "        return y\n"
        "    def predict(self, x):\n"
        "        return [i for i in x]\n"
        "    def score(self, x):\n"
        "        return len(x)\n"
        "    def partial_fit(self, x):\n"
        "        return self.fit(x, 0)\n"
        "    def transform(self, x):\n"
        "        return [i * 2 for i in x]\n"
        "class Derived(Base):\n"
        "    def __init__(self):\n"
        + "".join(f"        self.a{i} = None\n" for i in range(7))
        + "".join(f"    def get_a{i}(self):\n        return self.a{i}\n" for i in range(7))
    )
    classes = _classes_from_source(source)
    derived_cls = next(c for c in classes if c.name == "Derived")
    index = build_index([summarize_class(c, "corpus", "same.py") for c in classes])
    summary = summarize_class(derived_cls, "corpus", "same.py")

    assert body_scoped_woc(summary)["woc"] < 1 / 3  # the old, wrong reading
    resolved = woc_from_summary(summary, index)
    assert resolved["woc"] > 1 / 3  # real behaviour is inherited
    assert resolved["inheritance_status"] == "resolved"


def test_unresolvable_base_falls_back_and_says_so():
    """A base class outside the corpus must not be silently invented."""
    classes = _classes_from_source(
        "class Orphan(SomethingExternal):\n"
        "    def __init__(self):\n"
        "        self.a = None\n"
        "    def get_a(self):\n"
        "        return self.a\n"
    )
    cls = next(c for c in classes if c.name == "Orphan")
    index = build_index([summarize_class(c, "corpus", "f.py") for c in classes])
    result = woc_from_summary(summarize_class(cls, "corpus", "f.py"), index)

    assert result["inheritance_status"] == "unresolved"
    assert result["woc"] == body_scoped_woc(summarize_class(cls, "corpus", "f.py"))["woc"]


def test_ambiguous_base_name_is_not_guessed():
    """Ten classes named `Base` across ten projects say nothing about which
    one this class extends."""
    a = _classes_from_source("class Base:\n    def work(self):\n        return 1\n")
    b = _classes_from_source("class Base:\n    def other(self):\n        return 2\n")
    child = _classes_from_source(
        "class Child(Base):\n    def __init__(self):\n        self.v = None\n"
        "    def get_v(self):\n        return self.v\n"
    )
    summaries = (
        [summarize_class(c, "corpus", "a.py") for c in a]
        + [summarize_class(c, "corpus", "b.py") for c in b]
        + [summarize_class(c, "corpus", "child.py") for c in child]
    )
    index = build_index(summaries)
    cls = next(c for c in child if c.name == "Child")
    result = woc_from_summary(summarize_class(cls, "corpus", "child.py"), index)
    assert result["inheritance_status"] == "unresolved"


def test_test_classes_are_recognised():
    """37% of the first pass's God Class labels were TestCases -- they break
    cohesion metrics by design."""
    source = (
        "class TestThing(unittest.TestCase):\n"
        "    def test_a(self):\n"
        "        assert True\n"
        "class ThingTests(TestCase):\n"
        "    def test_b(self):\n"
        "        assert True\n"
        "class Production:\n"
        "    def run(self):\n"
        "        return 1\n"
    )
    classes = {c.name: c for c in _classes_from_source(source)}
    assert is_test_class(classes["TestThing"])
    assert is_test_class(classes["ThingTests"])
    assert not is_test_class(classes["Production"])


# ---------------------------------------------------------------------------
# Benchmarks (the `why` output)
# ---------------------------------------------------------------------------


def test_benchmarks_report_measured_against_published():
    derived = _derived(GOD_CLASS_SOURCE, "Big")
    benchmarks = benchmarks_for(derived, "God Class")

    assert benchmarks
    by_metric = {b.metric: b for b in benchmarks}
    assert by_metric["lcom"].threshold == 2 / 3
    assert by_metric["lcom"].exceeds
    assert by_metric["lcom"].source_short.startswith("Lanza")
    # The sentence form is what the report prints.
    assert "published threshold" in by_metric["lcom"].as_sentence()


def test_benchmarks_skip_metrics_that_were_not_computed():
    """Without a ClassInfo there is no WOC, and a Data Class benchmark must
    not appear against a fabricated value."""
    classes = _classes_from_source("class X:\n    def a(self):\n        return 1\n")
    metrics = compute_all_metrics(classes)
    derived = derive_metrics(metrics["X"])  # no cls, no all_classes

    assert "woc" not in derived
    assert all(b.metric != "woc" for b in benchmarks_for(derived, "Data Class"))


def test_unknown_smell_falls_back_to_core_benchmarks():
    derived = _derived(GOD_CLASS_SOURCE, "Big")
    benchmarks = benchmarks_for(derived, "Duplicate Code")
    assert {b.metric for b in benchmarks} == {"lcom", "wmc", "class_length", "max_method_cc"}
