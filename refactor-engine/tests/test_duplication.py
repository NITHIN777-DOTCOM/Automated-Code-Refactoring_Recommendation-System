import textwrap

from engine.duplication import (
    MIN_BODY_STATEMENTS,
    compute_method_similarity,
    find_duplicate_pairs,
    normalize_method,
)
from engine.parser import parse_file


def _write_and_parse(tmp_path, source, filename="sample.py"):
    file_path = tmp_path / filename
    file_path.write_text(textwrap.dedent(source), encoding="utf-8")
    return parse_file(str(file_path))


def _methods(tmp_path, source, filename="sample.py"):
    classes = _write_and_parse(tmp_path, source, filename)
    return {m.name: m for m in classes[0].methods}


def test_same_logic_different_variable_names_scores_as_identical(tmp_path):
    """The whole point of normalizing: renaming every local in a copied method
    must not lower its similarity to the original."""
    methods = _methods(tmp_path, """
    class Reports:
        def build_sales_report(self, rows):
            total = 0
            for row in rows:
                total += row['amount']
            average = total / len(rows)
            return {'total': total, 'average': average}

        def build_refund_report(self, records):
            summed = 0
            for record in records:
                summed += record['value']
            mean = summed / len(records)
            return {'total': summed, 'average': mean}
    """)

    similarity = compute_method_similarity(methods["build_sales_report"], methods["build_refund_report"])

    assert similarity == 1.0


def test_genuinely_different_methods_score_low(tmp_path):
    methods = _methods(tmp_path, """
    class Mixed:
        def collect(self, rows):
            total = 0
            for row in rows:
                total += row['amount']
            return total

        def describe(self, user):
            label = user.name.upper()
            if not label:
                raise ValueError('no name')
            while label.endswith('.'):
                label = label[:-1]
            return {'label': label, 'id': user.id, 'active': True}
    """)

    similarity = compute_method_similarity(methods["collect"], methods["describe"])

    assert similarity < 0.8


def test_near_identical_pair_is_flagged_and_different_pair_is_not(tmp_path):
    classes = _write_and_parse(tmp_path, """
    class Exporter:
        def export_csv(self, rows, path):
            lines = []
            for row in rows:
                lines.append(','.join(row))
            with open(path, 'w') as handle:
                handle.write('\\n'.join(lines))
            return len(lines)

        def export_tsv(self, records, destination):
            output = []
            for record in records:
                output.append('\\t'.join(record))
            with open(destination, 'w') as target:
                target.write('\\n'.join(output))
            return len(output)

        def load_config(self, path):
            if not path:
                raise ValueError('path required')
            settings = {}
            settings['path'] = path
            settings['mode'] = 'read'
            return settings
    """)

    pairs = find_duplicate_pairs(classes[0])

    assert len(pairs) == 1
    method_a, method_b, similarity = pairs[0]
    assert {method_a, method_b} == {"export_csv", "export_tsv"}
    assert similarity >= 0.8


def test_class_with_no_similar_methods_returns_no_pairs(tmp_path):
    classes = _write_and_parse(tmp_path, """
    class Mixed:
        def collect(self, rows):
            total = 0
            for row in rows:
                total += row['amount']
            return total

        def describe(self, user):
            label = user.name.upper()
            if not label:
                raise ValueError('no name')
            while label.endswith('.'):
                label = label[:-1]
            return {'label': label, 'id': user.id, 'active': True}
    """)

    assert find_duplicate_pairs(classes[0]) == []


def test_trivial_getters_are_not_reported_despite_identical_structure(tmp_path):
    """One-line getters have identical ASTs by construction. The similarity
    score says so honestly; find_duplicate_pairs() still must not report them,
    or every real finding drowns in this noise."""
    classes = _write_and_parse(tmp_path, """
    class Person:
        def get_name(self):
            return self.name

        def get_age(self):
            return self.age
    """)
    methods = {m.name: m for m in classes[0].methods}

    assert compute_method_similarity(methods["get_name"], methods["get_age"]) == 1.0
    assert find_duplicate_pairs(classes[0]) == []


def test_threshold_is_respected(tmp_path):
    """A pair similar enough to clear a low bar but not the default must
    appear only at the lower threshold."""
    classes = _write_and_parse(tmp_path, """
    class Handler:
        def handle_first(self, items):
            results = []
            for item in items:
                results.append(item.value)
            return results

        def handle_second(self, entries):
            output = []
            for entry in entries:
                if entry.enabled:
                    output.append(entry.value)
            total = len(output)
            return {'items': output, 'count': total}
    """)
    cls = classes[0]

    assert find_duplicate_pairs(cls, threshold=0.8) == []
    assert len(find_duplicate_pairs(cls, threshold=0.4)) == 1


def test_normalization_strips_names_but_keeps_control_flow(tmp_path):
    methods = _methods(tmp_path, """
    class Sample:
        def run(self, values):
            total = 0
            for value in values:
                total += value
            return total
    """)

    tokens = normalize_method(methods["run"])

    assert "For" in tokens
    assert "Return" in tokens
    # Identifiers are never emitted -- only node type names and brackets.
    assert "total" not in tokens
    assert "values" not in tokens


def test_docstrings_are_ignored(tmp_path):
    """Two methods must not look alike merely because both are documented."""
    methods = _methods(tmp_path, """
    class Sample:
        def documented(self, values):
            \"\"\"Adds the values up.\"\"\"
            total = 0
            for value in values:
                total += value
            return total

        def undocumented(self, numbers):
            running = 0
            for number in numbers:
                running += number
            return running
    """)

    assert normalize_method(methods["documented"]) == normalize_method(methods["undocumented"])


def test_method_shorter_than_the_statement_floor_is_skipped(tmp_path):
    source_lines = "\n".join(f"            self.value_{i} = {i}" for i in range(MIN_BODY_STATEMENTS))
    classes = _write_and_parse(tmp_path, f"""
    class Sizes:
        def long_enough(self):
{source_lines}

        def also_long_enough(self):
{source_lines}

        def too_short(self):
            self.value_0 = 0
    """)

    pairs = find_duplicate_pairs(classes[0])
    flagged_methods = {name for pair in pairs for name in pair[:2]}

    assert flagged_methods == {"long_enough", "also_long_enough"}
    assert "too_short" not in flagged_methods


def test_method_without_a_parsed_body_scores_zero():
    """SequenceMatcher rates two empty sequences a perfect 1.0 -- an absence of
    evidence must not read as a perfect match."""
    from engine.models import MethodInfo

    empty_a = MethodInfo(name="a", class_name="C", start_line=1, end_line=1)
    empty_b = MethodInfo(name="b", class_name="C", start_line=2, end_line=2)

    assert compute_method_similarity(empty_a, empty_b) == 0.0
