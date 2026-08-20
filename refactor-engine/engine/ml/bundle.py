"""A trained classifier packaged with the exact preprocessing it was fitted under.

WHY A BUNDLE RATHER THAN LOOSE FILES
------------------------------------
The original layout stores three artifacts side by side in this directory --
smell_classifier.pkl, scaler.pkl, label_encoder.pkl -- and predict.py loads
whichever three happen to be on disk. That works exactly as long as there is
only ever one model. The moment a second one exists it becomes a trap:
training a second model with the old code path would re-fit the scaler and
overwrite scaler.pkl, leaving the FIRST model paired with the SECOND model's
preprocessing. Nothing would raise. Every prediction from the shipped
classifier would silently shift, because its inputs are now being centred on
means it was never fitted against.

A bundle makes that failure unrepresentable: the model, the scaler and the
label encoder travel in one file and are loaded together or not at all.

The legacy three-file layout is still read as-is for the default synthetic
model, so nothing that already works changes. New models are written as
bundles.

WHERE BUNDLES LIVE
------------------
Inside the package (engine/ml/models/) rather than at the repository root.
pyproject.toml ships only `engine*`, so a top-level models/ directory would
exist in a git checkout and vanish from an installed `refactor-scan` -- the
same trap engine/thresholds.py documents for its threshold table. A model the
CLI cannot load when installed is not a model the CLI has.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import joblib

_HERE = os.path.dirname(os.path.abspath(__file__))

MODELS_DIR = os.path.join(_HERE, "models")

# The real-world-trained classifier from A.2/A.2b. Opt-in only -- see
# resolve_model_spec() for how a caller selects it.
REAL_MODEL_PATH = os.path.join(MODELS_DIR, "classifier_real.joblib")

# Selects which model the inference path loads. Deliberately an environment
# variable rather than a constant: it lets the CLI, the test suite and a
# side-by-side comparison run all choose a model without any of them mutating
# files on disk.
MODEL_ENV_VAR = "REFACTOR_SCAN_MODEL"

# The shipped default. Not the real-data model: switching production over is a
# separate, deliberate decision, and until it is made the default has to stay
# exactly what it has always been.
DEFAULT_MODEL = "synthetic"

_ALIASES = {
    "synthetic": "synthetic",
    "default": "synthetic",
    "legacy": "synthetic",
    "real": REAL_MODEL_PATH,
    "real_world": REAL_MODEL_PATH,
    "real-world": REAL_MODEL_PATH,
}


@dataclass(frozen=True)
class ModelBundle:
    """A classifier and the preprocessing that defines its input space."""

    model: Any
    scaler: Any
    label_encoder: Any
    metadata: dict = field(default_factory=dict)

    def as_artifacts(self) -> tuple:
        """The (model, scaler, encoder) triple predict.py expects."""
        return self.model, self.scaler, self.label_encoder


def save_bundle(path: str, model, scaler, label_encoder, metadata: dict | None = None) -> str:
    """Write a bundle, creating its directory. Returns the path written."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    joblib.dump(
        ModelBundle(
            model=model,
            scaler=scaler,
            label_encoder=label_encoder,
            metadata=dict(metadata or {}),
        ),
        path,
    )
    return path


def load_bundle(path: str) -> ModelBundle:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No model bundle at {path}. Train one with:\n"
            f"  python -m engine.ml.train --dataset data/real_world/labeled_real_dataset.csv "
            f"--split-column split --output {path}"
        )
    bundle = joblib.load(path)
    if not isinstance(bundle, ModelBundle):
        raise TypeError(
            f"{path} is not a model bundle (found {type(bundle).__name__}). "
            f"Loose .pkl estimators belong to the legacy three-file layout."
        )
    return bundle


def resolve_model_spec(choice: str | None = None) -> str:
    """Turn a model choice into either "synthetic" or a bundle path.

    Precedence: an explicit argument, then $REFACTOR_SCAN_MODEL, then the
    shipped default. A value that is neither a known alias nor an existing
    path is an error rather than a silent fallback -- a typo in a model name
    must not quietly evaluate the wrong model.
    """
    raw = choice or os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL
    raw = raw.strip()

    if raw in _ALIASES:
        return _ALIASES[raw]
    if os.path.exists(raw):
        return os.path.abspath(raw)

    known = ", ".join(sorted(_ALIASES))
    raise ValueError(
        f"Unknown model {raw!r}. Use one of: {known} -- or a path to a .joblib bundle."
    )


def describe_model(choice: str | None = None) -> dict:
    """What model the current selection resolves to, for reporting in the CLI."""
    spec = resolve_model_spec(choice)
    if spec == "synthetic":
        return {
            "name": "synthetic",
            "path": os.path.join(_HERE, "smell_classifier.pkl"),
            "trained_on": "data/labeled_dataset.csv (synthetic)",
            "is_default": True,
        }

    metadata = load_bundle(spec).metadata
    return {
        "name": metadata.get("name", os.path.basename(spec)),
        "path": spec,
        "trained_on": metadata.get("dataset", "unknown"),
        "is_default": False,
        **metadata,
    }
