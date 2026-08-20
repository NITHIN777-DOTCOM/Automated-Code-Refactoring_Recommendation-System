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


def _impute(df):
    return df[FEATURE_COLUMNS].apply(lambda col: col.fillna(col.median()))


def fit_scaler(train_df):
    """Fit a StandardScaler on TRAINING rows only, returning (X_train, scaler).

    Training rows only, deliberately. A scaler fitted on the full dataset has
    seen the held-out rows' means and standard deviations, and every reported
    test score is then very slightly a score the model was helped to. It is a
    mild leak next to fitting a model on test data, but it is the kind of
    thing that is fair to challenge in a viva, and there is no reason to
    carry it: the fix costs one extra transform call.

    Persists NOTHING. The scaler and the model fitted on its output are a
    matched pair (see engine/ml/bundle.py), and writing one to the shared
    path is only safe when the model about to be written is the shared model.
    """
    scaler = StandardScaler()
    return scaler.fit_transform(_impute(train_df).values), scaler


def fit_label_encoder(labels):
    """Fit the label encoder over the FULL label set.

    Not a leak, and not symmetric with the scaler: the set of smell names is
    a fixed design decision of this project, not a statistic estimated from
    the data. Fitting it on the training split alone would mean a class that
    happened to land only in test could not even be named in a report.
    """
    encoder = LabelEncoder()
    encoder.fit(labels)
    return encoder


def transform_features(df, scaler):
    """Apply an ALREADY-FITTED scaler to `df` -- the inference-side path.

    Used when evaluating a trained model on rows it has never seen: the
    columns must be scaled by the statistics the model was fitted under, not
    by the statistics of the evaluation set. Re-fitting a scaler on test data
    is the classic leak, and here it would also quietly make a
    synthetic-trained model look better on real data than it really is, by
    handing it real-world means it never learned from.
    """
    return scaler.transform(_impute(df).values)


def build_feature_matrix(df):
    """Fit the feature matrix AND persist the scaler/encoder to the shared paths.

    This is the default-model path: engine/ml/scaler.pkl and
    engine/ml/label_encoder.pkl are what predict.py loads, so calling this
    commits to `df` being the dataset the shipped classifier is trained on.

    Kept for the original whole-dataset call shape. Note that it fits the
    scaler on everything it is handed; engine/ml/train.py splits before
    fitting instead, and only uses this path when writing the default model.
    """
    scaler = StandardScaler()
    X = scaler.fit_transform(_impute(df).values)
    joblib.dump(scaler, SCALER_PATH)

    # The encoder is persisted alongside the scaler so inference can map the
    # model's integer predictions back to human-readable smell labels.
    label_encoder = fit_label_encoder(df["label"].values)
    joblib.dump(label_encoder, ENCODER_PATH)

    return X, label_encoder.transform(df["label"].values), FEATURE_COLUMNS
