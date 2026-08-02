import os

from engine.pipeline import analyze_repo

SAMPLE_REPO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_repo")


def test_analyze_repo_does_not_crash_on_sample_repo():
    result = analyze_repo(SAMPLE_REPO)

    assert "PaymentProcessor" in result
    entry = result["PaymentProcessor"]
    assert set(entry.keys()) == {"file_path", "metrics", "predicted_smell", "confidence", "suggestions"}
    assert isinstance(entry["suggestions"], list)


def test_report_manager_gets_non_clean_prediction_with_a_suggestion():
    result = analyze_repo(SAMPLE_REPO)

    entry = result["ReportManager"]
    assert entry["predicted_smell"] != "Clean"
    assert len(entry["suggestions"]) >= 1
    assert all(s["type"] == "extract_class" for s in entry["suggestions"])


def test_order_feature_envy_gets_unsupported_smell_type_suggestion():
    result = analyze_repo(SAMPLE_REPO)

    entry = result["Order"]
    assert entry["predicted_smell"] == "Feature Envy"
    assert len(entry["suggestions"]) >= 1
    assert entry["suggestions"][0]["type"] == "unsupported_smell_type"
    assert "Feature Envy detected" in entry["suggestions"][0]["note"]


def test_counter_clean_class_gets_no_suggestions():
    result = analyze_repo(SAMPLE_REPO)

    entry = result["Counter"]
    assert entry["suggestions"] == []


def test_empty_repo_returns_empty_dict(tmp_path):
    result = analyze_repo(str(tmp_path))

    assert result == {}


def test_syntax_error_in_one_file_does_not_crash_the_scan(tmp_path):
    (tmp_path / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    (tmp_path / "clean.py").write_text(
        "class Counter:\n"
        "    def __init__(self):\n"
        "        self.value = 0\n"
        "\n"
        "    def increment(self):\n"
        "        self.value += 1\n"
        "        return self.value\n",
        encoding="utf-8",
    )

    result = analyze_repo(str(tmp_path))

    assert "Counter" in result
