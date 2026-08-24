"""Expand the real-world corpus built by collect_corpus.py.

This is an ADDITIVE run, not a rebuild: it reuses everything already on
disk under raw_sources/ (4,500 files from the A.1 run) and adds more on
top, then re-parses the WHOLE corpus and re-writes corpus_manifest.json /
parse_report.json so they describe the combined result.

Three kinds of addition, matching the brief:

  1. GENERAL EXPANSION -- more small/less-curated GitHub repos (same
     source-diversity principles as collect_corpus.py: majority small,
     no single source dominating), plus a further ETH Py150 sample and a
     further CodeSearchNet sample.
  2. TARGETED DATA CLASS SEARCH -- repos likely to contain genuine data
     classes: Django/SQLAlchemy ORM model modules (many fields, few real
     methods -- low WOC) and dataclass/attrs-heavy typed Python.
  3. TARGETED FEATURE ENVY SEARCH -- older, less-refactored codebases with
     manager/service/backend classes that are known to reach into other
     classes' data (high ATFD-proxy, low LAA).

Run standalone: `python data/real_world/expand_corpus.py`
"""

from __future__ import annotations

import json
import os
import random
import shutil
import sys
import tarfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from engine.parser import iter_python_files  # noqa: E402

import collect_corpus as base  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REAL_WORLD_DIR = base.REAL_WORLD_DIR
RAW_DIR = base.RAW_DIR
CACHE_DIR = base.CACHE_DIR
MANIFEST_PATH = base.MANIFEST_PATH
PARSE_REPORT_PATH = base.PARSE_REPORT_PATH
ACCESS_DATE = datetime.now(timezone.utc).strftime("%Y-%m-%d")

# ---------------------------------------------------------------------------
# 1. General expansion -- more small/less-curated repos
# ---------------------------------------------------------------------------

GENERAL_REPOS = [
    {"name": "httpie", "url": "https://github.com/httpie/cli", "category": "small"},
    {"name": "faker", "url": "https://github.com/joke2k/faker", "category": "small"},
    {"name": "arrow", "url": "https://github.com/arrow-py/arrow", "category": "small"},
    {"name": "tenacity", "url": "https://github.com/jd/tenacity", "category": "small"},
    {"name": "more-itertools", "url": "https://github.com/more-itertools/more-itertools", "category": "small"},
    {"name": "boltons", "url": "https://github.com/mahmoud/boltons", "category": "small"},
    {"name": "glom", "url": "https://github.com/mahmoud/glom", "category": "small"},
    {"name": "dateparser", "url": "https://github.com/scrapinghub/dateparser", "category": "small"},
    {"name": "humanize", "url": "https://github.com/python-humanize/humanize", "category": "small"},
    {"name": "jsonschema", "url": "https://github.com/python-jsonschema/jsonschema", "category": "small"},
    {"name": "cerberus", "url": "https://github.com/pyeve/cerberus", "category": "small"},
    {"name": "voluptuous", "url": "https://github.com/alecthomas/voluptuous", "category": "small"},
    {"name": "wrapt", "url": "https://github.com/GrahamDumpleton/wrapt", "category": "small"},
    {"name": "funcy", "url": "https://github.com/Suor/funcy", "category": "small"},
    {"name": "cookiecutter", "url": "https://github.com/cookiecutter/cookiecutter", "category": "small"},
]

# ---------------------------------------------------------------------------
# 2. Targeted: repos likely to contain genuine Data Classes
#    (ORM model files with many fields + few real methods, or
#    dataclass/attrs-heavy typed modules -- low WOC by construction)
# ---------------------------------------------------------------------------

DATA_CLASS_REPOS = [
    {"name": "django-taggit", "url": "https://github.com/jazzband/django-taggit", "category": "data_class_target"},
    {"name": "django-mptt", "url": "https://github.com/django-mptt/django-mptt", "category": "data_class_target"},
    {"name": "django-simple-history", "url": "https://github.com/jazzband/django-simple-history", "category": "data_class_target"},
    {"name": "attrs", "url": "https://github.com/python-attrs/attrs", "category": "data_class_target"},
    {"name": "flask-appbuilder", "url": "https://github.com/dpgaspar/Flask-AppBuilder", "category": "data_class_target"},
]

# ---------------------------------------------------------------------------
# 3. Targeted: older, less-refactored codebases with tightly-coupled
#    manager/service/backend classes -- Feature Envy candidates
# ---------------------------------------------------------------------------

FEATURE_ENVY_REPOS = [
    {"name": "mezzanine", "url": "https://github.com/stephenmcd/mezzanine", "category": "feature_envy_target"},
    {"name": "django-oscar", "url": "https://github.com/django-oscar/django-oscar", "category": "feature_envy_target"},
    {"name": "social-core", "url": "https://github.com/python-social-auth/social-core", "category": "feature_envy_target"},
    {"name": "flask-restful", "url": "https://github.com/flask-restful/flask-restful", "category": "feature_envy_target"},
    {"name": "eve", "url": "https://github.com/pyeve/eve", "category": "feature_envy_target"},
]

ALL_NEW_GIT_REPOS = GENERAL_REPOS + DATA_CLASS_REPOS + FEATURE_ENVY_REPOS

PY150_EXTRA_TARGET = 700
PY150_EXTRA_SEED = base.RANDOM_SEED + 1
CODESEARCHNET_EXTRA_TARGET = 300


def _log(msg: str) -> None:
    print(f"[expand] {msg}", flush=True)


def collect_eth_py150_extra(target_count: int, seed: int) -> dict:
    """A further ETH Py150 sample, disjoint-by-construction from the first.

    Reuses the already-cached archive. Draws from a fresh random seed so
    the new sample is (almost certainly) a different subset of the 150k
    files than the first collect_corpus.py run picked; any accidental
    overlap is skipped by the existing-file check below so it never
    double-counts a file already on disk.
    """
    cache = base._ensure_py150_cache()
    with open(os.path.join(cache, "python100k_train.txt"), encoding="utf-8") as f:
        train_paths = [line.strip() for line in f if line.strip()]
    with open(os.path.join(cache, "python50k_eval.txt"), encoding="utf-8") as f:
        eval_paths = [line.strip() for line in f if line.strip()]
    all_paths = train_paths + eval_paths

    rng = random.Random(seed)
    oversample_n = min(int(target_count * 1.6) + 100, len(all_paths))
    wanted = set(rng.sample(all_paths, oversample_n))

    out_dir = os.path.join(RAW_DIR, "eth_py150")
    os.makedirs(out_dir, exist_ok=True)

    written = 0
    skipped_existing = 0
    skipped_error = 0
    data_tar = os.path.join(cache, "data.tar.gz")
    with tarfile.open(data_tar, "r:gz") as tar:
        for member in tar:
            if written >= target_count:
                break
            if member.name not in wanted or not member.isfile():
                continue
            rel = member.name.split("/", 1)[1] if "/" in member.name else member.name
            dest = os.path.join(out_dir, rel)
            if os.path.exists(dest):
                skipped_existing += 1
                continue
            fileobj = tar.extractfile(member)
            if fileobj is None:
                continue
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as out:
                    shutil.copyfileobj(fileobj, out)
            except OSError:
                skipped_error += 1
                continue
            written += 1

    _log(f"py150-extra: wrote {written} NEW files "
         f"(skipped {skipped_existing} already-present, {skipped_error} errors)")
    return {
        "name": "eth_py150_extra",
        "category": "standard_dataset",
        "origin": "ETH SRI Lab py150 dataset -- second sampling pass, additive",
        "citation": base.PY150_CITATION,
        "url": base.PY150_ARCHIVE_URL,
        "access_date": ACCESS_DATE,
        "file_count": written,
        "license": "unknown/mixed (aggregated from public GitHub repos; per-repo attribution "
        "in github_repos.txt inside the source archive, no single blanket license)",
    }


def collect_codesearchnet_extra(target_count: int) -> dict:
    import subprocess

    out_dir = os.path.join(RAW_DIR, "codesearchnet", "extra")
    os.makedirs(out_dir, exist_ok=True)
    worker = os.path.join(REAL_WORLD_DIR, "_collect_codesearchnet.py")

    _log(f"codesearchnet-extra: streaming {target_count} more python entries (subprocess)")
    result = subprocess.run(
        [sys.executable, worker, out_dir, str(target_count)],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if result.returncode != 0:
        _log(f"codesearchnet-extra: worker exited {result.returncode} (checking files on disk anyway)")
        _log(result.stderr[-2000:])

    written = len([f for f in os.listdir(out_dir) if f.endswith(".py")])
    _log(f"codesearchnet-extra: {written} files on disk")
    return {
        "name": "codesearchnet_extra",
        "category": "codesearchnet",
        "origin": "CodeSearchNet Python subset -- second sampling pass, additive",
        "citation": base.CODESEARCHNET_CITATION,
        "url": "https://huggingface.co/datasets/code-search-net/code_search_net",
        "access_date": ACCESS_DATE,
        "file_count": written,
        "license": "unknown/mixed (aggregated from public GitHub repos; underlying snippets retain "
        "their original repo's license per the CodeSearchNet dataset card)",
        "note": "Predominantly standalone functions, not classes -- supplementary source, "
        "not expected to contribute meaningfully to the parsed-class count.",
    }


def main() -> None:
    if not os.path.isdir(RAW_DIR):
        raise SystemExit(f"No corpus at {RAW_DIR}. Run collect_corpus.py first.")
    if not os.path.exists(MANIFEST_PATH):
        raise SystemExit(f"No manifest at {MANIFEST_PATH}. Run collect_corpus.py first.")

    with open(MANIFEST_PATH, encoding="utf-8") as f:
        old_manifest = json.load(f)

    new_sources = []

    _log(f"cloning {len(ALL_NEW_GIT_REPOS)} new repos "
         f"({len(GENERAL_REPOS)} general, {len(DATA_CLASS_REPOS)} data-class-targeted, "
         f"{len(FEATURE_ENVY_REPOS)} feature-envy-targeted) ...")
    git_entry = base.collect_git_repos(ALL_NEW_GIT_REPOS)
    git_entry["name"] = "git_repos_expansion"
    new_sources.append(git_entry)

    py150_entry = collect_eth_py150_extra(PY150_EXTRA_TARGET, PY150_EXTRA_SEED)
    new_sources.append(py150_entry)

    csn_entry = collect_codesearchnet_extra(CODESEARCHNET_EXTRA_TARGET)
    new_sources.append(csn_entry)

    added_files = sum(s["file_count"] for s in new_sources)
    _log(f"expansion added {added_files} new files on disk")

    combined_manifest = dict(old_manifest)
    combined_manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    combined_manifest["expansion_generated_at"] = datetime.now(timezone.utc).isoformat()
    combined_manifest["target_total_files"] = [6500, 7500]
    combined_manifest["sources"] = old_manifest["sources"] + new_sources

    _log("re-parsing the ENTIRE corpus (old + new) with engine.parser ...")
    report = base.parse_and_report(combined_manifest["sources"])
    combined_manifest["total_files_collected"] = report["total_files_seen"]

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(combined_manifest, f, indent=2)
    _log(f"manifest written to {MANIFEST_PATH}")

    report["imbalance_warnings"] = base._flag_imbalance(report)
    with open(PARSE_REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    _log(f"parse report written to {PARSE_REPORT_PATH}")

    _log("=" * 70)
    _log(f"TOTAL FILES ON DISK NOW: {report['total_files_seen']} "
         f"(was {old_manifest['total_files_collected']}, added {added_files})")
    _log(f"TOTAL FILES PARSED OK: {report['total_files_parsed_ok']} "
         f"(failed: {report['total_files_failed']})")
    _log(f"TOTAL CLASSES FOUND: {report['total_classes_found']}")
    for cat, stats in report["breakdown_by_source_category"].items():
        if stats["files_seen"]:
            _log(f"  {cat:18s} files={stats['files_seen']:5d}  classes={stats['classes']:5d}")
    if report["imbalance_warnings"]:
        _log("IMBALANCE WARNINGS:")
        for w in report["imbalance_warnings"]:
            _log(f"  - {w}")
    _log("=" * 70)


if __name__ == "__main__":
    main()
