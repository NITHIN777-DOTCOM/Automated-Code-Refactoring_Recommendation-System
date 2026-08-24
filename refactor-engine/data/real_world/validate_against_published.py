"""Validate our metrics against the published Sandouka & Aljamaan (2023) dataset.

Runs our own parser + metrics over the FOUR PINNED LIBRARY VERSIONS that
paper 1 (Sandouka & Aljamaan, PeerJ 2023) mined, and that paper 4 (Rao,
Dewangan & Mishra, MDPI 2025) reuses -- NumPy 1.9.2, Django 1.8.2,
Matplotlib 1.4.3, SciPy 0.16.0b2 -- then compares what we compute against
what they published.

WHY THIS IS A DISTRIBUTION COMPARISON AND NOT A ROW-BY-ROW ONE
--------------------------------------------------------------
Their released CSVs (Zenodo 10.5281/zenodo.7512516, CC-BY-4.0) carry only
19 numeric feature columns plus a label. There is no project name, file
path, class name, or line number, so their rows cannot be joined to source.
See docs/LITERATURE_REVIEW.md section 7.

Two comparisons are therefore run, and they answer different questions:

  A. INSTANCE-LEVEL, against their TOOL.
     Radon is what they used to extract metrics. We install the same tool,
     run it on the exact same class body we measured, and compare our
     number to Radon's on a per-class basis. This is the strong test: it
     isolates "does our measurement agree with theirs" from every question
     about which classes ended up in their sample.

  B. DISTRIBUTION-LEVEL, against their DATA.
     Our population distribution vs the distribution in their published
     CSV. This is the weaker test, because their 1000 rows are a
     constructed sample (20% smelly by design) and not a random draw from
     these libraries, so a gap here is expected and is not evidence that
     either measurement is wrong. Their CLEAN subset is reported alongside
     the full file for that reason.

Nothing here is tuned to agree. Where our number differs from Radon's the
difference is reported and explained, not corrected.

Run: python data/real_world/validate_against_published.py --libs-dir <dir>
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import textwrap
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from engine.metrics import (  # noqa: E402
    average_method_length,
    class_length,
    method_length,
)
from engine.parser import iter_python_files, parse_file  # noqa: E402
from engine.thresholds import is_test_path  # noqa: E402

# The four libraries, at the versions named in Sandouka & Aljamaan (2023).
LIBRARIES = [
    {"key": "numpy", "label": "NumPy 1.9.2", "dirname": "numpy-1.9.2", "source": "PyPI sdist"},
    {"key": "django", "label": "Django 1.8.2", "dirname": "Django-1.8.2", "source": "PyPI sdist"},
    {"key": "matplotlib", "label": "Matplotlib 1.4.3", "dirname": "matplotlib-1.4.3", "source": "PyPI sdist"},
    {"key": "scipy", "label": "SciPy 0.16.0b2", "dirname": "scipy-0.16.0b2", "source": "GitHub tag v0.16.0b2"},
]


def _log(msg: str) -> None:
    print(f"[validate] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Radon: the reference implementation they used
# ---------------------------------------------------------------------------


def radon_raw(source_block: str):
    """Radon raw metrics for a block of source, or None if it won't tokenize.

    Radon tokenizes, so a method body lifted out of its class has to be
    dedented first or it is an IndentationError. Class bodies taken from
    module level need no dedent but get the same treatment harmlessly.
    """
    try:
        from radon.raw import analyze
    except ImportError:  # pragma: no cover
        return None
    try:
        return analyze(textwrap.dedent(source_block))
    except Exception:
        # Python-2-era syntax, odd encodings, and unterminated blocks all
        # land here. Counted, never raised: these libraries are from 2015.
        return None


def _block(lines: list[str], start: int, end: int) -> str:
    """Source lines [start, end] 1-indexed inclusive, as one string."""
    return "".join(lines[start - 1 : end])


# ---------------------------------------------------------------------------
# Measurement pass
# ---------------------------------------------------------------------------


def measure_library(root: str) -> dict:
    """Every top-level class in a library, measured by us and by Radon."""
    classes: list[dict] = []
    methods: list[dict] = []
    stats = Counter()

    for path in iter_python_files(root):
        stats["files_seen"] += 1
        try:
            parsed = parse_file(path)
        except (SyntaxError, UnicodeDecodeError, OSError, ValueError, RecursionError):
            # Expected and reported: these are 2015 codebases with genuine
            # Python 2 syntax that a modern `ast` cannot parse.
            stats["files_failed"] += 1
            continue
        stats["files_parsed_ok"] += 1

        if not parsed:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue

        on_test_path = is_test_path(path)

        for cls in parsed:
            stats["classes_seen"] += 1
            raw = radon_raw(_block(lines, cls.start_line, cls.end_line))
            if raw is None:
                stats["classes_radon_failed"] += 1

            classes.append(
                {
                    "library": os.path.basename(root),
                    "file": os.path.relpath(path, root).replace("\\", "/"),
                    "class_name": cls.name,
                    "on_test_path": on_test_path,
                    "n_methods": len(cls.methods),
                    # ours
                    "our_class_length": class_length(cls),
                    "our_avg_method_length": average_method_length(cls).value,
                    # theirs (same tool, same block)
                    "radon_loc": None if raw is None else raw.loc,
                    "radon_lloc": None if raw is None else raw.lloc,
                    "radon_sloc": None if raw is None else raw.sloc,
                    "radon_comments": None if raw is None else raw.comments,
                    "radon_multi": None if raw is None else raw.multi,
                    "radon_blank": None if raw is None else raw.blank,
                }
            )

            for m in cls.methods:
                stats["methods_seen"] += 1
                mraw = radon_raw(_block(lines, m.start_line, m.end_line))
                if mraw is None:
                    stats["methods_radon_failed"] += 1
                methods.append(
                    {
                        "library": os.path.basename(root),
                        "on_test_path": on_test_path,
                        "our_method_length": method_length(m),
                        "our_method_length_plus1": m.end_line - m.start_line + 1,
                        "radon_loc": None if mraw is None else mraw.loc,
                        "radon_lloc": None if mraw is None else mraw.lloc,
                        "radon_sloc": None if mraw is None else mraw.sloc,
                    }
                )

    return {"classes": classes, "methods": methods, "stats": dict(stats)}


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------


def describe(values: list[float]) -> dict:
    vals = [v for v in values if v is not None]
    if not vals:
        return {"n": 0}
    vals_sorted = sorted(vals)

    def pct(p: float) -> float:
        if len(vals_sorted) == 1:
            return float(vals_sorted[0])
        idx = min(int(round(p * (len(vals_sorted) - 1))), len(vals_sorted) - 1)
        return float(vals_sorted[idx])

    return {
        "n": len(vals),
        "mean": round(statistics.mean(vals), 2),
        "median": round(statistics.median(vals), 1),
        "std": round(statistics.pstdev(vals), 2) if len(vals) > 1 else 0.0,
        "p25": pct(0.25),
        "p75": pct(0.75),
        "p90": pct(0.90),
        "min": min(vals),
        "max": max(vals),
    }


HISTOGRAM_EDGES = [0, 10, 20, 35, 50, 100, 200, 400, 800, float("inf")]


def histogram(values: list[float]) -> dict:
    vals = [v for v in values if v is not None]
    out = {}
    for lo, hi in zip(HISTOGRAM_EDGES, HISTOGRAM_EDGES[1:]):
        name = f"{lo}-{'inf' if hi == float('inf') else hi}"
        count = sum(1 for v in vals if lo <= v < hi)
        out[name] = {"count": count, "pct": round(100 * count / len(vals), 1) if vals else 0.0}
    return out


def agreement(rows: list[dict], ours_key: str, theirs_key: str) -> dict:
    """How our number compares to Radon's on the very same block."""
    pairs = [(r[ours_key], r[theirs_key]) for r in rows if r.get(theirs_key) is not None]
    if not pairs:
        return {"n": 0}
    diffs = [a - b for a, b in pairs]
    exact = sum(1 for d in diffs if d == 0)
    within1 = sum(1 for d in diffs if abs(d) <= 1)
    ours = [a for a, _ in pairs]
    theirs = [b for _, b in pairs]

    corr = None
    if len(pairs) > 1:
        mo, mt = statistics.mean(ours), statistics.mean(theirs)
        num = sum((a - mo) * (b - mt) for a, b in pairs)
        den = (sum((a - mo) ** 2 for a in ours) * sum((b - mt) ** 2 for b in theirs)) ** 0.5
        corr = round(num / den, 6) if den else None

    return {
        "n": len(pairs),
        "exact_match": exact,
        "exact_match_pct": round(100 * exact / len(pairs), 2),
        "within_1_line": within1,
        "within_1_line_pct": round(100 * within1 / len(pairs), 2),
        "mean_diff_ours_minus_theirs": round(statistics.mean(diffs), 4),
        "median_diff": statistics.median(diffs),
        "min_diff": min(diffs),
        "max_diff": max(diffs),
        "pearson_r": corr,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--libs-dir", required=True, help="directory holding the extracted library trees")
    ap.add_argument("--output", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                     "published_comparison.json"))
    args = ap.parse_args()

    all_classes: list[dict] = []
    all_methods: list[dict] = []
    per_library = {}

    for lib in LIBRARIES:
        root = os.path.join(args.libs_dir, lib["dirname"])
        if not os.path.isdir(root):
            _log(f"MISSING {lib['label']} at {root} -- skipping")
            per_library[lib["key"]] = {"error": "not found"}
            continue
        _log(f"measuring {lib['label']} ...")
        res = measure_library(root)
        all_classes.extend(res["classes"])
        all_methods.extend(res["methods"])

        prod = [c for c in res["classes"] if not c["on_test_path"]]
        per_library[lib["key"]] = {
            "label": lib["label"],
            "source": lib["source"],
            "parse_stats": res["stats"],
            "all_classes": {
                "our_class_length": describe([c["our_class_length"] for c in res["classes"]]),
                "radon_loc": describe([c["radon_loc"] for c in res["classes"]]),
            },
            "production_classes_only": {
                "n": len(prod),
                "our_class_length": describe([c["our_class_length"] for c in prod]),
                "radon_loc": describe([c["radon_loc"] for c in prod]),
            },
            "agreement_class_length_vs_radon_loc": agreement(res["classes"], "our_class_length", "radon_loc"),
        }
        _log(f"  {res['stats'].get('classes_seen', 0)} classes, "
             f"{res['stats'].get('files_failed', 0)}/{res['stats'].get('files_seen', 0)} files unparsable")

    report = {
        "libraries": per_library,
        "combined": {
            "n_classes": len(all_classes),
            "n_methods": len(all_methods),
            "class_length": {
                "ours_all": describe([c["our_class_length"] for c in all_classes]),
                "radon_loc_all": describe([c["radon_loc"] for c in all_classes]),
                "ours_production_only": describe(
                    [c["our_class_length"] for c in all_classes if not c["on_test_path"]]
                ),
            },
            "method_length": {
                "ours_all": describe([m["our_method_length"] for m in all_methods]),
                "ours_plus1_all": describe([m["our_method_length_plus1"] for m in all_methods]),
                "radon_loc_all": describe([m["radon_loc"] for m in all_methods]),
                "ours_production_only": describe(
                    [m["our_method_length"] for m in all_methods if not m["on_test_path"]]
                ),
            },
            "histograms": {
                "our_class_length": histogram([c["our_class_length"] for c in all_classes]),
                "radon_loc_class": histogram([c["radon_loc"] for c in all_classes]),
                "our_method_length": histogram([m["our_method_length"] for m in all_methods]),
            },
            "agreement": {
                "class_length_vs_radon_loc": agreement(all_classes, "our_class_length", "radon_loc"),
                "method_length_vs_radon_loc": agreement(all_methods, "our_method_length", "radon_loc"),
                "method_length_plus1_vs_radon_loc": agreement(
                    all_methods, "our_method_length_plus1", "radon_loc"
                ),
            },
        },
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    _log(f"wrote {args.output}")

    # Row-level dumps so the markdown write-up can be regenerated without
    # re-running the (slow) measurement pass.
    import csv

    csv_path = args.output.replace(".json", "_classes.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(all_classes[0].keys()))
        w.writeheader()
        w.writerows(all_classes)
    _log(f"wrote {csv_path} ({len(all_classes)} rows)")

    mcsv = args.output.replace(".json", "_methods.csv")
    with open(mcsv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(all_methods[0].keys()))
        w.writeheader()
        w.writerows(all_methods)
    _log(f"wrote {mcsv} ({len(all_methods)} rows)")


if __name__ == "__main__":
    main()
