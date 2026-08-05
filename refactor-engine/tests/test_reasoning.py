import os

import pytest

from engine.cli.html_report import render_report
from engine.ml.explain import explain_prediction
from engine.metrics import compute_all_metrics
from engine.parser import parse_file
from engine.reasoning import ClassNotFoundError, explain_file

SAMPLE_REPO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_repo")
COUPLED_FILE = os.path.join(SAMPLE_REPO, "coupled.py")
GOD_CLASS_FILE = os.path.join(SAMPLE_REPO, "god_class.py")
CLEAN_FILE = os.path.join(SAMPLE_REPO, "clean.py")


def _metrics_for(file_path, class_name):
    return compute_all_metrics(parse_file(file_path))[class_name]


# ---------------------------------------------------------------------------
# Local attribution
# ---------------------------------------------------------------------------


def test_attribution_agrees_with_the_prediction_it_explains():
    """An explanation of a different label than the one the pipeline reports
    would be worse than no explanation at all."""
    result = explain_prediction(_metrics_for(COUPLED_FILE, "Order"))

    assert result["label"] == "Feature Envy"
    assert 0.0 < result["confidence"] <= 1.0


def test_attribution_covers_every_feature_exactly_once():
    result = explain_prediction(_metrics_for(GOD_CLASS_FILE, "ReportManager"))

    features = [c["feature"] for c in result["contributions"]]
    assert sorted(features) == sorted(
        [
            "avg_cyclomatic_complexity",
            "avg_method_length",
            "cbo",
            "class_length",
            "dit",
            "fan_in",
            "fan_out",
            "lcom",
        ]
    )


def test_zero_importance_features_are_marked_unused_and_ranked_last():
    """cbo/dit/fan_in are constant across the training data, so the forest
    never learned to use them. Ranking one of them as a "top reason" would
    attribute a decision to a feature the model provably ignored."""
    result = explain_prediction(_metrics_for(COUPLED_FILE, "Order"))

    unused = [c["feature"] for c in result["contributions"] if not c["used_by_model"]]
    assert set(unused) == {"cbo", "dit", "fan_in"}

    used_positions = [i for i, c in enumerate(result["contributions"]) if c["used_by_model"]]
    unused_positions = [i for i, c in enumerate(result["contributions"]) if not c["used_by_model"]]
    assert max(used_positions) < min(unused_positions)


def test_outward_reaching_class_is_explained_by_its_outward_reach():
    """Order drives PaymentProcessor and does little else, so fan_out has to
    come out on top -- if it doesn't, the attribution isn't measuring what it
    claims to."""
    result = explain_prediction(_metrics_for(COUPLED_FILE, "Order"))

    top = result["contributions"][0]
    assert top["feature"] == "fan_out"
    assert top["support"] > 0


# ---------------------------------------------------------------------------
# Reasoning records
# ---------------------------------------------------------------------------


def test_explain_file_covers_every_flagged_class_and_no_clean_ones():
    reasoning = explain_file(COUPLED_FILE)

    assert {c.name for c in reasoning.classes} == {"Order", "PaymentProcessor"}
    assert reasoning.total_classes == 2
    assert reasoning.flagged_count == 2
    assert all(c.is_flagged for c in reasoning.classes)


def test_clean_file_yields_nothing_to_explain():
    reasoning = explain_file(CLEAN_FILE)

    assert reasoning.classes == []
    assert reasoning.flagged_count == 0
    assert reasoning.total_classes >= 1


def test_clean_fallback_explains_the_clean_classes_instead_of_nothing():
    """`why --output` on a clean file still has to produce the file it was
    asked for; a scripted step that writes nothing on success is broken."""
    reasoning = explain_file(CLEAN_FILE, clean_fallback=True)

    assert reasoning.classes
    assert all(not c.is_flagged for c in reasoning.classes)
    assert all(c.smell == "Clean" for c in reasoning.classes)


def test_class_filter_narrows_to_one_class():
    reasoning = explain_file(COUPLED_FILE, class_name="Order")

    assert [c.name for c in reasoning.classes] == ["Order"]
    # The file-level counts still describe the file, not the filtered view.
    assert reasoning.total_classes == 2


def test_unknown_class_name_raises_with_the_available_names():
    with pytest.raises(ClassNotFoundError) as exc_info:
        explain_file(COUPLED_FILE, class_name="Nope")

    assert exc_info.value.class_name == "Nope"
    assert exc_info.value.available == ["Order", "PaymentProcessor"]


def test_every_measurement_leads_with_words_not_numbers():
    """The readability rule this feature exists to satisfy: a finding must be
    a sentence. A bare "0.71" as the headline would defeat the point."""
    reasoning = explain_file(GOD_CLASS_FILE)
    measurements = reasoning.classes[0].measurements

    assert len(measurements) == 8
    for measurement in measurements:
        assert measurement.finding
        assert not measurement.finding[0].isdigit() or measurement.metric in ("fan_in",)
        assert measurement.detail  # the raw number, kept as supporting detail


def test_cohesion_smell_reports_the_clusters_that_produced_the_suggestion():
    reasoning = explain_file(GOD_CLASS_FILE)
    graph = reasoning.classes[0].graph

    assert graph.strategy == "clusters"
    assert len(graph.clusters) >= 2
    assert "__init__" in graph.excluded
    for cluster in graph.clusters:
        assert cluster["methods"]
        assert cluster["why"]
    # Edges have to carry the field names, not just a count -- "why were these
    # grouped" is unanswerable without them.
    shared = {f for e in graph.edges for f in e["shared_fields"]}
    assert "rows" in shared


def test_feature_envy_reports_call_attribution_rather_than_clusters():
    """The graph is built for every class but only *clusters* the cohesion
    smells. Reporting a cluster as the reason for a move-method suggestion
    would misdescribe how the pipeline reached it."""
    reasoning = explain_file(COUPLED_FILE, class_name="Order")
    graph = reasoning.classes[0].graph

    assert graph.strategy == "calls"
    assert graph.clusters == []
    targets = {item["target"] for item in graph.call_attribution}
    assert targets == {"PaymentProcessor"}


def test_top_reason_and_short_suggestion_stay_terminal_sized():
    """The terminal summary is capped at a few lines per class no matter how
    many classes a file has, which only holds if these two stay short."""
    for cls in explain_file(GOD_CLASS_FILE).classes + explain_file(COUPLED_FILE).classes:
        assert cls.top_reason
        assert len(cls.top_reason) < 200
        assert len(cls.short_suggestion) < 120
        assert "\n" not in cls.top_reason + cls.short_suggestion


def test_suggestion_sentences_restate_the_actual_suggestion():
    reasoning = explain_file(COUPLED_FILE, class_name="Order")
    sentences = reasoning.classes[0].suggestion_sentences

    assert any("checkout" in s and "PaymentProcessor" in s for s in sentences)


def test_long_method_reports_the_block_split_rather_than_clusters():
    reasoning = explain_file(os.path.join(os.path.dirname(SAMPLE_REPO), "orders.py"))
    cls = reasoning.classes[0]

    assert cls.smell == "Long Method"
    assert cls.graph.strategy == "blocks"
    assert cls.graph.blocks
    for block in cls.graph.blocks:
        assert block["lines"][0] <= block["lines"][1]
        assert block["description"]


def test_threshold_finding_is_not_credited_to_the_classifier(tmp_path):
    """Long Parameter List is a plain counting rule layered on top of the
    model, not a model output. Explaining it as a model decision would be a
    false claim about how the finding was reached -- and there is no
    confidence score to quote for it either."""
    source = tmp_path / "wide.py"
    source.write_text(
        "class Widget:\n"
        "    def __init__(self):\n"
        "        self.parts = []\n"
        "    def configure(self, a, b, c, d, e):\n"
        "        self.parts = [a, b, c, d, e]\n"
        "        return self.parts\n",
        encoding="utf-8",
    )

    cls = explain_file(str(source)).classes[0]

    assert cls.smell == "Long Parameter List"
    assert not cls.model.decided_by_model
    assert cls.model.model_label == "Clean"
    assert "not what flagged this class" in cls.model.non_model_reason
    # "100% confidence" would attach a model's certainty to something the
    # model had no part in.
    assert "confidence" not in cls.headline_metric
    assert "classifier" in cls.top_reason or "count" in cls.top_reason


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------


def test_report_is_self_contained():
    """No CDN, no external stylesheet, no script tag -- the report has to
    render from a file:// URL with the network off."""
    document = render_report(explain_file(GOD_CLASS_FILE))
    # The SVG namespace is an identifier, not an address -- nothing fetches it.
    fetchable = document.replace('xmlns="http://www.w3.org/2000/svg"', "")

    assert "<script" not in document.lower()
    assert "http://" not in fetchable
    assert "https://" not in fetchable


def test_report_contains_all_four_reasoning_steps_per_class():
    document = render_report(explain_file(COUPLED_FILE))

    for heading in (
        "What was measured",
        "Why the classifier reached this conclusion",
        "What to do about it",
    ):
        assert document.count(heading) == 2  # two flagged classes in this file
    assert document.count("<svg") == 2


def test_every_reasoning_section_is_collapsed_by_default():
    """The report is a summary you can drill into, not a wall of text. Four
    sections per class, every one of them shut until asked."""
    document = render_report(explain_file(COUPLED_FILE))

    assert document.count("<details>") == 8  # 4 sections x 2 classes
    assert "<details open" not in document
    assert document.count("<summary>") == 8


def test_collapsed_sections_each_carry_a_one_line_takeaway():
    """A closed section still has to say something -- otherwise skimming the
    page tells you only that four sections exist."""
    import re

    document = render_report(explain_file(GOD_CLASS_FILE))
    takeaways = re.findall(r'<span class="s-take">(.*?)</span>', document, re.S)

    assert len(takeaways) == 4
    for takeaway in takeaways:
        assert takeaway.strip()
        assert len(takeaway) < 130


def test_report_uses_only_the_four_palette_colours():
    """Depth of hierarchy comes from type and space, not from extra hues.
    Alpha variants of the ink are allowed; new hues are not."""
    import re

    document = render_report(explain_file(GOD_CLASS_FILE))

    hexes = {c.upper() for c in re.findall(r"#[0-9A-Fa-f]{6}", document)}
    assert hexes <= {"#112D4E", "#3F72AF", "#DBE2EF", "#F9F7F7"}

    # Any rgba() must be the ink at reduced alpha, never a different colour.
    for rgba in re.findall(r"rgba\(([^)]*)\)", document):
        red, green, blue = (part.strip() for part in rgba.split(",")[:3])
        assert (red, green, blue) == ("17", "45", "78")


def test_report_has_no_dark_theme():
    """The page is light, full stop -- no media query flipping it to a
    near-black background on a reader whose OS happens to be in dark mode."""
    document = render_report(explain_file(GOD_CLASS_FILE))

    assert "prefers-color-scheme" not in document
    assert "--paper: #F9F7F7" in document


def test_multi_class_report_gets_a_contents_list_and_single_class_does_not():
    linked = render_report(explain_file(COUPLED_FILE))
    assert 'class="contents"' in linked
    assert 'href="#class-Order"' in linked
    assert 'id="class-Order"' in linked

    alone = render_report(explain_file(GOD_CLASS_FILE))
    assert 'class="contents"' not in alone


def test_cluster_members_sit_together_on_the_ring():
    """Groups are marked by an arc rather than by colour, which only works if
    each group's methods are adjacent -- four dots scattered around the ring
    would argue against the grouping the picture exists to show."""
    from engine.cli.html_report import _ordered_nodes

    graph = explain_file(GOD_CLASS_FILE).classes[0].graph
    order, spans = _ordered_nodes(graph)

    assert len(spans) == len(graph.clusters)
    assert sorted(order) == sorted(n["name"] for n in graph.nodes)
    for cluster, (start, end) in zip(graph.clusters, spans):
        assert set(order[start : end + 1]) == set(cluster["methods"])


def test_report_escapes_content_rather_than_interpolating_it():
    document = render_report(explain_file(GOD_CLASS_FILE))

    # A class name lands in an id, a heading and an anchor; none of those may
    # be a place where source-derived text can open a tag.
    assert "ReportManager" in document
    assert "<ReportManager" not in document


def test_written_report_round_trips_to_disk(tmp_path):
    from engine.cli.html_report import write_report

    target = tmp_path / "nested" / "report.html"
    write_report(explain_file(COUPLED_FILE), str(target))

    document = target.read_text(encoding="utf-8")
    assert document.startswith("<!doctype html>")
    assert "Order" in document and "PaymentProcessor" in document
