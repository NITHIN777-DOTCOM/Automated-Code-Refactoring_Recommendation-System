"""Label the collected real-world corpus with literature-based thresholds.

The companion to data/generate_synthetic_dataset.py, and its opposite in
method. There, each class was GENERATED to exhibit a known smell and the
label came free. Here the code is real and unlabeled, we have no
hand-verified ground truth for thousands of scraped classes, and
hand-labeling them is not on the table -- so the label comes from published
detection thresholds instead (engine/thresholds.py, which documents every
value's source and every place our metric is a proxy rather than an
equivalent).

Output is a drop-in replacement for data/labeled_dataset.csv: identical
columns, plus a `split` column.

WHAT THIS DOES AND DOES NOT CLAIM
---------------------------------
Threshold labels are not ground truth. They are a reproducible, citable
approximation, and they inherit every disagreement the literature has with
itself about where a cutoff belongs. Two consequences worth stating plainly
because they affect how the retrained model should be read:

  1. A model trained on these labels learns to reproduce the thresholds,
     plus whatever the metric features happen to correlate with. It cannot
     become more accurate than the labeling rule on the rule's own terms.
  2. The distribution is genuinely imbalanced (most real classes are
     clean). That is a property of real code, not a defect to be corrected
     here -- balancing belongs at training time (class weights/resampling),
     where it can be undone, not baked into the dataset.

METRIC SCOPE
------------
compute_all_metrics() derives cbo/fan_in/fan_out relative to whatever class
list it is handed, so scope is a real choice. Two different scopes are used
here on purpose:

  * cbo / fan_in / fan_out, and everything written to the CSV feature
    columns, stay PER FILE. Pooling 4,600 classes from unrelated projects
    would invent coupling between a `Config` in one repo and a `Config` in
    another, and two of the four sources (ETH Py150 and CodeSearchNet) are
    sampled FILES with no surrounding repository, so a wider scope does not
    honestly exist for the bulk of the corpus.

  * The Feature Envy ATFD/LAA proxies use a WIDER scope, selectable with
    --envy-scope. At file scope the rule found 8 classes in the whole
    corpus, because envy is a cross-class smell and most Python files hold
    one class. Widening the lookup is what makes the label learnable. The
    counting rule that makes this safe -- distinct call NAMES, not
    name-x-owner pairs -- is documented in engine.thresholds.envy_from_summary.

  * WOC resolves inherited methods across the whole corpus, because a class
    that inherits its behavior is not a data class no matter which file the
    base lives in.

The run reports Feature Envy counts under every scope so the choice can be
made against numbers rather than assumption.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from engine.metrics import compute_all_metrics  # noqa: E402
from engine.parser import iter_python_files, parse_file  # noqa: E402
from engine.thresholds import (  # noqa: E402
    CLEAN_LABEL,
    RULES,
    as_manifest,
    body_scoped_woc,
    build_index,
    derive_metrics,
    envy_from_summary,
    is_test_class,
    is_test_path,
    label_for,
    summarize_class,
    woc_from_summary,
)

REAL_WORLD_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(REAL_WORLD_DIR, "raw_sources")
OUTPUT_CSV = os.path.join(REAL_WORLD_DIR, "labeled_real_dataset.csv")
AUDIT_JSON = os.path.join(REAL_WORLD_DIR, "labeling_audit.json")

RANDOM_SEED = 42
TEST_SIZE = 0.2
ENVY_SCOPES = ("file", "package", "repo", "corpus")

# Same columns as data/labeled_dataset.csv, plus `split`. Kept in this exact
# order so engine/ml/features.py's FEATURE_COLUMNS lookup works unchanged.
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
    "split",
]

SOURCE_CATEGORIES = {
    "eth_py150": "standard_dataset",
    "git_repos": "git_repos",
    "site_packages": "site_packages",
    "codesearchnet": "codesearchnet",
}


def _log(msg: str) -> None:
    print(f"[label] {msg}", flush=True)


def _relative_parts(file_path: str, raw_dir: str) -> list[str]:
    return os.path.relpath(file_path, raw_dir).replace("\\", "/").split("/")


def _source_category(file_path: str, raw_dir: str) -> str:
    return SOURCE_CATEGORIES.get(_relative_parts(file_path, raw_dir)[0], "unknown")


def _scope_keys(file_path: str, raw_dir: str) -> dict[str, str]:
    """The grouping key for this file under each candidate envy scope.

    "repo" means the independent source unit a class actually ships in, and
    that differs per source: py150 preserves <user>/<repo>, the git clones
    are one directory each, site-packages are one per distribution, and a
    CodeSearchNet entry is a lone extracted function with no project around
    it (so its repo is itself).
    """
    parts = _relative_parts(file_path, raw_dir)
    source = parts[0]

    if source == "eth_py150" and len(parts) >= 3:
        repo = "/".join(parts[:3])
    elif source in ("git_repos", "site_packages") and len(parts) >= 2:
        repo = "/".join(parts[:2])
    else:
        repo = "/".join(parts)

    return {
        "file": file_path,
        "package": os.path.dirname(file_path),
        "repo": repo,
        "corpus": "",
    }


# ---------------------------------------------------------------------------
# Pass 1: parse everything, keeping only lightweight per-class records
# ---------------------------------------------------------------------------


def collect_records(raw_dir: str) -> tuple[list[dict], dict]:
    """Every non-test class in the corpus, as a record with base metrics.

    ASTs are summarized and dropped here (see engine.thresholds.ClassSummary):
    holding ClassInfo objects for the whole corpus so that pass 2 can do
    cross-class lookups would mean holding every method's syntax tree at once.

    Parse failures are counted, not raised -- the corpus deliberately
    includes Python-2-era code that will never parse under a modern `ast`,
    and dropping those files is the existing behavior of parse_repo().
    """
    records: list[dict] = []
    stats = {
        "files_seen": 0,
        "files_parsed_ok": 0,
        "files_failed": 0,
        "files_on_test_paths": 0,
        "classes_seen": 0,
        "classes_excluded_test_name": 0,
        "classes_excluded_test_path_only": 0,
    }

    for file_path in iter_python_files(raw_dir):
        stats["files_seen"] += 1
        try:
            classes = parse_file(file_path)
        except (SyntaxError, UnicodeDecodeError, OSError, ValueError, RecursionError):
            stats["files_failed"] += 1
            continue
        stats["files_parsed_ok"] += 1

        if not classes:
            continue

        # Per-file scope for the CSV feature columns -- see module docstring.
        try:
            metrics_by_class = compute_all_metrics(classes)
        except (RecursionError, ValueError):
            continue

        on_test_path = is_test_path(file_path)
        if on_test_path:
            stats["files_on_test_paths"] += 1

        category = _source_category(file_path, raw_dir)
        scopes = _scope_keys(file_path, raw_dir)

        for cls in classes:
            metrics = metrics_by_class.get(cls.name)
            if metrics is None:
                continue

            # Excluded outright, not labeled Clean: a TestCase is not a clean
            # production class, and calling it one would teach the model that
            # dozens of stateless methods are normal.
            if is_test_class(cls):
                stats["classes_excluded_test_name"] += 1
                continue

            stats["classes_seen"] += 1
            if on_test_path:
                # Caught by path but NOT by name -- this is the population the
                # path-based check adds over the previous name-only filter.
                stats["classes_excluded_test_path_only"] += 1

            records.append(
                {
                    "class_name": cls.name,
                    "file_path": file_path,
                    "start_line": cls.start_line,
                    "end_line": cls.end_line,
                    "source_category": category,
                    "on_test_path": on_test_path,
                    "scope_keys": scopes,
                    "base": derive_metrics(metrics),
                    "summary": summarize_class(cls, scopes["corpus"], file_path),
                }
            )

    return records, stats


# ---------------------------------------------------------------------------
# Pass 2: cross-class metrics and labels
# ---------------------------------------------------------------------------


def _index_for_scope(records: list[dict], scope: str):
    summaries = [
        dataclasses.replace(r["summary"], scope_key=r["scope_keys"][scope]) for r in records
    ]
    return build_index(summaries), summaries


def feature_envy_counts_by_scope(records: list[dict]) -> dict[str, int]:
    """How many classes satisfy the Feature Envy rule under each scope.

    Reported because the scope choice is a judgement call with a real cost
    on either side -- too narrow and the label is unlearnable, too wide and
    name-based resolution starts matching unrelated projects -- and the
    decision should be made against numbers.
    """
    envy_rule = next(rule for rule in RULES if rule.label == "Feature Envy")
    counts = {}
    for scope in ENVY_SCOPES:
        index, summaries = _index_for_scope(records, scope)
        counts[scope] = sum(
            1
            for record, summary in zip(records, summaries)
            if envy_rule.matches({**record["base"], **envy_from_summary(summary, index)})
        )
    return counts


def label_records(records: list[dict], envy_scope: str) -> dict:
    """Attach both the previous-configuration and the fixed labels.

    Computing both in one pass is what lets the run report exactly which
    rows the WOC and scope fixes moved, instead of asserting an improvement.
    """
    envy_index, envy_summaries = _index_for_scope(records, envy_scope)
    file_index, file_summaries = _index_for_scope(records, "file")
    # Inheritance is resolved globally: an imported base class is no less
    # inherited for living in another file.
    inherit_index, _ = _index_for_scope(records, "corpus")

    status_counts = Counter()

    for record, envy_summary, file_summary in zip(records, envy_summaries, file_summaries):
        base = record["base"]

        previous = {
            **base,
            **body_scoped_woc(record["summary"]),
            **envy_from_summary(file_summary, file_index),
        }
        record["previous_label"], _ = label_for(previous)

        woc = woc_from_summary(
            dataclasses.replace(record["summary"], scope_key=""), inherit_index
        )
        status_counts[woc.pop("inheritance_status")] += 1

        current = {**base, **woc, **envy_from_summary(envy_summary, envy_index)}
        label, matched = label_for(current)

        record["derived"] = current
        record["label"] = label
        record["matched_rules"] = matched

    return dict(status_counts)


# ---------------------------------------------------------------------------
# Stratified split
# ---------------------------------------------------------------------------


def stratified_split(rows: list[dict], test_size: float, seed: int) -> dict:
    """Tag each row with train/test, stratified by label.

    Hand-rolled rather than sklearn's train_test_split because a label with
    a single member makes that function raise, and on real data a rare smell
    genuinely can come back with one instance. Here such a label goes to
    train (a one-row test stratum measures nothing) and the situation is
    reported rather than crashing the run.
    """
    rng = random.Random(seed)
    by_label = defaultdict(list)
    for row in rows:
        by_label[row["label"]].append(row)

    split_counts = {}
    for label, group in by_label.items():
        shuffled = group[:]
        rng.shuffle(shuffled)

        n_test = int(round(len(shuffled) * test_size))
        # Guarantee the test set sees every label that has at least 2
        # members -- the whole point of stratifying is that rare smells are
        # not absent at evaluation time.
        if len(shuffled) >= 2:
            n_test = max(1, min(n_test, len(shuffled) - 1))
        else:
            n_test = 0

        for row in shuffled[:n_test]:
            row["split"] = "test"
        for row in shuffled[n_test:]:
            row["split"] = "train"

        split_counts[label] = {
            "total": len(shuffled),
            "train": len(shuffled) - n_test,
            "test": n_test,
        }

    return split_counts


# ---------------------------------------------------------------------------
# Sanity-check sample
# ---------------------------------------------------------------------------


def _read_source(file_path: str, start: int, end: int, max_lines: int) -> str:
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return "<source unavailable>"

    snippet = lines[start - 1 : end]
    truncated = len(snippet) > max_lines
    if truncated:
        snippet = snippet[:max_lines]
    text = "".join(snippet).rstrip()
    if truncated:
        text += f"\n    ... [{end - start + 1 - max_lines} more lines]"
    return text


def sanity_sample(rows: list[dict], per_label: int, seed: int, max_lines: int) -> dict:
    """A few real classes per label, with their source, for eyeballing.

    Stratified on purpose: a uniform random sample of a corpus that is ~90%
    Clean would show almost nothing but Clean classes, which is exactly the
    part of the labeling that needs the least checking.
    """
    rng = random.Random(seed)
    by_label = defaultdict(list)
    for row in rows:
        by_label[row["label"]].append(row)

    sample = {}
    for label in [rule.label for rule in RULES] + [CLEAN_LABEL]:
        group = by_label.get(label, [])
        if not group:
            sample[label] = []
            continue
        picked = rng.sample(group, min(per_label, len(group)))
        sample[label] = [
            {
                "class_name": row["class_name"],
                "file": os.path.relpath(row["file_path"], RAW_DIR),
                "lines": f"{row['start_line']}-{row['end_line']}",
                "source_category": row["source_category"],
                "matched_rules": row["matched_rules"],
                "previous_label": row["previous_label"],
                "split": row.get("split"),
                "metrics": {
                    k: round(v, 3) if isinstance(v, float) else v
                    for k, v in row["derived"].items()
                },
                "source": _read_source(
                    row["file_path"], row["start_line"], row["end_line"], max_lines
                ),
            }
            for row in picked
        ]
    return sample


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_csv(rows: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({**row["derived"], **row})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default=RAW_DIR)
    parser.add_argument("--output", default=OUTPUT_CSV)
    parser.add_argument("--envy-scope", choices=ENVY_SCOPES, default="corpus")
    parser.add_argument("--sample-per-label", type=int, default=3)
    parser.add_argument("--sample-max-lines", type=int, default=28)
    parser.add_argument("--test-size", type=float, default=TEST_SIZE)
    parser.add_argument("--seed", type=int, default=RANDOM_SEED)
    args = parser.parse_args()

    if not os.path.isdir(args.raw_dir):
        raise SystemExit(
            f"No corpus at {args.raw_dir}. Run data/real_world/collect_corpus.py first."
        )

    _log(f"scanning {args.raw_dir} ...")
    records, stats = collect_records(args.raw_dir)
    _log(
        f"parsed {stats['files_parsed_ok']}/{stats['files_seen']} files "
        f"({stats['files_failed']} failed), {stats['classes_seen']} classes kept "
        f"({stats['classes_excluded_test_name']} excluded by test-class name)"
    )

    if not records:
        raise SystemExit("No classes found -- nothing to label.")

    _log("comparing Feature Envy yield across scopes ...")
    envy_by_scope = feature_envy_counts_by_scope(records)
    _log("  " + "  ".join(f"{scope}={count}" for scope, count in envy_by_scope.items()))

    _log(f"labeling with --envy-scope {args.envy_scope} ...")
    inheritance_status = label_records(records, args.envy_scope)

    previous_distribution = Counter(r["previous_label"] for r in records)

    # Path-based exclusion applies AFTER both label sets are computed, so the
    # before/after comparison is not confounded by a changing population.
    kept = [r for r in records if not r["on_test_path"]]
    excluded = [r for r in records if r["on_test_path"]]
    _log(
        f"path-based test exclusion drops {len(excluded)} further classes "
        f"({len(kept)} remain)"
    )

    distribution = Counter(r["label"] for r in kept)
    split_counts = stratified_split(kept, args.test_size, args.seed)
    sample = sanity_sample(kept, args.sample_per_label, args.seed, args.sample_max_lines)

    write_csv(kept, args.output)
    _log(f"wrote {len(kept)} labeled rows -> {args.output}")

    # Where the previously-labeled rows ended up, restricted to the rows that
    # survive path exclusion -- the honest denominator for "what did the fix
    # change" is the set we still keep.
    transitions = defaultdict(Counter)
    for record in records:
        destination = "excluded (test path)" if record["on_test_path"] else record["label"]
        transitions[record["previous_label"]][destination] += 1

    audit = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus_dir": os.path.relpath(args.raw_dir, ROOT),
        "row_count": len(kept),
        "metric_scope": {
            "csv_features": "per-file",
            "feature_envy_atfd_laa": args.envy_scope,
            "woc_inheritance": "corpus (global base-class resolution)",
        },
        "parse_stats": stats,
        "feature_envy_by_scope": envy_by_scope,
        "inheritance_resolution": inheritance_status,
        "label_distribution": dict(distribution.most_common()),
        "previous_label_distribution": dict(previous_distribution.most_common()),
        "label_transitions_previous_to_current": {
            k: dict(v.most_common()) for k, v in transitions.items()
        },
        "split": {
            "test_size": args.test_size,
            "seed": args.seed,
            "stratified_by": "label",
            "per_label": split_counts,
        },
        "classes_by_source": dict(Counter(r["source_category"] for r in kept)),
        "labels_by_source": {
            source: dict(Counter(r["label"] for r in kept if r["source_category"] == source))
            for source in sorted({r["source_category"] for r in kept})
        },
        "multi_rule_matches": dict(
            Counter(
                " + ".join(r["matched_rules"]) for r in kept if len(r["matched_rules"]) > 1
            ).most_common()
        ),
        "thresholds": as_manifest(),
        "sanity_sample": sample,
    }
    with open(AUDIT_JSON, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2)
    _log(f"audit written -> {AUDIT_JSON}")

    total = len(kept)
    _log("=" * 70)
    _log("LABEL DISTRIBUTION")
    for label, count in distribution.most_common():
        counts = split_counts[label]
        _log(
            f"  {label:14s} {count:5d} ({count / total:5.1%})   "
            f"train={counts['train']:5d}  test={counts['test']:4d}"
        )
    _log("=" * 70)


if __name__ == "__main__":
    main()
