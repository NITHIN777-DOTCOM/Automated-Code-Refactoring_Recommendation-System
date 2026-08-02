import os

from engine.pipeline import analyze_file, analyze_path, analyze_repo

SAMPLE_REPO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_repo")
GOD_CLASS_FILE = os.path.join(SAMPLE_REPO, "god_class.py")


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


def test_analyze_file_handles_a_single_py_file():
    result = analyze_file(GOD_CLASS_FILE)

    assert "ReportManager" in result
    entry = result["ReportManager"]
    assert entry["file_path"] == GOD_CLASS_FILE
    assert entry["predicted_smell"] != "Clean"
    assert len(entry["suggestions"]) >= 1


def test_analyze_path_dispatches_to_file_or_repo():
    file_result = analyze_path(GOD_CLASS_FILE)
    repo_result = analyze_path(SAMPLE_REPO)

    assert set(file_result.keys()) == {"ReportManager"}
    assert "ReportManager" in repo_result
    assert "Counter" in repo_result


def test_empty_repo_returns_empty_dict(tmp_path):
    result = analyze_repo(str(tmp_path))

    assert result == {}


def test_analyze_path_skips_default_excluded_directories(tmp_path):
    (tmp_path / "messy_code.py").write_text(
        "class Counter:\n"
        "    def __init__(self):\n"
        "        self.value = 0\n"
        "\n"
        "    def increment(self):\n"
        "        self.value += 1\n"
        "        return self.value\n",
        encoding="utf-8",
    )

    venv_dir = tmp_path / "venv" / "Lib" / "site-packages" / "somepkg"
    venv_dir.mkdir(parents=True)
    (venv_dir / "vendored.py").write_text(
        "class VendoredThing:\n"
        "    def method_a(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )

    result = analyze_path(str(tmp_path))

    assert "Counter" in result
    assert "VendoredThing" not in result


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
