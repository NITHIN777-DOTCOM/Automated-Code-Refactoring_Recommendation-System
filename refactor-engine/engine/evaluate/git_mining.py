"""Mine a local git repository's history for commits that look like refactors,
and pull the pre-commit version of the files they touched for before/after
comparison.

This is read-only tooling: every git operation here is either a query
(`git log`, `git show`, `git diff-tree`) or a write into a caller-supplied
output directory -- nothing here ever runs `git checkout`, `git reset`, or
anything else that could move the user's actual working tree out from under
them.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile

from engine.parser import parse_file

logger = logging.getLogger(__name__)

DEFAULT_KEYWORDS = [
    "refactor",
    "extract method",
    "extract class",
    "split class",
    "clean up",
    "cleanup",
    "reorganize",
    "reorganise",
    "simplify",
    "decompose",
    "move method",
    "rename",
    "tidy",
]

# A safety ceiling independent of whatever the caller passes as max_commits --
# this walks git history and shells out per matching commit (one subprocess
# call per changed file), so an accidental max_commits=100000 must not be
# allowed to run unbounded.
_MAX_COMMITS_CEILING = 200


def _run_git(repo_path: str, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", repo_path, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo_path!r}: {result.stderr.strip()}")
    return result.stdout


def _normalize(path: str) -> str:
    """A comparable form of `path`: absolute, symlink-resolved, case-folded on
    Windows, and with git's forward slashes turned back into os.sep."""
    return os.path.normcase(os.path.realpath(os.path.abspath(str(path))))


def _git_toplevel(path: str) -> str | None:
    """The work-tree root containing `path`, or None if there is no repo above it."""
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return _normalize(result.stdout.strip())


def _validate_git_repo(repo_path: str) -> None:
    """Require `repo_path` to be a git repository ROOT, not merely a path that
    happens to sit inside one.

    The stricter check exists because the looser one has a silent failure
    mode that is worse than an error. `git rev-parse --is-inside-work-tree`
    walks UP the directory tree, so a path that is not a repository at all
    still answers "true" whenever any ancestor is one -- and this is not
    hypothetical: a developer with `git init` run in their home directory
    makes every path under it, including every pytest temporary directory,
    report as being inside a work tree. Mining would then quietly run against
    the ANCESTOR repository's history and return commits from a completely
    unrelated project, with nothing raising.

    Requiring the root makes that case an error, and the error names the root
    it found so the caller can pass the right path.
    """
    if not os.path.isdir(repo_path):
        raise ValueError(f"{repo_path!r} is not a directory")

    toplevel = _git_toplevel(repo_path)
    if toplevel is None:
        raise ValueError(
            f"{repo_path!r} is not a git repository (and is not inside a git repository)"
        )

    if toplevel != _normalize(repo_path):
        raise ValueError(
            f"{repo_path!r} is not a git repository root -- it sits inside the repository "
            f"at {toplevel!r}. Pass that path instead; mining a subdirectory would "
            f"silently return the enclosing repository's history."
        )


def is_shallow_clone(repo_path: str) -> bool:
    """True when `repo_path` was cloned with --depth (a truncated history).

    Matters here specifically: mining walks commit-by-commit and diffs each
    one against its parent, so a --depth 1 clone (the default this project's
    own corpus repos were fetched with -- see data/real_world/collect_corpus.py)
    has exactly one commit and NO parent to diff against. That is not "this
    repo has no refactors", it is "this repo has no history to look in", and
    the two need different messages: one says "try a bigger --max-commits",
    the other says "deepen the clone first".
    """
    result = subprocess.run(
        ["git", "-C", repo_path, "rev-parse", "--is-shallow-repository"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def commit_count(repo_path: str) -> int:
    """Commits reachable from HEAD. For a shallow clone this is only what was
    fetched, not the project's real history -- callers pair it with
    is_shallow_clone() rather than reading it alone as "the whole project."""
    result = subprocess.run(
        ["git", "-C", repo_path, "rev-list", "--count", "HEAD"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def _changed_python_files(repo_path: str, commit_hash: str) -> list[str]:
    output = _run_git(repo_path, ["diff-tree", "--no-commit-id", "--name-only", "-r", commit_hash])
    return [line for line in output.splitlines() if line.endswith(".py")]


def _classes_in_blob(repo_path: str, ref: str, file_path: str) -> list[str]:
    """Classes defined in `file_path` as it existed at `ref` (a commit-ish like
    "<hash>" or "<hash>^"). Returns [] rather than raising for a file that
    doesn't exist at that ref -- an added file has no "before" blob, a
    deleted file has no "after" blob, and neither is an error worth stopping
    the whole scan for."""
    result = subprocess.run(
        ["git", "-C", repo_path, "show", f"{ref}:{file_path}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return []

    # parse_file() takes a path, not source text -- there's no in-memory
    # variant, so the blob content is written to a throwaway file purely to
    # reuse the existing parser rather than duplicating its AST-walking logic
    # here.
    fd, tmp_path = tempfile.mkstemp(suffix=".py")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(result.stdout)
        classes = parse_file(tmp_path)
    except (SyntaxError, UnicodeDecodeError, OSError) as exc:
        logger.warning("Could not parse %s at %s: %s", file_path, ref, exc)
        return []
    finally:
        os.remove(tmp_path)

    return [c.name for c in classes]


def find_refactor_commits(
    repo_path: str, keywords: list[str] | None = None, max_commits: int = 20
) -> list[dict]:
    """Commits on the current branch whose message mentions any of `keywords`
    (case-insensitive substring match), most recent first.

    Each result is {"commit_hash", "message", "files_changed", "classes_touched"}:
    - files_changed is every .py file the commit touched.
    - classes_touched is every class name found across those files' contents
      AT that commit (not the parent) -- a file the commit deleted
      contributes nothing, a file it added contributes whatever classes it
      defines.

    `max_commits` caps how many matching commits are returned, and is itself
    clamped to `_MAX_COMMITS_CEILING` regardless of what's passed in, so a
    caller can't accidentally trigger a scan across thousands of commits.
    """
    _validate_git_repo(repo_path)

    keywords = keywords if keywords is not None else DEFAULT_KEYWORDS
    if not keywords:
        raise ValueError("keywords must be a non-empty list (or None to use the defaults)")

    max_commits = max(1, min(max_commits, _MAX_COMMITS_CEILING))

    grep_args = []
    for kw in keywords:
        grep_args += ["--grep", kw]

    # -i: case-insensitive. Multiple --grep patterns are OR'd by git by
    # default, which is exactly "matches any keyword". -n caps the walk at
    # the source rather than filtering a longer log afterwards.
    output = _run_git(repo_path, ["log", f"-n{max_commits}", "-i", *grep_args, "--format=%H"])
    commit_hashes = [line for line in output.splitlines() if line]

    results = []
    for commit_hash in commit_hashes:
        message = _run_git(repo_path, ["log", "-1", "--format=%B", commit_hash]).strip()
        files_changed = _changed_python_files(repo_path, commit_hash)

        classes_touched: list[str] = []
        seen = set()
        for file_path in files_changed:
            for class_name in _classes_in_blob(repo_path, commit_hash, file_path):
                if class_name not in seen:
                    seen.add(class_name)
                    classes_touched.append(class_name)

        results.append(
            {
                "commit_hash": commit_hash,
                "message": message,
                "files_changed": files_changed,
                "classes_touched": classes_touched,
            }
        )

    return results


def _reject_output_dir_inside_repo(repo_path: str, output_dir: str) -> None:
    """Refuse to write extraction output anywhere inside the repository.

    This module's contract is that mining leaves the user's working tree
    exactly as it found it. Fetching blobs with `git show` already guarantees
    no TRACKED file is modified, but writing the output INTO the work tree
    would still leave untracked files behind and turn `git status` dirty --
    which is the same promise broken by a different route, and is exactly
    what a caller passing a relative output path is likely to do by accident.
    """
    repo_root = _normalize(repo_path)
    destination = _normalize(output_dir)

    if destination == repo_root or destination.startswith(repo_root + os.sep):
        raise ValueError(
            f"output_dir {output_dir!r} is inside the repository at {repo_path!r}. "
            f"Extraction must not write into the work tree -- it would leave untracked "
            f"files behind and dirty `git status`. Choose a directory outside the repo."
        )


def checkout_before_state(repo_path: str, commit_hash: str, output_dir: str) -> str:
    """Write the pre-commit ("before") version of every .py file `commit_hash`
    touched into `output_dir`, and return `output_dir`.

    This never touches the caller's actual working tree: every file is
    fetched with `git show <parent>:<path>` (a read-only blob lookup) and
    written under `output_dir`, never restored in place with `git checkout`.
    A file the commit added (so it has no parent version) is skipped with a
    warning rather than failing the whole call -- the same is true if
    `commit_hash` is the repository's very first commit, which has no parent
    at all.
    """
    _validate_git_repo(repo_path)
    _reject_output_dir_inside_repo(repo_path, output_dir)

    parent_ref = f"{commit_hash}^"
    files_changed = _changed_python_files(repo_path, commit_hash)

    os.makedirs(output_dir, exist_ok=True)

    for file_path in files_changed:
        result = subprocess.run(
            ["git", "-C", repo_path, "show", f"{parent_ref}:{file_path}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            logger.warning(
                "No pre-commit version of %s for %s (added by this commit, or %s has no parent): %s",
                file_path,
                commit_hash,
                commit_hash,
                result.stderr.strip(),
            )
            continue

        # file_path uses git's always-forward-slash paths; os.path.join
        # handles that fine on Windows too.
        destination = os.path.join(output_dir, file_path)
        os.makedirs(os.path.dirname(destination) or output_dir, exist_ok=True)
        with open(destination, "w", encoding="utf-8") as f:
            f.write(result.stdout)

    return output_dir


# ---------------------------------------------------------------------------
# Structural refactor detection
#
# A commit message is weak evidence: plenty of real refactors say "tidy" or
# nothing at all, and plenty of commits saying "refactor" also change
# behaviour. The signals below read the DIFF instead, comparing the parsed
# class/method structure of each changed file before and after the commit.
#
# Every signal is deliberately conservative -- it is better to miss a real
# refactor than to fill an evaluation set with behaviour changes, because a
# false positive here becomes a wrong "expected refactoring" downstream.
# ---------------------------------------------------------------------------

# Insertions and deletions this close in size read as "code moved around"
# rather than "code added". 0.0 = perfectly balanced. Refactors preserve
# behaviour, so they tend to delete about as much as they add.
BALANCED_CHURN_RATIO = 0.35

# A commit touching more than this many .py files is usually a sweep (a
# reformat, a dependency bump, a mass rename) rather than a single
# refactoring worth extracting as one before/after example.
MAX_FILES_FOR_CANDIDATE = 12

# How much of a class's method set must survive for "renamed" to beat the
# simpler explanation that one class was deleted and another added.
RENAME_OVERLAP_RATIO = 0.6

# Extraction reshapes a class without inflating it. A class that gained
# methods AND grew past this multiple is new behaviour, not an extraction.
EXTRACTION_MAX_GROWTH = 1.25


def _classes_at(repo_path: str, ref: str, file_path: str):
    """Parsed ClassInfo list for a file at a ref -- [] if absent or unparsable."""
    result = subprocess.run(
        ["git", "-C", repo_path, "show", f"{ref}:{file_path}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        return []

    fd, tmp_path = tempfile.mkstemp(suffix=".py")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(result.stdout)
        return parse_file(tmp_path)
    except (SyntaxError, UnicodeDecodeError, OSError, ValueError, RecursionError):
        return []
    finally:
        os.remove(tmp_path)


def _method_owner_map(classes) -> dict:
    """method name -> set of classes defining it, for one version of a file."""
    owners: dict[str, set] = {}
    for cls in classes:
        for method in cls.methods:
            owners.setdefault(method.name, set()).add(cls.name)
    return owners


def _churn(repo_path: str, commit_hash: str) -> tuple[int, int]:
    """(insertions, deletions) summed across the commit's .py files."""
    output = _run_git(
        repo_path, ["show", "--numstat", "--format=", "--no-renames", commit_hash]
    )
    insertions = deletions = 0
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or not parts[2].endswith(".py"):
            continue
        if parts[0] == "-" or parts[1] == "-":
            continue  # binary file: nothing to count
        insertions += int(parts[0])
        deletions += int(parts[1])
    return insertions, deletions


def structural_signals(repo_path: str, commit_hash: str) -> dict:
    """Structural evidence that `commit_hash` is a refactoring.

    Returns the raw evidence plus a `signals` list naming what was found:

      * "method_moved_between_classes" -- a method name left one class and
        appeared on a different one in the same commit. The clearest
        machine-detectable refactoring there is.
      * "class_renamed" -- one class disappeared and another appeared in the
        same file sharing most of its method names.
      * "method_extracted" -- a class gained methods while staying roughly
        the same size: new methods carved out of existing bodies rather than
        new behaviour bolted on.
      * "balanced_churn" -- insertions and deletions are close in size.
        Supporting evidence ONLY. On its own it is far too weak to qualify a
        commit, since any edit-in-place change looks like this, which is why
        `structural_match` ignores it.
    """
    files_changed = _changed_python_files(repo_path, commit_hash)
    insertions, deletions = _churn(repo_path, commit_hash)

    signals: list[str] = []
    details: dict[str, list] = {
        "methods_moved": [],
        "classes_renamed": [],
        "classes_gaining_methods": [],
    }

    for file_path in files_changed:
        before = _classes_at(repo_path, f"{commit_hash}^", file_path)
        after = _classes_at(repo_path, commit_hash, file_path)
        if not before and not after:
            continue

        before_owners = _method_owner_map(before)
        after_owners = _method_owner_map(after)

        for name, owners_before in before_owners.items():
            owners_after = after_owners.get(name)
            if not owners_after or owners_after == owners_before:
                continue
            gained = owners_after - owners_before
            lost = owners_before - owners_after
            if gained and lost:
                details["methods_moved"].append(
                    {
                        "file": file_path,
                        "method": name,
                        "from_class": sorted(lost),
                        "to_class": sorted(gained),
                    }
                )

        before_by_name = {c.name: c for c in before}
        after_by_name = {c.name: c for c in after}
        removed = set(before_by_name) - set(after_by_name)
        added = set(after_by_name) - set(before_by_name)

        for old_name in sorted(removed):
            old_methods = {m.name for m in before_by_name[old_name].methods}
            if not old_methods:
                continue
            for new_name in sorted(added):
                new_methods = {m.name for m in after_by_name[new_name].methods}
                if not new_methods:
                    continue
                overlap = len(old_methods & new_methods)
                ratio = overlap / max(len(old_methods), len(new_methods))
                if ratio >= RENAME_OVERLAP_RATIO:
                    details["classes_renamed"].append(
                        {
                            "file": file_path,
                            "from": old_name,
                            "to": new_name,
                            "shared_methods": overlap,
                        }
                    )

        for name, after_cls in after_by_name.items():
            before_cls = before_by_name.get(name)
            if before_cls is None:
                continue
            gained_methods = len(after_cls.methods) - len(before_cls.methods)
            if gained_methods <= 0:
                continue
            before_span = before_cls.end_line - before_cls.start_line + 1
            after_span = after_cls.end_line - after_cls.start_line + 1
            if after_span <= before_span * EXTRACTION_MAX_GROWTH:
                details["classes_gaining_methods"].append(
                    {
                        "file": file_path,
                        "class": name,
                        "methods_gained": gained_methods,
                        "lines_before": before_span,
                        "lines_after": after_span,
                    }
                )

    if details["methods_moved"]:
        signals.append("method_moved_between_classes")
    if details["classes_renamed"]:
        signals.append("class_renamed")
    if details["classes_gaining_methods"]:
        signals.append("method_extracted")

    total = insertions + deletions
    if total and abs(insertions - deletions) / total <= BALANCED_CHURN_RATIO:
        signals.append("balanced_churn")

    return {
        "signals": signals,
        "structural_match": any(s != "balanced_churn" for s in signals),
        "insertions": insertions,
        "deletions": deletions,
        "files_changed": files_changed,
        "details": details,
    }


def _matched_keywords(message: str, keywords: list[str]) -> list[str]:
    lowered = message.lower()
    return [kw for kw in keywords if kw.lower() in lowered]


# Hard ceiling on scan_limit regardless of what a caller passes, independent
# of the CLI's own default. Structural detection shells out to `git show`
# per changed file per commit, so an accidental scan_limit=1000000 on a huge
# repo must not be allowed to run unbounded -- see MAX_SCAN_LIMIT_CEILING.
MAX_SCAN_LIMIT_CEILING = 5000


def find_refactoring_candidates(
    repo_path: str,
    keywords: list[str] | None = None,
    scan_limit: int = 300,
    max_candidates: int = 50,
    require_structural: bool = False,
    on_progress=None,
) -> list[dict]:
    """Walk recent history and return commits that look like refactorings.

    Two independent detectors, reported separately in `detected_by` so the
    evaluation set can be filtered either way afterwards: message keywords,
    and structural diff signals. `require_structural` drops keyword-only
    commits, which is the setting to use when the message cannot be trusted.

    `scan_limit` is how many commits back (from HEAD) to look -- clamped to
    MAX_SCAN_LIMIT_CEILING regardless of what's passed in. `max_candidates`
    is a SEPARATE cap on how many matches to keep before stopping early; the
    two are not redundant -- a large, quiet repo can need a big scan_limit to
    find even a handful of candidates.

    `on_progress`, if given, is called as `on_progress(commits_walked,
    commits_to_walk, candidates_found)` after each commit is examined --
    structural diffing is the slow part of this function (one or more `git
    show` calls per commit), so a caller mining thousands of commits can use
    this to render a progress indicator instead of sitting with no feedback.
    """
    _validate_git_repo(repo_path)
    keywords = keywords if keywords is not None else DEFAULT_KEYWORDS

    scan_limit = max(1, min(scan_limit, MAX_SCAN_LIMIT_CEILING))
    log = _run_git(repo_path, ["log", f"-n{scan_limit}", "--format=%H%x1f%s"])
    lines = [line for line in log.splitlines() if "\x1f" in line]
    total = len(lines)

    def _report(walked: int) -> None:
        if on_progress is not None:
            on_progress(walked, total, len(candidates))

    candidates: list[dict] = []
    for walked, line in enumerate(lines, start=1):
        commit_hash, subject = line.split("\x1f", 1)

        files_changed = _changed_python_files(repo_path, commit_hash)
        if not files_changed or len(files_changed) > MAX_FILES_FOR_CANDIDATE:
            _report(walked)
            continue

        matched = _matched_keywords(subject, keywords)
        structural = structural_signals(repo_path, commit_hash)

        detected_by = []
        if matched:
            detected_by.append("keyword")
        if structural["structural_match"]:
            detected_by.append("structural")
        if not detected_by or (require_structural and "structural" not in detected_by):
            _report(walked)
            continue

        candidates.append(
            {
                "commit_hash": commit_hash,
                "message": subject,
                "detected_by": detected_by,
                "matched_keywords": matched,
                "signals": structural["signals"],
                "files_changed": files_changed,
                "insertions": structural["insertions"],
                "deletions": structural["deletions"],
                "details": structural["details"],
            }
        )
        _report(walked)
        if len(candidates) >= max_candidates:
            break

    return candidates


def checkout_after_state(repo_path: str, commit_hash: str, output_dir: str) -> str:
    """The commit's own ("after") version of each .py file it touched.

    Mirror image of checkout_before_state(), under the same rule: read-only
    blob lookups, never a write into the work tree.
    """
    _validate_git_repo(repo_path)
    _reject_output_dir_inside_repo(repo_path, output_dir)

    os.makedirs(output_dir, exist_ok=True)
    for file_path in _changed_python_files(repo_path, commit_hash):
        result = subprocess.run(
            ["git", "-C", repo_path, "show", f"{commit_hash}:{file_path}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            logger.warning("No post-commit version of %s at %s", file_path, commit_hash)
            continue
        destination = os.path.join(output_dir, file_path)
        os.makedirs(os.path.dirname(destination) or output_dir, exist_ok=True)
        with open(destination, "w", encoding="utf-8") as f:
            f.write(result.stdout)

    return output_dir


def extract_refactoring_dataset(
    repo_path: str,
    output_dir: str,
    repo_name: str | None = None,
    **finder_kwargs,
) -> dict:
    """Find refactoring candidates and materialise their before/after states.

    Layout under `output_dir`:

        <repo_name>/<short_hash>/before/<path/to/file.py>
        <repo_name>/<short_hash>/after/<path/to/file.py>

    Returns the manifest, also written to <output_dir>/<repo_name>.json, with
    one entry per commit carrying the hash, message, detection evidence, the
    files changed, and the before/after path written for each file.
    """
    _validate_git_repo(repo_path)
    _reject_output_dir_inside_repo(repo_path, output_dir)

    repo_name = repo_name or os.path.basename(os.path.abspath(str(repo_path).rstrip("/\\")))

    # Diagnostics gathered BEFORE mining, not derived from a 0-candidate
    # result afterwards -- a shallow clone with one commit and a full-history
    # repo that legitimately has no matching commits both look like "0
    # candidates" from the candidate list alone, and callers need to tell
    # those apart to give useful advice (deepen the clone vs. widen the scan).
    shallow = is_shallow_clone(repo_path)
    available = commit_count(repo_path)

    candidates = find_refactoring_candidates(repo_path, **finder_kwargs)

    entries = []
    for candidate in candidates:
        short = candidate["commit_hash"][:12]
        commit_dir = os.path.join(output_dir, repo_name, short)
        before_dir = os.path.join(commit_dir, "before")
        after_dir = os.path.join(commit_dir, "after")

        checkout_before_state(repo_path, candidate["commit_hash"], before_dir)
        checkout_after_state(repo_path, candidate["commit_hash"], after_dir)

        pairs = []
        for file_path in candidate["files_changed"]:
            before_file = os.path.join(before_dir, file_path)
            after_file = os.path.join(after_dir, file_path)
            # A file the commit ADDED has no before-state and a file it
            # DELETED has no after-state; null rather than a path that is
            # not there.
            pairs.append(
                {
                    "file": file_path,
                    "before_path": (
                        os.path.relpath(before_file, output_dir).replace("\\", "/")
                        if os.path.exists(before_file) else None
                    ),
                    "after_path": (
                        os.path.relpath(after_file, output_dir).replace("\\", "/")
                        if os.path.exists(after_file) else None
                    ),
                }
            )

        entries.append({**candidate, "short_hash": short, "file_pairs": pairs})

    requested_scan = min(finder_kwargs.get("scan_limit", 300), MAX_SCAN_LIMIT_CEILING)
    manifest = {
        "repo": repo_name,
        "repo_path": os.path.abspath(str(repo_path)),
        "shallow_clone": shallow,
        "commits_available": available,
        # The number actually walked, not just what was requested -- a repo
        # with fewer commits than the requested scan_limit walked all of them.
        "commits_scanned": min(requested_scan, available) if available else requested_scan,
        "candidate_count": len(entries),
        "candidates": entries,
    }

    os.makedirs(output_dir, exist_ok=True)
    manifest_path = os.path.join(output_dir, f"{repo_name}.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    manifest["manifest_path"] = manifest_path

    return manifest
