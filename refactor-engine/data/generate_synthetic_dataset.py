"""Generate a synthetic, labeled code-smell dataset by running our own
Phase 1 engine (parse_file + compute_all_metrics) over programmatically
generated Python classes.

Path taken: SYNTHETIC FALLBACK (not a real public dataset).

Why not a real dataset (MLCQ / Fontana et al.):
- Both MLCQ and the Fontana/Qualitas Corpus dataset are built from Java
  source code. Our engine (engine/parser.py) is a Python-only `ast` walker,
  so their precomputed metric columns don't correspond to metrics our own
  extractor could ever produce, and their raw code samples would require
  building an entirely separate Java analyzer to re-derive metrics.
- MLCQ's CSV (MLCQCodeSmellSamples.csv) is reviewer-labeled code snippets,
  not a metrics table — using it would mean re-running analysis on raw
  Java source, which we can't do with a Python-only engine.

So instead: generate synthetic Python classes with a KNOWN, deliberately
engineered smell, run them through our real parser/metrics pipeline (no
hand-fabricated metric values), and label each row with the smell we
built it to exhibit.

Each sample is parsed in ISOLATION (one temp file per sample, not one big
parse_repo() over the whole corpus). This matters for cbo/fan_in/fan_out:
those metrics are computed by name-matching calls against `all_classes`,
and many synthetic classes intentionally reuse generic method names
(e.g. get_value_0) across unrelated samples. Parsing everything together
would create spurious cross-sample coupling. Parsing per-sample keeps
each class's coupling metrics meaningful relative to only its own
purpose-built collaborator(s).

ON DELIBERATE OVERLAP: an earlier version of this generator produced
perfectly separated clusters -- every God Class was extreme (15-25 methods,
~100 lines, one dedicated field per method giving lcom ~0.90). A classifier
trained on that learned "God Class == very large" and misread a realistic
mid-size god class (12 methods, 40 lines, lcom 0.71) as a Data Class at
100% confidence. Two changes address that:

  1. Ranges span realistic-to-extreme, not just extreme. God classes now
     run from ~8 methods / ~35 lines up to ~25 methods / ~115 lines, and
     share fields between methods so lcom lands in a realistic 0.65-0.90
     band rather than pinned near 0.90.
  2. A fraction of every label (BORDERLINE_RATE) is generated as a
     deliberately ambiguous variant -- data classes with real validation
     logic, mild long methods, weak feature envy, non-trivial clean
     classes. These sit between cluster centroids on purpose.

The goal is a training set where confidence scores mean something. Expect
accuracy below 100%; that is the point, not a regression.

KNOWN LIMITATION -- no stateless/utility classes:
Training data contains no stateless/utility classes (LCOM=1.0 from zero
self-access); model may misclassify such classes as God/Data Class due to
out-of-distribution extrapolation. Example: PaymentProcessor (3 methods,
8 lines) -> predicted God Class @ 0.56 confidence.

Every generator here gives its class instance state, so lcom tops out at
0.88 across all 400 rows. A class whose methods never touch `self` scores
lcom == 1.0, above anything the model has seen, and the forest extrapolates
toward the high-lcom labels (God Class / Data Class) regardless of how tiny
the class is. Closing this would mean adding a stateless-utility generator
(module-level helpers, @staticmethod-only classes). Deliberately left open
and documented rather than patched -- see PHASE2_NOTES.md.
"""

from __future__ import annotations

import csv
import os
import random
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.metrics import compute_all_metrics
from engine.parser import parse_file

SAMPLES_PER_LABEL = 80

# Fraction of each label generated as a deliberately ambiguous "borderline"
# variant that sits between cluster centroids. Kept moderate: the goal is
# meaningful uncertainty near the boundaries, not label noise that makes the
# task unlearnable.
BORDERLINE_RATE = 0.3
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "labeled_dataset.csv")

CSV_COLUMNS = [
    "class_name",
    "cbo",
    "lcom",
    "class_length",
    "avg_method_length",
    "avg_cyclomatic_complexity",
    "dit",
    "fan_in",
    "fan_out",
    "label",
]


def make_god_class(idx):
    """Many methods across several unrelated field groups (low cohesion).

    Spans realistic mid-size (~8 methods / ~35 lines) through extreme
    (~25 methods / ~115 lines). Fields are deliberately FEWER than methods
    so that methods share state: giving each method its own dedicated field
    pins lcom near 0.90, which no real god class looks like. Sharing puts
    lcom in a realistic ~0.65-0.90 band that overlaps how an actual
    multi-responsibility class reads.
    """
    class_name = f"GodClass{idx}"

    # A god class does NOT have to contain complex methods. The "bloated
    # manager" shape -- many trivial accessors/renderers sprawled over
    # several unrelated field groups -- is extremely common in real code
    # (sample_repo's ReportManager is exactly this: 12 one-line methods,
    # 6 unrelated fields, 40 lines). Generating only complexity-bearing god
    # classes left avg_method_length >= 3.0 and avg_cyclomatic_complexity
    # >= 1.44 with nothing below, so trivial-method god classes fell
    # entirely outside the learned region and read as Data Class instead.
    trivial_methods = random.random() < 0.4
    # Trivial variants lean larger in method count so that sheer breadth,
    # not per-method complexity, is what separates them from a Data Class.
    n_methods = random.randint(10, 25) if trivial_methods else random.randint(8, 25)
    n_fields = max(3, random.randint(n_methods // 3, n_methods // 2))

    lines = [f"class {class_name}:", "    def __init__(self):"]
    for i in range(n_fields):
        lines.append(f"        self.field_{i} = {i}")

    for i in range(n_methods):
        field = i % n_fields
        if not trivial_methods and i % 4 == 0:
            lines.append(f"    def method_{i}(self, items):")
            lines.append("        total = 0")
            lines.append("        for item in items:")
            lines.append(f"            if item > {i}:")
            lines.append("                total += item")
            lines.append(f"        self.field_{field} = total")
            lines.append("        return total")
        elif trivial_methods:
            # Genuine single-statement accessors, matching how a bloated
            # manager actually reads. A two-statement body (assign THEN
            # return) would floor avg_method_length near 2.1 and still miss
            # real classes like ReportManager, which sits at 1.42.
            if i % 2 == 0:
                lines.append(f"    def set_field_{i}(self, value):")
                lines.append(f"        self.field_{field} = value")
            else:
                lines.append(f"    def get_field_{i}(self):")
                lines.append(f"        return self.field_{field}")
        else:
            lines.append(f"    def method_{i}(self, value):")
            lines.append(f"        self.field_{field} = value")
            lines.append(f"        return self.field_{field}")

    return "\n".join(lines), class_name


def make_long_method(idx, borderline=False):
    """Small class, one method with high cyclomatic complexity/length.

    Borderline variant: a milder offending method PLUS several trivial
    sibling methods. Both changes drag the class-level averages
    (avg_cyclomatic_complexity, avg_method_length) down toward the God
    Class / Clean range, since our features average over all methods --
    a real class with one long method rarely consists of only that method.
    """
    class_name = f"LongMethod{idx}"
    depth = random.randint(2, 4) if borderline else random.randint(4, 8)

    lines = [
        f"class {class_name}:",
        "    def __init__(self):",
        "        self.state = 0",
        "",
        "    def complex_process(self, items, flag_a, flag_b, flag_c):",
        "        total = 0",
    ]
    for i in range(depth):
        lines.append(f"        if items and items[{i % 3}] > {i}:")
        lines.append(f"            total += {i}")
        lines.append("        elif flag_a or flag_b:")
        lines.append(f"            total -= {i}")
        lines.append("        for x in items:")
        lines.append(f"            if x == {i}:")
        lines.append("                total += x")
        lines.append(f"            while flag_c and total < {i}:")
        lines.append("                total += 1")
        lines.append("                flag_c = False")
    lines.append("        self.state = total")
    lines.append("        return total")

    if borderline:
        for i in range(random.randint(3, 6)):
            lines.append("")
            lines.append(f"    def helper_{i}(self):")
            lines.append("        return self.state")

    return "\n".join(lines), class_name


def make_feature_envy(idx, borderline=False):
    """Method that calls another class's getters far more than it uses self.

    Borderline variant: weak envy -- fewer foreign calls, and the envier
    also maintains real state of its own. This lowers fan_out toward the
    range where a legitimately collaborating class lives, which is exactly
    where envy is genuinely hard to call.
    """
    class_name = f"Envier{idx}"
    helper_name = f"Provider{idx}"
    n_fields = random.randint(2, 3) if borderline else random.randint(3, 6)

    helper_lines = [f"class {helper_name}:", "    def __init__(self):"]
    for i in range(n_fields):
        helper_lines.append(f"        self.value_{i} = {i}")
    for i in range(n_fields):
        helper_lines.append(f"    def get_value_{i}(self):")
        helper_lines.append(f"        return self.value_{i}")

    envier_lines = [
        f"class {class_name}:",
        "    def __init__(self):",
        "        self.own_field = 0",
    ]
    if borderline:
        envier_lines.append("        self.own_total = 0")
        envier_lines.append("        self.own_count = 0")

    envier_lines.append("")
    envier_lines.append("    def process(self, provider):")
    envier_lines.append("        total = 0")
    for i in range(n_fields):
        envier_lines.append(f"        total += provider.get_value_{i}()")
    envier_lines.append("        self.own_field = total")
    envier_lines.append("        return total")

    if borderline:
        envier_lines.append("")
        envier_lines.append("    def accumulate(self, amount):")
        envier_lines.append("        self.own_total += amount")
        envier_lines.append("        self.own_count += 1")
        envier_lines.append("        return self.own_total")

    source = "\n".join(helper_lines) + "\n\n\n" + "\n".join(envier_lines)
    return source, class_name


def make_data_class(idx, borderline=False):
    """Trivial getters/setters with no real logic.

    Borderline variant: setters carry genuine validation and the class gains
    a small derived-value method. That lifts avg_cyclomatic_complexity above
    the flat 1.0 of a pure data holder, pushing it toward Clean and mid-size
    God Class -- the region where "is this actually a data class?" is a real
    judgement call rather than a threshold check.
    """
    class_name = f"DataRecord{idx}"
    n_fields = random.randint(4, 8)

    lines = [f"class {class_name}:", "    def __init__(self):"]
    for i in range(n_fields):
        lines.append(f"        self.attr_{i} = None")

    for i in range(n_fields):
        lines.append(f"    def get_attr_{i}(self):")
        lines.append(f"        return self.attr_{i}")
        if borderline:
            lines.append(f"    def set_attr_{i}(self, value):")
            lines.append("        if value is None:")
            lines.append(f"            raise ValueError('attr_{i} is required')")
            lines.append(f"        self.attr_{i} = value")
        else:
            lines.append(f"    def set_attr_{i}(self, value):")
            lines.append(f"        self.attr_{i} = value")

    if borderline:
        lines.append("    def summary(self):")
        lines.append("        parts = []")
        lines.append("        for value in [self.attr_0, self.attr_1]:")
        lines.append("            if value is not None:")
        lines.append("                parts.append(value)")
        lines.append("        return parts")

    return "\n".join(lines), class_name


def make_clean_class(idx, borderline=False):
    """Small, cohesive, low-complexity class -- the "no smell" baseline.

    Borderline variant: still cohesive and reasonably sized, but with real
    branching and a few more methods, so "clean" is not trivially separable
    by "avg_cyclomatic_complexity == 1.0 and tiny".
    """
    class_name = f"Clean{idx}"
    lines = [
        f"class {class_name}:",
        "    def __init__(self):",
        "        self.value = 0",
        "",
        "    def increment(self):",
        "        self.value += 1",
        "        return self.value",
        "",
        "    def decrement(self):",
        "        self.value -= 1",
        "        return self.value",
        "",
        "    def reset(self):",
        "        self.value = 0",
        "        return self.value",
    ]

    if borderline:
        lines += [
            "",
            "    def adjust(self, amount, allow_negative):",
            "        if amount == 0:",
            "            return self.value",
            "        if not allow_negative and self.value + amount < 0:",
            "            self.value = 0",
            "        else:",
            "            self.value += amount",
            "        return self.value",
            "",
            "    def clamp(self, ceiling):",
            "        if self.value > ceiling:",
            "            self.value = ceiling",
            "        return self.value",
        ]

    return "\n".join(lines), class_name


# God Class has no separate borderline variant: its widened method-count and
# field-sharing ranges already produce a continuum from mid-size to extreme,
# so the small end of that range IS the ambiguous case.
GENERATORS = {
    "God Class": make_god_class,
    "Long Method": make_long_method,
    "Feature Envy": make_feature_envy,
    "Data Class": make_data_class,
    "Clean": make_clean_class,
}
LABELS_WITH_BORDERLINE = {"Long Method", "Feature Envy", "Data Class", "Clean"}


def _row_for_class(cls_metrics, class_name, label):
    methods = cls_metrics["methods"]
    method_lengths = [m["length"] for m in methods.values()]
    method_complexities = [m["cyclomatic_complexity"] for m in methods.values()]

    avg_method_length = sum(method_lengths) / len(method_lengths) if method_lengths else 0.0
    avg_cyclomatic_complexity = (
        sum(method_complexities) / len(method_complexities) if method_complexities else 0.0
    )

    return {
        "class_name": class_name,
        "cbo": cls_metrics["cbo"],
        "lcom": cls_metrics["lcom"],
        "class_length": cls_metrics["class_length"],
        "avg_method_length": avg_method_length,
        "avg_cyclomatic_complexity": avg_cyclomatic_complexity,
        "dit": cls_metrics["depth_of_inheritance"],
        "fan_in": cls_metrics["fan_in"],
        "fan_out": cls_metrics["fan_out"],
        "label": label,
    }


def generate_dataset():
    random.seed(42)
    tmp_dir = tempfile.mkdtemp(prefix="refactor_engine_synthetic_")
    rows = []
    counter = 0

    try:
        for label, generator in GENERATORS.items():
            n_borderline = (
                int(SAMPLES_PER_LABEL * BORDERLINE_RATE)
                if label in LABELS_WITH_BORDERLINE
                else 0
            )
            for i in range(SAMPLES_PER_LABEL):
                counter += 1
                if label in LABELS_WITH_BORDERLINE:
                    source, class_name = generator(counter, borderline=i < n_borderline)
                else:
                    source, class_name = generator(counter)

                file_path = os.path.join(tmp_dir, f"sample_{counter}.py")
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(source)

                classes = parse_file(file_path)
                metrics = compute_all_metrics(classes)
                rows.append(_row_for_class(metrics[class_name], class_name, label))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return rows


def write_csv(rows, output_path):
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    rows = generate_dataset()
    write_csv(rows, OUTPUT_PATH)

    counts = {}
    for row in rows:
        counts[row["label"]] = counts.get(row["label"], 0) + 1

    print(f"Generated {len(rows)} labeled classes -> {OUTPUT_PATH}")
    for label, count in counts.items():
        print(f"  {label}: {count}")


if __name__ == "__main__":
    main()
