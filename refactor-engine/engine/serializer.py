"""JSON serialization for compute_all_metrics() output."""

from __future__ import annotations

import ast
import json


def _json_default(value):
    if isinstance(value, ast.AST):
        return None
    if isinstance(value, set):
        return sorted(value)
    return str(value)


def metrics_to_json(metrics_dict: dict) -> str:
    return json.dumps(metrics_dict, indent=2, default=_json_default, sort_keys=True)
