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
    "simplify",
    "decompose",
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


def _validate_git_repo(repo_path: str) -> None:
    if not os.path.isdir(repo_path):
        raise ValueError(f"{repo_path!r} is not a directory")

    result = subprocess.run(
        ["git", "-C", repo_path, "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise ValueError(
            f"{repo_path!r} is not inside a git repository "
            f"(git rev-parse --is-inside-work-tree failed: {result.stderr.strip()})"
        )


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
