"""CLI entrypoint: scan a repo and write a structural-metrics JSON report."""

from __future__ import annotations

import argparse
import os

from engine.metrics import compute_all_metrics
from engine.parser import parse_repo
from engine.serializer import metrics_to_json


def main():
    parser = argparse.ArgumentParser(description="Scan a Python repo and compute structural metrics.")
    parser.add_argument("repo_path", help="Path to the repo (or directory) to scan")
    parser.add_argument("--output", default="report.json", help="Path to write the JSON report to")
    args = parser.parse_args()

    classes = parse_repo(args.repo_path)
    metrics = compute_all_metrics(classes)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(metrics_to_json(metrics))

    file_count = sum(
        1
        for _root, _dirs, files in os.walk(args.repo_path)
        for filename in files
        if filename.endswith(".py")
    )
    class_count = len(classes)
    method_count = sum(len(cls.methods) for cls in classes)

    print(f"Scanned {file_count} files, {class_count} classes, {method_count} methods")
    print(f"Report written to {args.output}")


if __name__ == "__main__":
    main()
