"""Turns the labeled metrics CSV into a model-ready feature matrix.

Missing-value strategy: numeric metric columns are imputed with the
column MEDIAN, not 0. Several of our metrics (lcom, cyclomatic complexity)
are naturally bimodal/skewed (see data/labeled_dataset.csv), so 0 is not a
neutral "no signal" value -- it can coincide with a real cluster (e.g. a
cohesive class legitimately has lcom == 0.0). The median is more robust to
that skew and doesn't fabricate a value that looks like a meaningful class.
"""

from __future__ import annotations

import os

import joblib
from sklearn.preprocessing import LabelEncoder, StandardScaler

FEATURE_COLUMNS = [
    "cbo",
    "lcom",
    "class_length",
    "avg_method_length",
    "avg_cyclomatic_complexity",
    "dit",
    "fan_in",
    "fan_out",
]

_HERE = os.path.dirname(os.path.abspath(__file__))
SCALER_PATH = os.path.join(_HERE, "scaler.pkl")
ENCODER_PATH = os.path.join(_HERE, "label_encoder.pkl")


def build_feature_matrix(df):
    features_df = df[FEATURE_COLUMNS].apply(lambda col: col.fillna(col.median()))

    scaler = StandardScaler()
    X = scaler.fit_transform(features_df.values)
    joblib.dump(scaler, SCALER_PATH)

    # The encoder is persisted alongside the scaler so inference can map the
    # model's integer predictions back to human-readable smell labels.
    label_encoder = LabelEncoder()
    y = label_encoder.fit_transform(df["label"].values)
    joblib.dump(label_encoder, ENCODER_PATH)

    return X, y, FEATURE_COLUMNS
