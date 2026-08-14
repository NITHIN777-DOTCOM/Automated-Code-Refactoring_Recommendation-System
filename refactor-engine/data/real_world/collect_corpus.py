"""Build a real-world Python corpus to retrain the smell classifier on,
replacing the synthetic dataset from generate_synthetic_dataset.py.

Path taken here is the mirror image of that file's: instead of generating
labeled classes with a KNOWN engineered smell, this pulls UNLABELED real
Python source from four sources of decreasing "certified-ness" so the
result reads as a professional, citable corpus rather than an ad hoc
scrape, per the professor's requirement:

  1. STANDARD/CERTIFIED ANCHOR -- ETH Py150 (Raychev, Bielik, Vechev,
     "Probabilistic Model for Code with Decision Trees", OOPSLA 2016).
     Chosen over CodeParrot for the anchor role because it ships as a
     plain source-file tarball (data.tar.gz of 150,000 real GitHub .py
     files) with no ML-library dependency and no streaming/parquet
     fragility -- CodeParrot would work too (the brief allows either) but
     needs `datasets` + network streaming of a much larger corpus for the
     same outcome. Sized to be the BULK of the corpus (see _plan_sizes).
  2. CLONED GITHUB REPOS -- ~20 real projects, majority small/older/
     less-curated, a few popular polished ones as a minority. This is the
     "messiness" source: whole repos with their real directory layout,
     tests, scripts, not just isolated files.
  3. SITE-PACKAGES -- a handful of already-installed mature libraries
     (numpy, pandas, scikit-learn), sampled lightly. Explicitly capped
     small: mature libraries skew clean, so leaning on them would bias
     the corpus away from "real-world messy code".
  4. CODESEARCHNET -- HF `datasets` community parquet mirror of the
     Python subset. Predominantly standalone functions (many extracted
     out of their enclosing class), so this source is expected to
     contribute files but close to zero parsed classes -- treated as
     supplementary padding toward the file-count target, never toward
     the class-count target.

Run standalone: `python data/real_world/collect_corpus.py`
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from engine.parser import iter_python_files, parse_file, parse_repo  # noqa: E402

REAL_WORLD_DIR = os.path.join(ROOT, "data", "real_world")
RAW_DIR = os.path.join(REAL_WORLD_DIR, "raw_sources")
CACHE_DIR = os.path.join(REAL_WORLD_DIR, ".cache")
MANIFEST_PATH = os.path.join(REAL_WORLD_DIR, "corpus_manifest.json")
PARSE_REPORT_PATH = os.path.join(REAL_WORLD_DIR, "parse_report.json")

ACCESS_DATE = datetime.now(timezone.utc).strftime("%Y-%m-%d")
RANDOM_SEED = 20260814

# Overall target: 4000-5000 files total, with py150 sized last to fill
# whatever git repos + site-packages + CodeSearchNet didn't already use,
# so the anchor dataset stays dominant regardless of how many files the
# organically-sized git clones happen to yield.
TARGET_TOTAL_MID = 4500
SITE_PACKAGES_TARGET = 250
CODESEARCHNET_TARGET = 900
PY150_MIN = 1800
PY150_MAX = 3600

GIT_REPOS = [
    # -- majority: small / older / less-actively-maintained (diversity & messiness) --
    {"name": "maya", "url": "https://github.com/kennethreitz/maya", "category": "small"},
    {"name": "records", "url": "https://github.com/kennethreitz42/records", "category": "small"},
    {"name": "tablib", "url": "https://github.com/jazzband/tablib", "category": "small"},
    {"name": "python-slugify", "url": "https://github.com/un33k/python-slugify", "category": "small"},
    {"name": "ftfy", "url": "https://github.com/rspeer/python-ftfy", "category": "small"},
    {"name": "emoji", "url": "https://github.com/carpedm20/emoji", "category": "small"},
    {"name": "pint", "url": "https://github.com/hgrecco/pint", "category": "small"},
    {"name": "validators", "url": "https://github.com/python-validators/validators", "category": "small"},
    {"name": "gspread", "url": "https://github.com/burnash/gspread", "category": "small"},
    {"name": "premailer", "url": "https://github.com/peterbe/premailer", "category": "small"},
    {"name": "webassets", "url": "https://github.com/miracle2k/webassets", "category": "small"},
    {"name": "schedule", "url": "https://github.com/dbader/schedule", "category": "small"},
    {"name": "jellyfish", "url": "https://github.com/jamesturk/jellyfish", "category": "small"},
    {"name": "python-json-logger", "url": "https://github.com/madzak/python-json-logger", "category": "small"},
    {"name": "exif-py", "url": "https://github.com/ianare/exif-py", "category": "small"},
    {"name": "pluginbase", "url": "https://github.com/mitsuhiko/pluginbase", "category": "small"},
    # -- minority: popular, polished, well-maintained --
    {"name": "requests", "url": "https://github.com/psf/requests", "category": "popular"},
    {"name": "click", "url": "https://github.com/pallets/click", "category": "popular"},
    {"name": "rich", "url": "https://github.com/Textualize/rich", "category": "popular"},
    {"name": "tqdm", "url": "https://github.com/tqdm/tqdm", "category": "popular"},
]

SITE_PACKAGES = ["numpy", "pandas", "sklearn"]

LICENSE_PATTERNS = [
    ("MIT", re.compile(r"\bMIT License\b", re.I)),
    ("Apache-2.0", re.compile(r"\bApache License\b.*\bVersion 2\.0\b", re.I | re.S)),
    ("BSD-3-Clause", re.compile(r"\bBSD\b.*\b3.Clause\b", re.I | re.S)),
    ("BSD", re.compile(r"\bBSD License\b|\bRedistribution and use in source\b", re.I)),
    ("GPL", re.compile(r"\bGNU GENERAL PUBLIC LICENSE\b", re.I)),
    ("LGPL", re.compile(r"\bGNU LESSER GENERAL PUBLIC LICENSE\b", re.I)),
    ("MPL-2.0", re.compile(r"\bMozilla Public License\b.*\b2\.0\b", re.I | re.S)),
    ("ISC", re.compile(r"\bISC License\b", re.I)),
    ("Unlicense", re.compile(r"\bThis is free and unencumbered software\b", re.I)),
]


def _log(msg: str) -> None:
    print(f"[collect] {msg}", flush=True)


def _detect_license_from_text(text: str) -> str:
    for label, pattern in LICENSE_PATTERNS:
        if pattern.search(text):
            return label
    return "unknown"


def _detect_repo_license(repo_dir: str) -> str:
    for fname in os.listdir(repo_dir):
        if fname.upper().startswith(("LICENSE", "COPYING")):
            path = os.path.join(repo_dir, fname)
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        return _detect_license_from_text(f.read(4000))
                except OSError:
                    continue
    return "unknown"


# ---------------------------------------------------------------------------
# Source 1: ETH Py150 (standard/certified anchor)
# ---------------------------------------------------------------------------

PY150_ARCHIVE_URL = "https://files.sri.inf.ethz.ch/data/py150_files.tar.gz"
PY150_CITATION = (
    "Veselin Raychev, Pavol Bielik, Martin Vechev. "
    "\"Probabilistic Model for Code with Decision Trees.\" OOPSLA 2016."
)


def _ensure_py150_cache() -> str:
    """Download+unpack the py150 source archive into CACHE_DIR once; reuse on reruns."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    data_tar = os.path.join(CACHE_DIR, "data.tar.gz")
    train_list = os.path.join(CACHE_DIR, "python100k_train.txt")
    eval_list = os.path.join(CACHE_DIR, "python50k_eval.txt")

    if os.path.exists(data_tar) and os.path.exists(train_list) and os.path.exists(eval_list):
        _log("py150: using cached archive")
        return CACHE_DIR

    outer_path = os.path.join(CACHE_DIR, "py150_files.tar.gz")
    _log(f"py150: downloading {PY150_ARCHIVE_URL} (~190MB)")
    urllib.request.urlretrieve(PY150_ARCHIVE_URL, outer_path)

    with tarfile.open(outer_path, "r:gz") as outer:
        outer.extractall(CACHE_DIR, filter="data")
    os.remove(outer_path)
    return CACHE_DIR


def collect_eth_py150(target_count: int) -> dict:
    cache = _ensure_py150_cache()
    with open(os.path.join(cache, "python100k_train.txt"), encoding="utf-8") as f:
        train_paths = [line.strip() for line in f if line.strip()]
    with open(os.path.join(cache, "python50k_eval.txt"), encoding="utf-8") as f:
        eval_paths = [line.strip() for line in f if line.strip()]
    all_paths = train_paths + eval_paths

    # Oversample: some py150 paths nest deep enough (long GitHub repo/file
    # names) to blow past Windows' 260-char MAX_PATH once joined onto our
    # own output tree. Rather than fight that with \\?\ path prefixing
    # (which has its own footguns), sample extra candidates up front and
    # skip-and-continue past any that fail to write.
    rng = random.Random(RANDOM_SEED)
    oversample_n = min(int(target_count * 1.25) + 50, len(all_paths))
    wanted = set(rng.sample(all_paths, oversample_n))

    out_dir = os.path.join(RAW_DIR, "eth_py150")
    os.makedirs(out_dir, exist_ok=True)

    written = 0
    skipped = 0
    data_tar = os.path.join(cache, "data.tar.gz")
    with tarfile.open(data_tar, "r:gz") as tar:
        for member in tar:
            if written >= target_count:
                break
            if member.name not in wanted or not member.isfile():
                continue
            fileobj = tar.extractfile(member)
            if fileobj is None:
                continue
            # member.name looks like "data/<user>/<repo>/.../file.py"; keep
            # the <user>/<repo>/... part so filenames stay unique and traceable.
            rel = member.name.split("/", 1)[1] if "/" in member.name else member.name
            dest = os.path.join(out_dir, rel)
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as out:
                    shutil.copyfileobj(fileobj, out)
            except OSError:
                skipped += 1
                continue
            written += 1

    if skipped:
        _log(f"py150: skipped {skipped} files (path too long or other OS error)")
    _log(f"py150: wrote {written} files")
    return {
        "name": "eth_py150",
        "category": "standard_dataset",
        "origin": "ETH SRI Lab py150 dataset -- raw GitHub Python source files",
        "citation": PY150_CITATION,
        "url": PY150_ARCHIVE_URL,
        "access_date": ACCESS_DATE,
        "file_count": written,
        "license": "unknown/mixed (aggregated from public GitHub repos; per-repo attribution "
        "in github_repos.txt inside the source archive, no single blanket license)",
    }


# ---------------------------------------------------------------------------
# Source 2: cloned GitHub repos
# ---------------------------------------------------------------------------


def _count_py_files(path: str) -> int:
    return sum(1 for _ in iter_python_files(path))


def collect_git_repos(repos: list[dict]) -> dict:
    out_dir = os.path.join(RAW_DIR, "git_repos")
    os.makedirs(out_dir, exist_ok=True)

    items = []
    failures = []
    for repo in repos:
        dest = os.path.join(out_dir, repo["name"])
        if os.path.isdir(dest):
            _log(f"git: {repo['name']} already cloned, reusing")
        else:
            _log(f"git: cloning {repo['url']}")
            try:
                subprocess.run(
                    ["git", "clone", "--depth", "1", "--quiet", repo["url"], dest],
                    check=True,
                    timeout=180,
                    capture_output=True,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
                _log(f"git: FAILED {repo['url']} ({exc})")
                failures.append({"name": repo["name"], "url": repo["url"], "error": str(exc)})
                continue

        file_count = _count_py_files(dest)
        items.append(
            {
                "name": repo["name"],
                "url": repo["url"],
                "repo_category": repo["category"],
                "file_count": file_count,
                "license": _detect_repo_license(dest),
            }
        )

    total_files = sum(item["file_count"] for item in items)
    _log(f"git: {len(items)}/{len(repos)} repos cloned successfully, {total_files} .py files total")
    if failures:
        _log(f"git: {len(failures)} repo(s) failed to clone: {[f['name'] for f in failures]}")

    return {
        "name": "git_repos",
        "category": "git_repos",
        "origin": "Shallow clones (git clone --depth 1) of real public GitHub Python projects",
        "citation": "N/A -- primary sources, cited individually by repo URL",
        "access_date": ACCESS_DATE,
        "file_count": total_files,
        "license": "per-repo, see items[].license",
        "items": items,
        "clone_failures": failures,
    }


# ---------------------------------------------------------------------------
# Source 3: installed site-packages (supplementary, capped small)
# ---------------------------------------------------------------------------


_IMPORT_TO_DIST_NAME = {"sklearn": "scikit-learn"}


def _package_license(pkg: str) -> str:
    try:
        from importlib.metadata import metadata

        meta = metadata(_IMPORT_TO_DIST_NAME.get(pkg, pkg))
        license_field = meta.get("License")
        if license_field and license_field.strip() and license_field.strip().lower() != "unknown":
            return license_field.strip()
        for classifier in meta.get_all("Classifier") or []:
            if classifier.startswith("License ::"):
                return classifier.split("::")[-1].strip()
    except Exception:
        pass
    return "unknown"


def collect_site_packages(packages: list[str], target_total: int) -> dict:
    out_dir = os.path.join(RAW_DIR, "site_packages")
    os.makedirs(out_dir, exist_ok=True)

    per_package_cap = max(target_total // len(packages), 1)
    items = []
    for pkg in packages:
        try:
            mod = __import__(pkg)
        except ImportError:
            _log(f"site-packages: {pkg} not installed, skipping")
            items.append({"name": pkg, "file_count": 0, "license": "unknown", "note": "not installed"})
            continue

        pkg_root = os.path.dirname(mod.__file__)
        dest_root = os.path.join(out_dir, pkg)
        os.makedirs(dest_root, exist_ok=True)

        all_py = list(iter_python_files(pkg_root))
        rng = random.Random(RANDOM_SEED)
        rng.shuffle(all_py)
        sampled = all_py[:per_package_cap]

        for src_path in sampled:
            rel = os.path.relpath(src_path, pkg_root)
            dest_path = os.path.join(dest_root, rel)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copyfile(src_path, dest_path)

        items.append({"name": pkg, "file_count": len(sampled), "license": _package_license(pkg)})
        _log(f"site-packages: {pkg} -> {len(sampled)} files")

    total_files = sum(item["file_count"] for item in items)
    return {
        "name": "site_packages",
        "category": "site_packages",
        "origin": "Installed mature libraries sampled from the local Python environment "
        "(numpy, pandas, scikit-learn) -- supplementary only, not relied on for volume",
        "citation": "N/A -- primary sources, cited individually by package",
        "access_date": ACCESS_DATE,
        "file_count": total_files,
        "license": "per-package, see items[].license",
        "items": items,
    }


# ---------------------------------------------------------------------------
# Source 4: CodeSearchNet (supplementary, run in a subprocess)
# ---------------------------------------------------------------------------

CODESEARCHNET_CITATION = (
    "Hamel Husain, Ho-Hsiang Wu, Tiferet Gazit, Miltiadis Allamanis, Marc Brockschmidt. "
    "\"CodeSearchNet Challenge: Evaluating the State of Semantic Code Search.\" arXiv:1909.09436 (2019)."
)


def collect_codesearchnet(target_count: int) -> dict:
    out_dir = os.path.join(RAW_DIR, "codesearchnet")
    os.makedirs(out_dir, exist_ok=True)
    worker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_collect_codesearchnet.py")

    _log(f"codesearchnet: streaming {target_count} python entries (subprocess, isolates a known "
         "pyarrow/interpreter-shutdown crash-on-exit from the rest of the pipeline)")
    result = subprocess.run(
        [sys.executable, worker, out_dir, str(target_count)],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if result.returncode != 0:
        _log(f"codesearchnet: worker exited {result.returncode} (checking files on disk anyway)")
        _log(result.stderr[-2000:])

    written = len([f for f in os.listdir(out_dir) if f.endswith(".py")])
    _log(f"codesearchnet: {written} files on disk")

    return {
        "name": "codesearchnet",
        "category": "codesearchnet",
        "origin": "CodeSearchNet Python subset, HuggingFace parquet mirror "
        "(code-search-net/code_search_net, config 'default', filtered to language=='python')",
        "citation": CODESEARCHNET_CITATION,
        "url": "https://huggingface.co/datasets/code-search-net/code_search_net",
        "access_date": ACCESS_DATE,
        "file_count": written,
        "license": "unknown/mixed (aggregated from public GitHub repos; underlying snippets retain "
        "their original repo's license per the CodeSearchNet dataset card)",
        "note": "Predominantly standalone functions, not classes -- supplementary source, "
        "not expected to contribute meaningfully to the parsed-class count.",
    }


# ---------------------------------------------------------------------------
# Sizing + parsing + reporting
# ---------------------------------------------------------------------------


def _plan_py150_size(other_files_so_far: int) -> int:
    remaining = TARGET_TOTAL_MID - other_files_so_far
    return max(PY150_MIN, min(PY150_MAX, remaining))


def parse_and_report(sources_manifest: list[dict]) -> dict:
    # Map each raw_sources/<subdir> to the manifest category it belongs to,
    # so per-file parse results can be attributed back to a source category.
    category_by_prefix = {
        os.path.join(RAW_DIR, "eth_py150"): "standard_dataset",
        os.path.join(RAW_DIR, "git_repos"): "git_repos",
        os.path.join(RAW_DIR, "site_packages"): "site_packages",
        os.path.join(RAW_DIR, "codesearchnet"): "codesearchnet",
    }

    def categorize(filepath: str) -> str:
        for prefix, category in category_by_prefix.items():
            if filepath.startswith(prefix + os.sep):
                return category
        return "unknown"

    breakdown = {cat: {"files_seen": 0, "files_parsed_ok": 0, "files_failed": 0, "classes": 0} for cat in
                 ["standard_dataset", "git_repos", "site_packages", "codesearchnet", "unknown"]}
    failures = []
    total_files_seen = 0

    for filepath in iter_python_files(RAW_DIR):
        total_files_seen += 1
        cat = categorize(filepath)
        breakdown[cat]["files_seen"] += 1
        try:
            classes = parse_file(filepath)
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            breakdown[cat]["files_failed"] += 1
            failures.append({"file": os.path.relpath(filepath, RAW_DIR), "error": str(exc)})
            continue
        breakdown[cat]["files_parsed_ok"] += 1
        breakdown[cat]["classes"] += len(classes)

    # Cross-check against the official entry point named in the brief.
    all_classes_via_parse_repo = parse_repo(RAW_DIR)

    total_classes = sum(b["classes"] for b in breakdown.values())
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_files_seen": total_files_seen,
        "total_files_parsed_ok": sum(b["files_parsed_ok"] for b in breakdown.values()),
        "total_files_failed": sum(b["files_failed"] for b in breakdown.values()),
        "total_classes_found": total_classes,
        "total_classes_via_parse_repo": len(all_classes_via_parse_repo),
        "breakdown_by_source_category": breakdown,
        "parse_failures_sample": failures[:25],
        "parse_failures_count": len(failures),
    }
    return report


def _flag_imbalance(report: dict) -> list[str]:
    warnings = []
    breakdown = report["breakdown_by_source_category"]
    total_files = report["total_files_seen"]
    total_classes = report["total_classes_found"]

    if total_files:
        for cat, stats in breakdown.items():
            if cat == "unknown" or stats["files_seen"] == 0:
                continue
            share = stats["files_seen"] / total_files
            if share > 0.90:
                warnings.append(
                    f"'{cat}' alone accounts for {share:.0%} of all files -- corpus is not "
                    "genuinely mixed across sources."
                )

    if total_classes:
        for cat, stats in breakdown.items():
            if cat == "unknown" or stats["classes"] == 0:
                continue
            share = stats["classes"] / total_classes
            if share > 0.90:
                warnings.append(
                    f"'{cat}' alone accounts for {share:.0%} of all parsed classes -- class "
                    "counts are not genuinely mixed across sources."
                )

    other_than_standard = total_files - breakdown["standard_dataset"]["files_seen"]
    if other_than_standard == 0:
        warnings.append("No files at all came from git_repos/site_packages/codesearchnet -- "
                         "corpus is 100% the standard dataset, not a mixed corpus.")

    if breakdown["git_repos"]["files_seen"] == 0:
        warnings.append("git_repos contributed 0 files -- diversity/messiness source is missing entirely.")

    return warnings


def main() -> None:
    os.makedirs(RAW_DIR, exist_ok=True)
    manifest_sources = []

    git_entry = collect_git_repos(GIT_REPOS)
    manifest_sources.append(git_entry)

    site_entry = collect_site_packages(SITE_PACKAGES, SITE_PACKAGES_TARGET)
    manifest_sources.append(site_entry)

    csn_entry = collect_codesearchnet(CODESEARCHNET_TARGET)
    manifest_sources.append(csn_entry)

    other_total = git_entry["file_count"] + site_entry["file_count"] + csn_entry["file_count"]
    py150_target = _plan_py150_size(other_total)
    _log(f"py150: sizing anchor sample to {py150_target} files (other sources totalled {other_total})")
    py150_entry = collect_eth_py150(py150_target)
    manifest_sources.insert(0, py150_entry)

    total_files_collected = sum(s["file_count"] for s in manifest_sources)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_total_files": [4000, 5000],
        "total_files_collected": total_files_collected,
        "sources": manifest_sources,
    }
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    _log(f"manifest written to {MANIFEST_PATH}")

    _log("parsing entire collected corpus with engine.parser ...")
    report = parse_and_report(manifest_sources)
    report["imbalance_warnings"] = _flag_imbalance(report)
    with open(PARSE_REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    _log(f"parse report written to {PARSE_REPORT_PATH}")

    _log("=" * 70)
    _log(f"TOTAL FILES COLLECTED: {total_files_collected}")
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
