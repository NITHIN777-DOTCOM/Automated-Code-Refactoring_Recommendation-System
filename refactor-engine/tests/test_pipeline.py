import os

from engine.pipeline import analyze_file, analyze_path, analyze_repo

SAMPLE_REPO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_repo")
GOD_CLASS_FILE = os.path.join(SAMPLE_REPO, "god_class.py")


def test_analyze_repo_does_not_crash_on_sample_repo():
    result = analyze_repo(SAMPLE_REPO)

    assert "PaymentProcessor" in result["classes"]
    entry = result["classes"]["PaymentProcessor"]
    assert set(entry.keys()) == {"file_path", "metrics", "predicted_smell", "confidence", "suggestions"}
    assert isinstance(entry["suggestions"], list)


def test_report_manager_gets_non_clean_prediction_with_a_suggestion():
    result = analyze_repo(SAMPLE_REPO)

    entry = result["classes"]["ReportManager"]
    assert entry["predicted_smell"] != "Clean"
    assert len(entry["suggestions"]) >= 1
    assert all(s["type"] == "extract_class" for s in entry["suggestions"])


def test_order_feature_envy_gets_move_method_suggestion():
    """Order.checkout/cancel both drive PaymentProcessor, so Feature Envy must
    now name that class as the destination rather than falling through to the
    generic "no strategy" note."""
    result = analyze_repo(SAMPLE_REPO)

    entry = result["classes"]["Order"]
    assert entry["predicted_smell"] == "Feature Envy"

    moves = [s for s in entry["suggestions"] if s["type"] == "move_method"]
    assert {s["source_method"] for s in moves} == {"checkout", "cancel"}
    assert all(s["target_class"] == "PaymentProcessor" for s in moves)
    assert all(s["external_references"] > s["own_data_uses"] for s in moves)
    assert not any(s["type"] == "unsupported_smell_type" for s in entry["suggestions"])


def test_counter_clean_class_gets_no_suggestions():
    result = analyze_repo(SAMPLE_REPO)

    entry = result["classes"]["Counter"]
    assert entry["suggestions"] == []


def test_analyze_file_handles_a_single_py_file():
    result = analyze_file(GOD_CLASS_FILE)

    assert "ReportManager" in result["classes"]
    entry = result["classes"]["ReportManager"]
    assert entry["file_path"] == GOD_CLASS_FILE
    assert entry["predicted_smell"] != "Clean"
    assert len(entry["suggestions"]) >= 1


def test_analyze_path_dispatches_to_file_or_repo():
    file_result = analyze_path(GOD_CLASS_FILE)
    repo_result = analyze_path(SAMPLE_REPO)

    assert set(file_result["classes"].keys()) == {"ReportManager"}
    assert "ReportManager" in repo_result["classes"]
    assert "Counter" in repo_result["classes"]


def test_empty_repo_returns_empty_dict(tmp_path):
    result = analyze_repo(str(tmp_path))

    assert result == {"classes": {}, "unused_imports": {}}


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

    assert "Counter" in result["classes"]
    assert "VendoredThing" not in result["classes"]


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

    assert "Counter" in result["classes"]


def test_method_with_six_parameters_is_flagged_as_long_parameter_list(tmp_path):
    (tmp_path / "booking.py").write_text(
        "class Booking:\n"
        "    def __init__(self):\n"
        "        self.state = 'new'\n"
        "\n"
        "    def schedule(self, name, date, start_time, end_time, location, notes):\n"
        "        self.state = 'scheduled'\n"
        "        return self.state\n",
        encoding="utf-8",
    )

    result = analyze_path(str(tmp_path))

    entry = result["classes"]["Booking"]
    assert entry["predicted_smell"] == "Long Parameter List"
    assert entry["confidence"] == 1.0
    long_param_suggestions = [s for s in entry["suggestions"] if s["type"] == "long_parameter_list"]
    assert len(long_param_suggestions) == 1
    assert long_param_suggestions[0]["methods"] == ["schedule"]


def test_method_with_three_parameters_is_not_flagged(tmp_path):
    (tmp_path / "booking.py").write_text(
        "class Booking:\n"
        "    def __init__(self):\n"
        "        self.state = 'new'\n"
        "\n"
        "    def schedule(self, name, date, location):\n"
        "        self.state = 'scheduled'\n"
        "        return self.state\n",
        encoding="utf-8",
    )

    result = analyze_path(str(tmp_path))

    entry = result["classes"]["Booking"]
    assert entry["predicted_smell"] != "Long Parameter List"
    assert all(s["type"] != "long_parameter_list" for s in entry["suggestions"])


def test_long_method_dispatches_to_extract_method_strategy(tmp_path):
    """Long Method must now route to the statement-block strategy rather than
    falling through to the generic "not implemented" note."""
    (tmp_path / "orders.py").write_text(
        "class OrderProcessor:\n"
        "    def __init__(self):\n"
        "        self.tax_rate = 0.08\n"
        "\n"
        "    def process_order(self, customer, items, coupon_code):\n"
        "        errors = []\n"
        "        if not customer:\n"
        "            errors.append('customer is required')\n"
        "        if not items:\n"
        "            errors.append('need items')\n"
        "        if errors:\n"
        "            raise ValueError('; '.join(errors))\n"
        "\n"
        "        subtotal = 0.0\n"
        "        for item in items:\n"
        "            subtotal += item['price']\n"
        "        discount = 0.0\n"
        "        if coupon_code == 'SAVE10':\n"
        "            discount = subtotal * 0.10\n"
        "        total = (subtotal - discount) * (1 + self.tax_rate)\n"
        "\n"
        "        lines = []\n"
        "        lines.append('Customer: ' + customer)\n"
        "        lines.append('Total: ' + str(total))\n"
        "        receipt = ', '.join(lines)\n"
        "        return receipt\n",
        encoding="utf-8",
    )

    result = analyze_path(str(tmp_path))
    entry = result["classes"]["OrderProcessor"]

    assert entry["predicted_smell"] == "Long Method"
    method_suggestions = [s for s in entry["suggestions"] if s["type"] == "extract_method"]
    assert len(method_suggestions) >= 2
    assert all(s["source_method"] == "process_order" for s in method_suggestions)
    assert not any(s["type"] == "unsupported_smell_type" for s in entry["suggestions"])


def test_indivisible_long_method_falls_back_to_explanatory_note():
    """When no split point exists the pipeline must say so, rather than
    returning an empty list that renders as class-extraction phrasing."""
    from engine.models import ClassInfo, MethodInfo
    from engine.pipeline import _suggestions_for_smell

    empty_class = ClassInfo(name="Opaque", file_path="<test>", methods=[
        MethodInfo(name="run", class_name="Opaque", start_line=1, end_line=2)
    ])

    suggestions = _suggestions_for_smell(empty_class, "Long Method", [empty_class])

    assert len(suggestions) == 1
    assert suggestions[0]["type"] == "unsupported_smell_type"
    assert "Long Method detected" in suggestions[0]["note"]


def test_duplicated_methods_get_a_duplicate_code_note(tmp_path):
    # Both methods write self.count so the class is cohesive enough for the
    # classifier to call it Clean -- otherwise the ML label would be the thing
    # under test rather than the duplication check.
    (tmp_path / "exporter.py").write_text(
        "class Exporter:\n"
        "    def __init__(self):\n"
        "        self.count = 0\n"
        "\n"
        "    def export_csv(self, rows):\n"
        "        lines = []\n"
        "        for row in rows:\n"
        "            lines.append(','.join(row))\n"
        "        self.count = len(lines)\n"
        "        return self.count\n"
        "\n"
        "    def export_tsv(self, records):\n"
        "        output = []\n"
        "        for record in records:\n"
        "            output.append('\\t'.join(record))\n"
        "        self.count = len(output)\n"
        "        return self.count\n",
        encoding="utf-8",
    )

    result = analyze_path(str(tmp_path))
    entry = result["classes"]["Exporter"]

    duplicate_notes = [s for s in entry["suggestions"] if s["type"] == "duplicate_code"]
    assert len(duplicate_notes) == 1

    pairs = duplicate_notes[0]["pairs"]
    assert len(pairs) == 1
    assert set(pairs[0]["methods"]) == {"export_csv", "export_tsv"}
    assert pairs[0]["similarity"] >= 0.8

    # An otherwise-Clean class must surface as flagged, or the finding never
    # reaches the report at all.
    assert entry["predicted_smell"] == "Duplicate Code"
    # Not a borrowed model probability and not the 1.0 a hard check earns --
    # the similarity of the pair it came from.
    assert entry["confidence"] == pairs[0]["similarity"]


def test_duplicate_code_note_states_that_it_is_a_heuristic(tmp_path):
    """False positives are likelier here than for the other smells, so the
    output has to say so where the finding is, not only in `explain`."""
    (tmp_path / "exporter.py").write_text(
        "class Exporter:\n"
        "    def export_csv(self, rows, path):\n"
        "        lines = []\n"
        "        for row in rows:\n"
        "            lines.append(','.join(row))\n"
        "        return len(lines)\n"
        "\n"
        "    def export_tsv(self, records, destination):\n"
        "        output = []\n"
        "        for record in records:\n"
        "            output.append('\\t'.join(record))\n"
        "        return len(output)\n",
        encoding="utf-8",
    )

    result = analyze_path(str(tmp_path))
    note = [s for s in result["classes"]["Exporter"]["suggestions"] if s["type"] == "duplicate_code"][0]["note"]

    assert "structural similarity heuristic" in note
    assert "not exact-text duplication" in note


def test_genuinely_different_methods_get_no_duplicate_code_note(tmp_path):
    (tmp_path / "mixed.py").write_text(
        "class Mixed:\n"
        "    def collect(self, rows):\n"
        "        total = 0\n"
        "        for row in rows:\n"
        "            total += row['amount']\n"
        "        return total\n"
        "\n"
        "    def describe(self, user):\n"
        "        label = user.name.upper()\n"
        "        if not label:\n"
        "            raise ValueError('no name')\n"
        "        while label.endswith('.'):\n"
        "            label = label[:-1]\n"
        "        return {'label': label, 'id': user.id, 'active': True}\n",
        encoding="utf-8",
    )

    result = analyze_path(str(tmp_path))
    entry = result["classes"]["Mixed"]

    assert all(s["type"] != "duplicate_code" for s in entry["suggestions"])
    assert entry["predicted_smell"] != "Duplicate Code"


def test_long_parameter_list_keeps_the_headline_over_duplicate_code(tmp_path):
    """Both checks can fire on one class. The unambiguous one should be the
    label; the heuristic one rides along as an extra note."""
    (tmp_path / "booking.py").write_text(
        "class Booking:\n"
        "    def __init__(self):\n"
        "        self.entries = []\n"
        "\n"
        "    def schedule(self, name, date, start_time, end_time, location, notes):\n"
        "        entry = {}\n"
        "        entry['name'] = name\n"
        "        entry['date'] = date\n"
        "        self.entries.append(entry)\n"
        "        return entry\n"
        "\n"
        "    def rebook(self, title, day, begins, ends, place, comments):\n"
        "        record = {}\n"
        "        record['name'] = title\n"
        "        record['date'] = day\n"
        "        self.entries.append(record)\n"
        "        return record\n",
        encoding="utf-8",
    )

    result = analyze_path(str(tmp_path))
    entry = result["classes"]["Booking"]

    assert entry["predicted_smell"] == "Long Parameter List"
    assert entry["confidence"] == 1.0
    assert any(s["type"] == "duplicate_code" for s in entry["suggestions"])


def test_unused_import_is_reported_at_file_scope(tmp_path):
    (tmp_path / "utils.py").write_text(
        "import os\n"
        "import json\n"
        "\n"
        "def load(path):\n"
        "    return json.load(open(path))\n",
        encoding="utf-8",
    )

    result = analyze_path(str(tmp_path))
    file_path = str(tmp_path / "utils.py")

    assert file_path in result["unused_imports"]
    names = [item["name"] for item in result["unused_imports"][file_path]]
    assert names == ["os"]


def test_file_with_all_imports_used_reports_none(tmp_path):
    (tmp_path / "utils.py").write_text(
        "import json\n"
        "\n"
        "def load(path):\n"
        "    return json.load(open(path))\n",
        encoding="utf-8",
    )

    result = analyze_path(str(tmp_path))

    assert result["unused_imports"] == {}


def test_unused_imports_are_reported_even_for_files_with_no_classes(tmp_path):
    """File-scoped, not class-scoped: a pure script with zero classes must
    still get checked."""
    (tmp_path / "script.py").write_text(
        "import sys\n"
        "\n"
        "def main():\n"
        "    return 1\n",
        encoding="utf-8",
    )

    result = analyze_path(str(tmp_path))
    file_path = str(tmp_path / "script.py")

    assert result["classes"] == {}
    assert file_path in result["unused_imports"]
    assert result["unused_imports"][file_path][0]["name"] == "sys"
