"""Inference helper: predict a code smell label for a single class.

Takes the per-class dict that Phase 1's compute_all_metrics() produces, i.e.

    {
        "class_length": int,
        "lcom": float,
        "cbo": int,
        "fan_in": int,
        "fan_out": int,
        "depth_of_inheritance": int,
        "methods": {"<name>": {"cyclomatic_complexity": int, "length": int}, ...},
    }

and flattens it into the 8 model features. The flattening below MUST stay in
sync with _row_for_class() in data/generate_synthetic_dataset.py -- that
function produced the training rows, so any divergence here is train/serve
skew: `dit` comes from `depth_of_inheritance`, and the two `avg_*` features
are means over the per-method sub-dict.
"""

from __future__ import annotations

import os
from functools import lru_cache

import joblib
import numpy as np

from engine.ml.features import ENCODER_PATH, FEATURE_COLUMNS, SCALER_PATH

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smell_classifier.pkl")


@lru_cache(maxsize=1)
def _load_artifacts():
    for path in (MODEL_PATH, SCALER_PATH, ENCODER_PATH):
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"Missing model artifact: {path}. Run `python -m engine.ml.train` first."
            )
    return joblib.load(MODEL_PATH), joblib.load(SCALER_PATH), joblib.load(ENCODER_PATH)


def flatten_metrics(metrics_dict: dict) -> dict:
    """Reduce a compute_all_metrics() per-class dict to the flat feature dict."""
    methods = metrics_dict.get("methods", {})
    lengths = [m["length"] for m in methods.values()]
    complexities = [m["cyclomatic_complexity"] for m in methods.values()]

    return {
        "cbo": metrics_dict.get("cbo", 0),
        "lcom": metrics_dict.get("lcom", 0.0),
        "class_length": metrics_dict.get("class_length", 0),
        "avg_method_length": (sum(lengths) / len(lengths)) if lengths else 0.0,
        "avg_cyclomatic_complexity": (
            (sum(complexities) / len(complexities)) if complexities else 0.0
        ),
        # Phase 1 names this `depth_of_inheritance`; the dataset column is `dit`.
        "dit": metrics_dict.get("depth_of_inheritance", metrics_dict.get("dit", 0)),
        "fan_in": metrics_dict.get("fan_in", 0),
        "fan_out": metrics_dict.get("fan_out", 0),
    }


def predict_smell(metrics_dict: dict) -> str:
    model, scaler, label_encoder = _load_artifacts()

    features = flatten_metrics(metrics_dict)
    row = np.array([[features[col] for col in FEATURE_COLUMNS]], dtype=float)
    scaled = scaler.transform(row)

    prediction = model.predict(scaled)
    return str(label_encoder.inverse_transform(prediction)[0])


def predict_smell_with_confidence(metrics_dict: dict) -> tuple[str, float]:
    """Same as predict_smell(), but also returns the model's probability for
    the winning class -- useful when triaging borderline real-world classes."""
    model, scaler, label_encoder = _load_artifacts()

    features = flatten_metrics(metrics_dict)
    row = np.array([[features[col] for col in FEATURE_COLUMNS]], dtype=float)
    scaled = scaler.transform(row)

    probabilities = model.predict_proba(scaled)[0]
    best = int(np.argmax(probabilities))
    return str(label_encoder.inverse_transform([best])[0]), float(probabilities[best])
