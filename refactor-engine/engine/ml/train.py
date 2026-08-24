"""Loads a labeled dataset, builds features, trains a RandomForest smell
classifier, and reports held-out evaluation metrics.

TWO DATASETS, TWO MODELS
------------------------
Run with no arguments this trains the DEFAULT model on
data/labeled_dataset.csv, which is a SYNTHETIC dataset whose classes were
constructed to exhibit one smell each. Its feature distributions are cleanly
separated by construction (lcom and cyclomatic complexity are both sharply
bimodal, with no borderline cases), and three of its eight features -- cbo,
dit, fan_in -- are constant at zero, so the forest never learns to use them
at all. Near-perfect test accuracy there measures synthetic separability, NOT
real-world generalization.

Run with --dataset/--split-column/--output it trains a SEPARATE model on the
real-world corpus from A.2/A.2b and writes it as a self-contained bundle
(engine/ml/bundle.py), leaving every default artifact untouched:

    python -m engine.ml.train \\
        --dataset data/real_world/labeled_real_dataset.csv \\
        --split-column split \\
        --output engine/ml/models/classifier_real.joblib

WHY --output CHANGES THE WRITE BEHAVIOUR
----------------------------------------
The default path persists scaler.pkl and label_encoder.pkl next to the model,
because those three files ARE the shipped classifier. A second model must not
write them: it would leave the shipped model paired with another dataset's
preprocessing, with nothing raising and every prediction quietly shifting.
With --output the scaler and encoder go inside the bundle instead.
"""

from __future__ import annotations

import argparse
import os
from collections import Counter
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import train_test_split

from engine.ml.bundle import save_bundle
from engine.ml.features import (
    ENCODER_PATH,
    FEATURE_COLUMNS,
    SCALER_PATH,
    columns_for,
    fit_label_encoder,
    fit_scaler,
    transform_features,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))
DATASET_PATH = os.path.join(_PROJECT_ROOT, "data", "labeled_dataset.csv")
REAL_DATASET_PATH = os.path.join(
    _PROJECT_ROOT, "data", "real_world", "labeled_real_dataset.csv"
)
MODEL_PATH = os.path.join(_HERE, "smell_classifier.pkl")

# Hyperparameters are held here rather than inline so the two training runs
# are provably the same estimator. The before/after comparison is only a
# statement about the DATA if nothing else differs between the models.
FOREST_PARAMS = {
    "n_estimators": 100,
    "class_weight": "balanced",
    "random_state": 42,
}


def _print_distribution(title, labels):
    counts = Counter(labels)
    total = sum(counts.values())
    print(f"{title} (n={total}):")
    for label, count in sorted(counts.items()):
        print(f"  {label}: {count} ({count / total:.1%})")


def _print_confusion_matrix(y_true, y_pred, class_names):
    # labels= pinned for the same reason as in the classification report: a
    # class absent from the split must still get its own row and column, or
    # the matrix silently stops lining up with class_names.
    matrix = confusion_matrix(y_true, y_pred, labels=list(range(len(class_names))))
    width = max(len(name) for name in class_names) + 2

    header = " " * width + "".join(f"{name[:10]:>12}" for name in class_names)
    print("(rows = actual, columns = predicted)")
    print(header)
    for name, row in zip(class_names, matrix):
        print(f"{name:<{width}}" + "".join(f"{count:>12}" for count in row))


def _print_feature_importances(model, feature_names):
    importances = model.feature_importances_
    order = np.argsort(importances)[::-1]
    for rank, idx in enumerate(order, start=1):
        bar = "#" * int(round(importances[idx] * 50))
        print(f"  {rank}. {feature_names[idx]:<28} {importances[idx]:.4f}  {bar}")


def _print_per_class_feature_means(df, columns):
    """Raw (unscaled) per-class means, so feature importances can be sanity
    checked against which metric actually separates which smell."""
    grouped = df.groupby("label")[columns].mean()
    col_width = 13
    print("  " + "label".ljust(14) + "".join(c[:12].rjust(col_width) for c in columns))
    for label, row in grouped.iterrows():
        print("  " + label.ljust(14) + "".join(f"{row[c]:>{col_width}.2f}" for c in columns))


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m engine.ml.train",
        description="Train the RandomForest code-smell classifier.",
    )
    parser.add_argument(
        "--dataset", default=DATASET_PATH,
        help="Labeled CSV to train on. Default: the synthetic data/labeled_dataset.csv.",
    )
    parser.add_argument(
        "--split-column", default=None, metavar="COLUMN",
        help="Use this existing column's train/test values instead of re-splitting. "
             "Required for the real-world corpus, whose split is already fixed.",
    )
    parser.add_argument(
        "--output", default=None, metavar="PATH.joblib",
        help="Write a self-contained bundle here instead of overwriting the default "
             "model + scaler + encoder. Use this for any non-default model.",
    )
    parser.add_argument(
        "--name", default=None,
        help="Human-readable name recorded in the bundle metadata.",
    )
    return parser.parse_args(argv)


def _masks_from_column(df, column):
    """Honour a dataset's own train/test column, as a pair of boolean masks.

    Re-splitting a corpus that ships a split would make every number
    incomparable with the labeling audit that produced it, and -- because the
    real corpus is 86% Clean -- a fresh stratified split would also quietly
    move rare-class examples between train and test, changing what "n=5 Data
    Class test rows" even refers to.
    """
    values = df[column].astype(str).str.strip().str.lower().values
    unexpected = sorted(set(values) - {"train", "test"})
    if unexpected:
        raise ValueError(
            f"Column {column!r} holds unexpected values {unexpected}; expected only train/test."
        )

    is_train = values == "train"
    is_test = values == "test"
    if not is_train.any() or not is_test.any():
        raise ValueError(f"Column {column!r} must contain both train and test rows.")
    return is_train, is_test


def _masks_from_stratified_split(df, y):
    """The original 80/20 stratified split, expressed as masks.

    Returned as masks rather than as split arrays so that the caller can
    split the DATAFRAME first and fit the scaler on training rows only.
    """
    positions = np.arange(len(df))
    train_positions, _ = train_test_split(
        positions, test_size=0.2, stratify=y, random_state=42
    )
    is_train = np.zeros(len(df), dtype=bool)
    is_train[train_positions] = True
    return is_train, ~is_train


def main(argv=None):
    args = _parse_args(argv)
    dataset_path = os.path.abspath(args.dataset)
    df = pd.read_csv(dataset_path)
    labels = df["label"].values
    # Which feature list this dataset supports. A re-labelled corpus carrying
    # the real ATFD/FDP columns trains an 11-feature model; an older CSV
    # without them still trains the original 8-feature shape. The choice is
    # recorded in the bundle so inference feeds the model what it was fitted
    # on -- see engine/ml/features.py.
    feature_names = columns_for(df)

    # The label space is fixed across the whole dataset (see fit_label_encoder).
    label_encoder = fit_label_encoder(labels)
    class_names = list(label_encoder.classes_)
    y = label_encoder.transform(labels)

    if args.split_column:
        is_train, is_test = _masks_from_column(df, args.split_column)
        split_description = f"existing {args.split_column!r} column (not re-split)"
    else:
        is_train, is_test = _masks_from_stratified_split(df, y)
        split_description = "stratified 80/20 train_test_split(random_state=42)"

    # Split BEFORE fitting the scaler, so held-out rows contribute nothing to
    # the means and standard deviations the model is trained under.
    X_train, scaler = fit_scaler(df[is_train], feature_names)
    X_test = transform_features(df[is_test], scaler, feature_names)
    y_train, y_test = y[is_train], y[is_test]
    labels_train, labels_test = labels[is_train], labels[is_test]

    # --output means "a second model", which must not touch the shipped
    # artifacts; no --output means "the default model", which is exactly the
    # three files predict.py loads.
    if not args.output:
        joblib.dump(scaler, SCALER_PATH)
        joblib.dump(label_encoder, ENCODER_PATH)

    print("=" * 78)
    print("DATA PIPELINE")
    print("=" * 78)
    print(f"Dataset: {dataset_path}")
    print(f"Rows: {len(df)}")
    print(f"Split: {split_description}")
    print(f"Feature columns: {feature_names}")
    print(f"X_train shape: {X_train.shape}, y_train shape: {y_train.shape}")
    print(f"X_test shape: {X_test.shape}, y_test shape: {y_test.shape}")
    print()
    _print_distribution("Train split distribution", labels_train)
    print()
    _print_distribution("Test split distribution", labels_test)

    print()
    print("=" * 78)
    print("TRAINING")
    print("=" * 78)
    model = RandomForestClassifier(**FOREST_PARAMS)
    model.fit(X_train, y_train)
    settings = ", ".join(f"{k}={v!r}" for k, v in FOREST_PARAMS.items())
    print(f"Trained RandomForestClassifier({settings}) on {X_train.shape[0]} samples.")

    y_pred = model.predict(X_test)

    print()
    print("=" * 78)
    print("EVALUATION (held-out test set)")
    print("=" * 78)
    print(f"Accuracy: {accuracy_score(y_test, y_pred):.4f}")
    print()
    # labels= is pinned to the encoder's full class list: without it, a class
    # that happens to be absent from a test split silently shifts every
    # subsequent row's name by one.
    print(classification_report(
        y_test, y_pred,
        labels=list(range(len(class_names))),
        target_names=class_names,
        digits=3,
        zero_division=0,
    ))
    print("Confusion matrix:")
    _print_confusion_matrix(y_test, y_pred, class_names)

    print()
    print("=" * 78)
    print("FEATURE IMPORTANCES")
    print("=" * 78)
    _print_feature_importances(model, feature_names)
    print()
    print("Per-class raw feature means (for sanity-checking the above):")
    _print_per_class_feature_means(df, feature_names)

    print()
    if args.output:
        written = save_bundle(
            args.output, model, scaler, label_encoder,
            metadata={
                "name": args.name or os.path.splitext(os.path.basename(args.output))[0],
                "dataset": os.path.relpath(dataset_path, _PROJECT_ROOT).replace("\\", "/"),
                "rows": int(len(df)),
                "train_rows": int(X_train.shape[0]),
                "test_rows": int(X_test.shape[0]),
                "split": split_description,
                "features": list(feature_names),
                "classes": class_names,
                "forest_params": dict(FOREST_PARAMS),
                "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        )
        print(f"Bundle saved to {written}")
        print("The default model, scaler and label encoder were NOT modified.")
    else:
        joblib.dump(model, MODEL_PATH)
        print(f"Model saved to {MODEL_PATH}")

    print()
    print("=" * 78)
    print("CAVEAT")
    print("=" * 78)
    if args.output:
        print("Scores above are on the real-world corpus's own held-out split. Read the")
        print("per-class rows against their support counts before trusting any of them:")
        print("the corpus is heavily dominated by Clean, and the rarest smells have")
        print("single- or low-double-digit test counts where one misclassification moves")
        print("F1 by tens of points. See data/real_world/RETRAIN_COMPARISON.md.")
    else:
        print("These scores reflect SYNTHETIC data separability, not real-world")
        print("generalization. The synthetic classes were constructed to exhibit one")
        print("smell each, producing cleanly separated (bimodal) feature clusters with")
        print("no borderline cases. Real validation comes from the later evaluation")
        print("phase against real OSS repos with refactor-commit-derived labels.")

    return model, X_train, X_test, y_train, y_test


if __name__ == "__main__":
    main()
