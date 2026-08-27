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
    """LAA is still an approximation (aggregated per class, not per method)."""
    threshold = THRESHOLDS["laa_low"]
    assert threshold.is_proxy
    assert threshold.mapping_note, "laa_low is a proxy but explains nothing"


def test_atfd_and_fdp_are_no_longer_proxies():
    """Regression guard for C.2: ATFD used to be proxied by fan_out and FDP
    was dropped from the rule entirely. Both are measured directly now, so
    neither may claim proxy status -- while still documenting the stage-1
    receiver-name limitation."""
    for key in ("atfd_high", "fdp_few"):
        threshold = THRESHOLDS[key]
        assert not threshold.is_proxy, f"{key} should be a real metric now"
        assert threshold.mapping_note, f"{key} must still document its limitations"
        # The published cutoff is now applied to the metric it was written
        # for, rather than to a stand-in with a different meaning.
        assert threshold.metric in ("atfd", "fdp")


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


def test_meta_style_class_with_only_class_body_attributes_is_a_data_class():
    """The blind spot this guards: a nested `class Meta:` (Django's pattern,
    and its equivalents elsewhere) that declares fields directly in the class
    body, with no `__init__` and no methods at all. Before class-body
    assignments were counted, NOPA read 0 regardless of how many attributes
    the body declared, which forced WOC to 1.0 and failed the rule's
    WOC < 1/3 gate no matter what. With 0 methods, WMC is 0 (< 31), so this
    is a clean example of the small arm: NOPA+NOAM > 5 AND WMC < 31."""
    source = (
        "class Meta:\n"
        "    ordering = ['-created']\n"
        "    verbose_name = 'entry'\n"
        "    verbose_name_plural = 'entries'\n"
        "    app_label = 'blog'\n"
        "    db_table = 'blog_entry'\n"
        "    unique_together = ('slug', 'author')\n"
    )
    derived = _derived(source, "Meta")
    assert derived["nopa"] == 6
    assert derived["woc"] < 1 / 3
    assert derived["noam_plus_nopa"] > 5
    assert label_for(derived)[0] == "Data Class"


def test_class_body_attributes_alongside_real_method_logic_stay_clean():
    """The other half of the same boundary: a class that happens to declare
    attributes directly in its body but ALSO does substantial work in its
    methods must not be flagged just because it has class-body attributes.
    WOC is about the proportion of the public interface that is functional,
    not about whether any non-self-assigned attribute exists."""
    source = (
        "class Pipeline:\n"
        "    default_batch_size = 32\n"
        "    retry_limit = 3\n"
        "\n"
        "    def __init__(self):\n"
        "        self.queue = []\n"
        "\n"
        "    def process(self, items):\n"
        "        batch = []\n"
        "        for item in items:\n"
        "            if item is None:\n"
        "                continue\n"
        "            batch.append(item)\n"
        "            if len(batch) >= self.default_batch_size:\n"
        "                self.queue.append(list(batch))\n"
        "                batch = []\n"
        "        if batch:\n"
        "            self.queue.append(batch)\n"
        "        return len(self.queue)\n"
        "\n"
        "    def retry(self, fn):\n"
        "        attempts = 0\n"
        "        while attempts < self.retry_limit:\n"
        "            try:\n"
        "                return fn()\n"
        "            except Exception:\n"
        "                attempts += 1\n"
        "        raise RuntimeError('exhausted retries')\n"
    )
    derived = _derived(source, "Pipeline")
    assert derived["nopa"] >= 2  # default_batch_size, retry_limit still counted as fields
    assert label_for(derived)[0] != "Data Class"


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
    assert envy["atfd"] <= 5
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
    assert envy_metrics(cls, classes)["atfd"] == 0


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
    assert envy["atfd"] > 5
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


def test_real_atfd_is_scope_invariant():
    """Real ATFD reads the class's OWN foreign attribute accesses, so unlike
    the old fan_out proxy it cannot change with how many other classes are
    visible. The proxy scored 0 for this envier at file scope and >5 at
    corpus scope -- same source, different answer. Both must now agree."""
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

    narrow = envy_from_summary(summarize_class(envier, "envier_file"))
    wide = envy_from_summary(summarize_class(envier, "corpus"))

    assert narrow == wide
    assert narrow["atfd"] > 5
    # All eight reads land on the single parameter `p`.
    assert narrow["fdp"] == 1
    assert narrow["fdp_concentration"] == 1.0


def test_atfd_is_not_inflated_by_unrelated_classes_sharing_a_method_name():
    """The proxy resolved a bare call name against every class in scope, so
    30 unrelated classes defining `process` could each be counted. Real ATFD
    counts one (receiver, attribute) pair regardless of who else exists."""
    crowd = []
    for i in range(30):
        crowd.extend(
            _classes_from_source(f"class Holder{i}:\n    def process(self):\n        return {i}\n")
        )
    caller = _classes_from_source(
        "class Caller:\n    def go(self, other):\n        return other.process()\n"
    )
    cls = next(c for c in caller if c.name == "Caller")

    build_index([summarize_class(c, "corpus") for c in crowd + caller])
    assert envy_from_summary(summarize_class(cls, "corpus"))["atfd"] == 1


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


# ---------------------------------------------------------------------------
# Real ATFD / FDP (C.2) -- the three-way distinction the metric exists to make
# ---------------------------------------------------------------------------

# (a) Genuinely envious: many reads, all on ONE receiver.
ENVIOUS_SOURCE = (
    "class Order:\n"
    "    def __init__(self):\n"
    + "".join(f"        self.f{i} = {i}\n" for i in range(8))
    + "".join(f"    def get_f{i}(self):\n        return self.f{i}\n" for i in range(8))
    + "class ReportBuilder:\n"
    "    def build(self, order):\n"
    "        total = 0\n"
    + "".join(f"        total += order.get_f{i}()\n" for i in range(8))
    + "        return total\n"
)

# (b) Parameter-parsing, ORCIDOAuth2-shaped: every receiver is a builtin, so
#     nothing counts as foreign data at all.
BUILTIN_PARSER_SOURCE = (
    "class PayloadParser:\n"
    "    def parse(self, payload: dict, name: str):\n"
    "        parts = []\n"
    "        parts.append(payload.get('a'))\n"
    "        parts.append(payload.get('b'))\n"
    "        parts.append(payload.get('c'))\n"
    "        parts.append(payload.get('d'))\n"
    "        parts.append(payload.get('e'))\n"
    "        parts.append(payload.get('f'))\n"
    "        return name.strip().upper().title()\n"
)

# (c) Scattered: plenty of foreign reads, but spread thinly over many
#     unrelated objects -- dispersed coupling, not envy.
SCATTERED_SOURCE = (
    "class Coordinator:\n"
    "    def run(self, a, b, c, d, e, f, g, h):\n"
    "        return (\n"
    "            a.alpha() + b.bravo() + c.charlie() + d.delta()\n"
    "            + e.echo() + f.foxtrot() + g.golf() + h.hotel()\n"
    "        )\n"
)


def _envy(source, name):
    classes = _classes_from_source(source)
    cls = next(c for c in classes if c.name == name)
    return cls, classes, envy_metrics(cls, classes)


def test_genuinely_envious_class_is_detected_and_concentrated():
    cls, classes, envy = _envy(ENVIOUS_SOURCE, "ReportBuilder")

    assert envy["atfd"] == 8
    assert envy["fdp"] == 1
    assert envy["fdp_concentration"] == 1.0
    assert envy["laa"] < 1 / 3

    metrics = compute_all_metrics(classes)
    assert label_for(derive_metrics(metrics[id(cls)], cls, classes))[0] == "Feature Envy"


def test_builtin_receivers_do_not_count_as_foreign_data():
    """The ORCIDOAuth2 shape: a class whose methods only pick apart dicts and
    strings handed in as parameters is doing ordinary Python, not envying
    another class. Before the builtin filter these reads inflated ATFD."""
    cls, classes, envy = _envy(BUILTIN_PARSER_SOURCE, "PayloadParser")

    assert envy["atfd"] == 0
    assert envy["fdp"] == 0

    metrics = compute_all_metrics(classes)
    assert label_for(derive_metrics(metrics[id(cls)], cls, classes))[0] != "Feature Envy"


def test_scattered_foreign_reads_are_not_feature_envy():
    """High ATFD but spread across 8 providers. Lanza & Marinescu's FDP <= FEW
    conjunct exists precisely to keep this out: there is no single class to
    move a method into, so calling it Feature Envy would produce advice that
    cannot be followed."""
    cls, classes, envy = _envy(SCATTERED_SOURCE, "Coordinator")

    assert envy["atfd"] == 8
    assert envy["fdp"] == 8
    assert envy["fdp_concentration"] < 0.2
    assert not THRESHOLDS["fdp_few"].exceeded_by(envy["fdp"])

    metrics = compute_all_metrics(classes)
    assert label_for(derive_metrics(metrics[id(cls)], cls, classes))[0] != "Feature Envy"


def test_suggester_names_the_envied_receiver_when_reads_concentrate():
    """C.2's payoff for the report: concentrated foreign reads now yield a
    named destination instead of 'the envied class may live outside the
    scanned path'."""
    from engine.suggester import suggest_method_moves

    cls, classes, _ = _envy(ENVIOUS_SOURCE, "ReportBuilder")
    moves = [s for s in suggest_method_moves(cls, classes) if s["type"] == "move_method"]

    assert moves, "concentrated envy should produce a move_method suggestion"
    assert moves[0]["target_class"] == "Order"


def test_suggester_refuses_to_name_a_target_when_reads_are_scattered():
    from engine.suggester import suggest_method_moves

    cls, classes, _ = _envy(SCATTERED_SOURCE, "Coordinator")
    named = [s for s in suggest_method_moves(cls, classes) if s["type"] == "move_method"]

    assert not named, "scattered reads must not produce a confident destination"
