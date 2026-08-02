"""Loads the labeled dataset, builds features, trains a RandomForest smell
classifier, and reports held-out evaluation metrics.

NOTE ON ACCURACY: this trains on data/labeled_dataset.csv, which is a
SYNTHETIC dataset whose classes were constructed to exhibit one smell each.
Its feature distributions are cleanly separated by construction (lcom and
cyclomatic complexity are both sharply bimodal, with no borderline cases).
Near-perfect test accuracy here therefore measures synthetic separability,
NOT real-world generalization. The real generalization test is the later
evaluation phase against real OSS repos with refactor-commit-derived labels.
"""

from __future__ import annotations

import os
from collections import Counter

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import train_test_split

from engine.ml.features import FEATURE_COLUMNS, build_feature_matrix

_HERE = os.path.dirname(os.path.abspath(__file__))
DATASET_PATH = os.path.join(
    os.path.dirname(os.path.dirname(_HERE)),
    "data",
    "labeled_dataset.csv",
)
MODEL_PATH = os.path.join(_HERE, "smell_classifier.pkl")


def _print_distribution(title, labels):
    counts = Counter(labels)
    total = sum(counts.values())
    print(f"{title} (n={total}):")
    for label, count in sorted(counts.items()):
        print(f"  {label}: {count} ({count / total:.1%})")


def _print_confusion_matrix(y_true, y_pred, class_names):
    matrix = confusion_matrix(y_true, y_pred)
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


def _print_per_class_feature_means(df):
    """Raw (unscaled) per-class means, so feature importances can be sanity
    checked against which metric actually separates which smell."""
    grouped = df.groupby("label")[FEATURE_COLUMNS].mean()
    col_width = 13
    print("  " + "label".ljust(14) + "".join(c[:12].rjust(col_width) for c in FEATURE_COLUMNS))
    for label, row in grouped.iterrows():
        print("  " + label.ljust(14) + "".join(f"{row[c]:>{col_width}.2f}" for c in FEATURE_COLUMNS))


def main():
    df = pd.read_csv(DATASET_PATH)

    X, y, feature_names = build_feature_matrix(df)
    labels = df["label"].values
    label_encoder = joblib.load(os.path.join(_HERE, "label_encoder.pkl"))
    class_names = list(label_encoder.classes_)

    X_train, X_test, y_train, y_test, labels_train, labels_test = train_test_split(
        X, y, labels, test_size=0.2, stratify=y, random_state=42
    )

    print("=" * 78)
    print("DATA PIPELINE")
    print("=" * 78)
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
    model = RandomForestClassifier(
        n_estimators=100,
        class_weight="balanced",
        random_state=42,
    )
    model.fit(X_train, y_train)
    print(f"Trained RandomForestClassifier(n_estimators=100, class_weight='balanced') "
          f"on {X_train.shape[0]} samples.")

    y_pred = model.predict(X_test)

    print()
    print("=" * 78)
    print("EVALUATION (held-out test set)")
    print("=" * 78)
    print(f"Accuracy: {accuracy_score(y_test, y_pred):.4f}")
    print()
    print(classification_report(y_test, y_pred, target_names=class_names, digits=3))
    print("Confusion matrix:")
    _print_confusion_matrix(y_test, y_pred, class_names)

    print()
    print("=" * 78)
    print("FEATURE IMPORTANCES")
    print("=" * 78)
    _print_feature_importances(model, feature_names)
    print()
    print("Per-class raw feature means (for sanity-checking the above):")
    _print_per_class_feature_means(df)

    joblib.dump(model, MODEL_PATH)
    print()
    print(f"Model saved to {MODEL_PATH}")

    print()
    print("=" * 78)
    print("CAVEAT")
    print("=" * 78)
    print("These scores reflect SYNTHETIC data separability, not real-world")
    print("generalization. The synthetic classes were constructed to exhibit one")
    print("smell each, producing cleanly separated (bimodal) feature clusters with")
    print("no borderline cases. Real validation comes from the later evaluation")
    print("phase against real OSS repos with refactor-commit-derived labels.")

    return model, X_train, X_test, y_train, y_test


if __name__ == "__main__":
    main()
