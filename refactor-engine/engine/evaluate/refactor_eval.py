"""Check our detector against what real developers actually refactored.

Takes the before/after pairs mined by engine.evaluate.git_mining, runs the
normal analysis pipeline over the BEFORE state of each one, and asks a single
question per commit: did we independently flag the class that the real commit
went on to restructure?

WHAT THIS MEASURES, AND WHAT IT DOES NOT
----------------------------------------
"Was refactored" is NOT the same as "was smelly". Developers restructure code
for reasons that have nothing to do with any detectable smell -- renaming for
a new API, adapting to a dependency, marking things private, splitting a file
for readability. A commit landing on a class is therefore not evidence that
the class was smelly, and our failing to flag it is not necessarily a miss.

So the hit rate below is a LOWER BOUND on what the tool could plausibly have
caught, not a recall figure against verified ground truth. Nothing here is a
formal precision/recall benchmark, and the CLI prints that caveat next to
every number rather than burying it in documentation.

TWO METRICS, DELIBERATELY SEPARATED
-----------------------------------
  * TARGETED hit rate -- the strong one. Only commits carrying a structural
    signal (a method moved between classes, a class renamed, methods
    extracted) name a specific class that was restructured, so only those can
    be checked precisely. Match is on (file, class), not class name alone.
  * FILE-LEVEL hit rate -- the weak one. Did we flag ANY class in any file
    the commit touched? Available for every candidate including keyword-only
    ones, but it is much easier to satisfy and is reported separately so it
    can never be mistaken for the targeted number.

SCOPE CAVEAT
------------
The before-state directory holds only the files a commit touched, not the
whole repository, so coupling metrics (cbo/fan_in/fan_out) are computed over
that narrow scope. Feature Envy in particular is scope-sensitive in principle;
in practice the real ATFD/FDP metrics read a class's own foreign attribute
accesses and are scope-free, so this affects cbo/fan_in/fan_out only.
"""

from __future__ import annotations

import json
import logging
import os

from engine.evaluate.git_mining import extract_refactoring_dataset
from engine.pipeline import analyze_path

logger = logging.getLogger(__name__)

CLEAN_LABELS = {"Clean"}

# Printed by the CLI next to every number. Kept here rather than in the
# renderer so that the JSON output carries it too -- a machine-readable
# result that drops the caveat is exactly how a lower bound gets quoted as a
# recall figure.
CAVEAT = (
    "'Was refactored' is NOT 'was smelly'. Developers refactor for many reasons "
    "unrelated to detectable smells (renames, API changes, new features), so this "
    "hit rate is a LOWER BOUND on what the tool could plausibly have flagged -- "
    "not a rigorous precision/recall benchmark against verified ground truth."
)


# ---------------------------------------------------------------------------
# Which class did the real commit actually restructure?
# ---------------------------------------------------------------------------


def refactor_targets(candidate: dict) -> list[dict]:
    """The classes a commit demonstrably restructured, named as they exist in
    the BEFORE state.

    Only structural signals name a target. A commit found by keyword alone
    says "refactor" in its message and nothing verifiable about which class
    changed, so it yields no targets and is excluded from the targeted hit
    rate rather than being given a guessed one.

    Naming is deliberately before-state:
      * a moved method identifies the class that LOST it (the source is what
        Move Method fixes, and it is the one that exists beforehand);
      * a renamed class is identified by its OLD name;
      * an extracted-method class keeps its name across the commit.
    """
    details = candidate.get("details") or {}
    targets: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(file_path: str, class_name: str, signal: str, note: str) -> None:
        key = (file_path, class_name)
        if key in seen:
            return
        seen.add(key)
        targets.append(
            {"file": file_path, "class": class_name, "signal": signal, "note": note}
        )

    for moved in details.get("methods_moved", []):
        for source in moved.get("from_class", []):
            add(
                moved["file"],
                source,
                "method_moved_between_classes",
                f"lost method {moved['method']}() to {', '.join(moved.get('to_class', []))}",
            )

    for renamed in details.get("classes_renamed", []):
        add(
            renamed["file"],
            renamed["from"],
            "class_renamed",
            f"renamed to {renamed['to']}",
        )

    for gained in details.get("classes_gaining_methods", []):
        add(
            gained["file"],
            gained["class"],
            "method_extracted",
            f"gained {gained['methods_gained']} method(s) without growing "
            f"({gained['lines_before']} -> {gained['lines_after']} lines)",
        )

    return targets


# ---------------------------------------------------------------------------
# Running our analysis over a before-state tree
# ---------------------------------------------------------------------------


def _flagged_classes(before_root: str) -> dict:
    """{(repo_relative_file, class_name): entry} for every NON-clean class.

    Keyed by file as well as name because the pipeline's own result dict is
    keyed by class name alone, and two files in one commit can each define a
    class of the same name.
    """
    if not os.path.isdir(before_root):
        return {}

    try:
        results = analyze_path(before_root)
    except Exception as exc:  # a single bad tree must not abort the sweep
        logger.warning("Could not analyze before-state at %s: %s", before_root, exc)
        return {}

    flagged = {}
    for class_name, entry in results.get("classes", {}).items():
        if entry.get("predicted_smell") in CLEAN_LABELS:
            continue
        rel = os.path.relpath(entry.get("file_path", ""), before_root).replace("\\", "/")
        flagged[(rel, class_name)] = entry
    return flagged


def evaluate_candidate(candidate: dict, history_dir: str, repo_name: str) -> dict:
    """Evaluate one mined commit: were its refactor targets flagged?"""
    before_root = os.path.join(history_dir, repo_name, candidate["short_hash"], "before")
    flagged = _flagged_classes(before_root)

    targets = refactor_targets(candidate)
    target_results = []
    for target in targets:
        entry = flagged.get((target["file"], target["class"]))
        target_results.append(
            {
                **target,
                "flagged": entry is not None,
                "flagged_as": entry["predicted_smell"] if entry else None,
                "confidence": round(entry["confidence"], 3) if entry else None,
            }
        )

    changed = set(candidate.get("files_changed", []))
    file_level_hits = sorted(
        {name for (path, name) in flagged if path in changed}
    )

    return {
        "commit_hash": candidate["commit_hash"],
        "short_hash": candidate["short_hash"],
        "message": candidate.get("message", ""),
        "detected_by": candidate.get("detected_by", []),
        "signals": candidate.get("signals", []),
        "has_structural_target": bool(targets),
        "targets": target_results,
        "targets_total": len(target_results),
        "targets_flagged": sum(1 for t in target_results if t["flagged"]),
        "hit": any(t["flagged"] for t in target_results),
        # Weak secondary signal -- see module docstring.
        "any_class_flagged_in_changed_files": bool(file_level_hits),
        "classes_flagged_in_changed_files": file_level_hits,
        "classes_flagged_total": len(flagged),
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _bucket(detected_by: list[str]) -> str:
    has_kw = "keyword" in detected_by
    has_st = "structural" in detected_by
    if has_kw and has_st:
        return "both"
    if has_st:
        return "structural"
    if has_kw:
        return "keyword"
    return "unknown"


def summarize(commit_results: list[dict]) -> dict:
    """Aggregate hit rates, broken down by smell type and by detector."""
    targeted = [r for r in commit_results if r["has_structural_target"]]
    hits = [r for r in targeted if r["hit"]]

    by_smell: dict[str, int] = {}
    by_signal: dict[str, dict] = {}
    for result in targeted:
        for target in result["targets"]:
            signal_stats = by_signal.setdefault(
                target["signal"], {"targets": 0, "flagged": 0}
            )
            signal_stats["targets"] += 1
            if target["flagged"]:
                signal_stats["flagged"] += 1
                by_smell[target["flagged_as"]] = by_smell.get(target["flagged_as"], 0) + 1

    by_detector: dict[str, dict] = {}
    for result in commit_results:
        bucket = by_detector.setdefault(
            _bucket(result["detected_by"]),
            {"commits": 0, "with_target": 0, "hits": 0, "file_level_hits": 0},
        )
        bucket["commits"] += 1
        if result["has_structural_target"]:
            bucket["with_target"] += 1
            if result["hit"]:
                bucket["hits"] += 1
        if result["any_class_flagged_in_changed_files"]:
            bucket["file_level_hits"] += 1

    all_targets = [t for r in targeted for t in r["targets"]]
    flagged_targets = [t for t in all_targets if t["flagged"]]

    file_level = [r for r in commit_results if r["any_class_flagged_in_changed_files"]]

    return {
        "commits_evaluated": len(commit_results),
        "commits_with_structural_target": len(targeted),
        "commits_without_structural_target": len(commit_results) - len(targeted),
        "commits_hit": len(hits),
        "targeted_hit_rate": round(len(hits) / len(targeted), 4) if targeted else None,
        "targets_total": len(all_targets),
        "targets_flagged": len(flagged_targets),
        "target_level_hit_rate": (
            round(len(flagged_targets) / len(all_targets), 4) if all_targets else None
        ),
        "file_level_hits": len(file_level),
        "file_level_hit_rate": (
            round(len(file_level) / len(commit_results), 4) if commit_results else None
        ),
        "by_smell_type": dict(sorted(by_smell.items(), key=lambda kv: -kv[1])),
        "by_signal": by_signal,
        "by_detector": by_detector,
    }


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _manifest_path(history_dir: str, repo_name: str) -> str:
    return os.path.join(history_dir, f"{repo_name}.json")


# Below this many available commits, a repo's git-reported "shallow" flag is
# treated as the cause of a thin result. Above it, the flag is IGNORED for
# this purpose: `git fetch --depth=N` on an already-shallow clone deepens the
# history (this project's own corpus repos are deepened exactly this way --
# see the "Evaluating against real refactoring history" README section) but
# git keeps reporting `rev-parse --is-shallow-repository` as true forever
# after, even with thousands of commits available. Gating on the boolean
# alone would wrongly tell someone with 1795 real commits to "deepen the
# clone" when there is nothing to fix.
_THIN_HISTORY_THRESHOLD = 5


def _repo_notes(manifest: dict) -> list[str]:
    """Human-readable diagnostics for a repo that mined zero (or few)
    candidates, so the CLI has something better to show than an empty table.

    Two genuinely different situations produce the same "0 candidates" list,
    and they need different advice:

      * genuinely THIN history (a fresh `git clone --depth 1`, or simply a
        very young repo) has no parent commits to diff most things against,
        so mining cannot find much regardless of what the project's real
        history eventually contains;
      * a repo with plenty of history can legitimately have no commits
        matching either detector in the window scanned.

    Reported for every repo, not only empty ones -- a thin clone that DID
    find a candidate from its handful of commits still deserves the caveat
    that its history is truncated.
    """
    notes: list[str] = []
    shallow = manifest.get("shallow_clone")
    available = manifest.get("commits_available") or 0
    scanned = manifest.get("commits_scanned")
    found = manifest.get("candidate_count", len(manifest.get("candidates", [])))

    # Only a THIN shallow clone gets the "deepen it" advice -- that is the
    # one case with an actual fix. A shallow-but-deep clone (see the
    # threshold comment above) falls through to the ordinary "found nothing"
    # message below, same as any other repo.
    if shallow and available <= _THIN_HISTORY_THRESHOLD:
        notes.append(
            f"This repository is a SHALLOW clone ({available} commit(s) available) -- "
            f"there is little or no history to diff against, so mining cannot find much "
            f"regardless of the project's real history. Deepen it first, e.g.:\n"
            f"    git -C {manifest.get('repo_path', '<repo>')} fetch --depth=1000 origin\n"
            f"then re-run with --remine."
        )
    elif found == 0:
        notes.append(
            f"No refactoring-like commits found by keyword or structural detectors in the "
            f"last {scanned} commit(s) ({available} available in this clone). This "
            f"repository's history may not use refactor-style commits in that window, or "
            f"nothing structurally matched our heuristics there. Try a larger --max-commits."
        )

    return notes


def evaluate_mined_repo(history_dir: str, repo_name: str) -> dict:
    """Evaluate a repo already mined into `history_dir`."""
    path = _manifest_path(history_dir, repo_name)
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)

    commit_results = [
        evaluate_candidate(candidate, history_dir, repo_name)
        for candidate in manifest.get("candidates", [])
    ]
    return {
        "repo": repo_name,
        "manifest": path,
        "shallow_clone": manifest.get("shallow_clone"),
        "commits_available": manifest.get("commits_available"),
        "commits_scanned": manifest.get("commits_scanned"),
        "notes": _repo_notes(manifest),
        "commits": commit_results,
        "summary": summarize(commit_results),
    }


def available_repos(history_dir: str) -> list[str]:
    """Repo names already mined into `history_dir`, alphabetically."""
    if not os.path.isdir(history_dir):
        return []
    return sorted(
        os.path.splitext(name)[0]
        for name in os.listdir(history_dir)
        if name.endswith(".json")
    )


def evaluate(
    repo_path: str | None,
    history_dir: str,
    remine: bool = False,
    scan_limit: int = 400,
    max_candidates: int = 40,
    on_progress=None,
) -> dict:
    """Evaluate one repo, mining it first if it has not been mined yet.

    `repo_path` may be a path to a git repository, or the bare name of a repo
    already mined into `history_dir`. Passing None evaluates every repo
    already mined there, which is how the whole extracted set is reported at
    once. Mining is delegated to git_mining.extract_refactoring_dataset --
    this module never re-implements it.

    `scan_limit` is the ONLY commit-count knob (there is no separate
    "max commits" parameter to keep in sync with it -- see the CLI's
    --max-commits, which maps directly onto this). `max_candidates` is a
    distinct cap on how many matches to keep, not a duplicate of scan_limit.

    `on_progress`, if given, is forwarded to git_mining.find_refactoring_candidates
    for exactly the case that needs it: mining a repo that was NOT already
    mined, where the scan can be slow on a large history. Evaluating an
    already-mined repo never calls it -- there is nothing slow left to do.
    """
    if repo_path is None:
        repos = available_repos(history_dir)
        if not repos:
            raise ValueError(
                f"No mined repositories under {history_dir!r}. Pass a repository path "
                f"to mine one first."
            )
        return _combine([evaluate_mined_repo(history_dir, name) for name in repos])

    name = os.path.basename(os.path.abspath(str(repo_path).rstrip("/\\")))
    already_mined = os.path.exists(_manifest_path(history_dir, name))

    if not already_mined or remine:
        if not os.path.isdir(repo_path):
            raise ValueError(
                f"{repo_path!r} has not been mined and is not a directory to mine. "
                f"Known mined repos: {', '.join(available_repos(history_dir)) or 'none'}"
            )
        extract_refactoring_dataset(
            repo_path,
            history_dir,
            repo_name=name,
            scan_limit=scan_limit,
            max_candidates=max_candidates,
            on_progress=on_progress,
        )

    return _combine([evaluate_mined_repo(history_dir, name)])


def _combine(repo_results: list[dict]) -> dict:
    all_commits = [c for r in repo_results for c in r["commits"]]
    all_notes = [
        f"[{r['repo']}] {note}" for r in repo_results for note in r.get("notes", [])
    ]
    return {
        "caveat": CAVEAT,
        "notes": all_notes,
        "repos": repo_results,
        "overall": summarize(all_commits),
    }
