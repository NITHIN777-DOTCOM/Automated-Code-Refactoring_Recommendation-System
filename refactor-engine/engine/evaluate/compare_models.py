"""Evaluate the synthetic-trained and real-trained classifiers on ONE test set.

THE QUESTION THIS ANSWERS
-------------------------
The shipped classifier was trained on data/labeled_dataset.csv -- 400 synthetic
classes, 80 per smell, each constructed to exhibit exactly one smell. It scores
near-perfectly on its own held-out split, but that split is synthetic too, so
the score measures how separable the generator made the classes and says
nothing about real code.

This module puts both models in front of the same real held-out rows -- the
`split == "test"` rows of data/real_world/labeled_real_dataset.csv -- and
reports them side by side.

TWO RULES THAT MAKE THE COMPARISON HONEST
-----------------------------------------
1. Each model is applied with the scaler it was FITTED under, never with the
   other's. A model's scaler defines its input space; feeding synthetic-trained
   trees inputs centred on real-world means would be measuring a model that was
   never trained. This is why models are loaded as bundles (engine/ml/bundle.py)
   and why the synthetic model is read from its own three-file set.

2. Predictions are compared as LABEL STRINGS, never as encoder integers. Two
   LabelEncoders fitted on different datasets can assign different integers to
   the same smell; comparing codes would silently score every model against a
   permuted answer key. Strings cannot be permuted.

Everything here is plain data. Rendering lives in report.py.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_recall_fscore_support

from engine.ml.bundle import REAL_MODEL_PATH, load_bundle
from engine.ml.features import (
    ENCODER_PATH,
    FEATURE_COLUMNS,
    SCALER_PATH,
    columns_for,
    transform_features,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))

REAL_DATASET_PATH = os.path.join(
    _PROJECT_ROOT, "data", "real_world", "labeled_real_dataset.csv"
)
SYNTHETIC_MODEL_PATH = os.path.join(_PROJECT_ROOT, "engine", "ml", "smell_classifier.pkl")

# How much weight a per-class score can carry, by how many test examples stand
# behind it. Tiered rather than a yes/no flag because the real corpus's classes
# are not cleanly split into "plenty" and "none": Clean has 550 rows, Data Class
# has 5, and God Class sits at 29 where the number is worth reading but not
# worth defending to two decimal places. A single prediction moves recall by
# 1/n, which is 0.2 percentage points at n=550 and 20 at n=5.
SUPPORT_TIERS = (
    (100, "solid", "Large enough to report as a real measurement."),
    (30, "indicative", "Enough to show the direction; the error bars are wide."),
    (10, "weak", "One or two predictions move this several points. Treat as a hint."),
    (0, "not meaningful", "Too few examples to measure anything. Reported for completeness only."),
)

# Below this, a per-class score is explicitly marked as not carrying evidential
# weight in the headline comparison.
MIN_TRUSTWORTHY_SUPPORT = 30


def support_tier(support: int) -> tuple[str, str]:
    """(tier name, what that tier means) for a given test-set count."""
    for floor, name, meaning in SUPPORT_TIERS:
        if support >= floor:
            return name, meaning
    return SUPPORT_TIERS[-1][1], SUPPORT_TIERS[-1][2]


@dataclass
class ClassScore:
    label: str
    precision: float
    recall: float
    f1: float
    support: int

    @property
    def is_trustworthy(self) -> bool:
        return self.support >= MIN_TRUSTWORTHY_SUPPORT

    @property
    def tier(self) -> str:
        return support_tier(self.support)[0]

    @property
    def tier_meaning(self) -> str:
        return support_tier(self.support)[1]

    @property
    def recall_interval(self) -> tuple[float, float]:
        """Wilson 95% interval for recall.

        Wilson rather than the textbook normal interval because the normal one
        is degenerate exactly where it matters here: at 0 correct out of 5 it
        returns the interval [0, 0], claiming certainty from five examples.
        """
        return _wilson(self.recall, self.support)


@dataclass
class ModelScore:
    name: str
    description: str
    trained_on: str
    accuracy: float
    macro_f1: float
    weighted_f1: float
    per_class: list[ClassScore]
    confusion: np.ndarray
    class_names: list[str]
    predictions: np.ndarray = field(repr=False, default=None)

    def by_label(self, label: str) -> ClassScore:
        return next(c for c in self.per_class if c.label == label)


def _wilson(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    denominator = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def load_test_rows(dataset_path: str = REAL_DATASET_PATH) -> pd.DataFrame:
    """The real corpus's own held-out rows -- the shared yardstick.

    The dataset ships a `split` column produced by the A.2 labeling run. It is
    read rather than recomputed so both models are judged on precisely the rows
    the corpus documentation describes.
    """
    df = pd.read_csv(dataset_path)
    if "split" not in df.columns:
        raise ValueError(f"{dataset_path} has no `split` column to hold the fixed test set.")
    test_df = df[df["split"].astype(str).str.strip().str.lower() == "test"].reset_index(drop=True)
    if test_df.empty:
        raise ValueError(f"{dataset_path} contains no rows marked `test`.")
    return test_df


def _score(name, description, trained_on, y_true_labels, y_pred_labels, class_names) -> ModelScore:
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true_labels, y_pred_labels, labels=class_names, zero_division=0
    )
    return ModelScore(
        name=name,
        description=description,
        trained_on=trained_on,
        accuracy=float(accuracy_score(y_true_labels, y_pred_labels)),
        macro_f1=float(f1_score(y_true_labels, y_pred_labels, labels=class_names,
                                average="macro", zero_division=0)),
        weighted_f1=float(f1_score(y_true_labels, y_pred_labels, labels=class_names,
                                   average="weighted", zero_division=0)),
        per_class=[
            ClassScore(label=label, precision=float(p), recall=float(r),
                       f1=float(fone), support=int(s))
            for label, p, r, fone, s in zip(class_names, precision, recall, f1, support)
        ],
        confusion=confusion_matrix(y_true_labels, y_pred_labels, labels=class_names),
        class_names=list(class_names),
        predictions=np.asarray(y_pred_labels),
    )


def evaluate_synthetic(test_df: pd.DataFrame, class_names: list[str]) -> ModelScore:
    """The shipped model, applied with its own synthetic-fitted scaler."""
    model = joblib.load(SYNTHETIC_MODEL_PATH)
    scaler = joblib.load(SCALER_PATH)
    encoder = joblib.load(ENCODER_PATH)

    # The shipped model predates real ATFD/FDP and was fitted on the
    # original eight columns; it must keep being scored on exactly those,
    # whatever extra columns the corpus has grown since.
    X = transform_features(test_df, scaler, FEATURE_COLUMNS)
    predicted = encoder.inverse_transform(model.predict(X))

    return _score(
        name="Synthetic-trained (current default)",
        description="RandomForest trained on 400 generated example classes, 80 per smell.",
        trained_on="data/labeled_dataset.csv (synthetic, n=400)",
        y_true_labels=test_df["label"].values,
        y_pred_labels=predicted,
        class_names=class_names,
    )


def evaluate_real(test_df: pd.DataFrame, class_names: list[str],
                  bundle_path: str = REAL_MODEL_PATH) -> ModelScore:
    """The retrained model, applied with the scaler stored in its own bundle."""
    bundle = load_bundle(bundle_path)

    # Whatever this bundle was actually fitted on -- 8 columns for a model
    # trained before C.2, 11 for one trained after.
    real_columns = list(bundle.metadata.get("features") or FEATURE_COLUMNS)
    X = transform_features(test_df, bundle.scaler, real_columns)
    predicted = bundle.label_encoder.inverse_transform(bundle.model.predict(X))

    metadata = bundle.metadata
    return _score(
        name="Real-data-trained (new)",
        description=(
            f"RandomForest trained on {metadata.get('train_rows', '?')} real classes "
            f"mined from open-source Python."
        ),
        trained_on=f"{metadata.get('dataset', 'unknown')} (real, n={metadata.get('rows', '?')})",
        y_true_labels=test_df["label"].values,
        y_pred_labels=predicted,
        class_names=class_names,
    )


def compare(dataset_path: str = REAL_DATASET_PATH,
            bundle_path: str = REAL_MODEL_PATH) -> dict:
    """Both models on the same real held-out rows."""
    test_df = load_test_rows(dataset_path)
    # Sorted for a stable row order in every table; both models are scored
    # against this identical list so their rows line up.
    class_names = sorted(pd.read_csv(dataset_path)["label"].unique())

    return {
        "test_df": test_df,
        "class_names": class_names,
        "test_size": len(test_df),
        "synthetic": evaluate_synthetic(test_df, class_names),
        "real": evaluate_real(test_df, class_names, bundle_path),
        "feature_columns": columns_for(test_df),
        "synthetic_feature_columns": list(FEATURE_COLUMNS),
    }
