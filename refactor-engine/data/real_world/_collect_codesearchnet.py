"""Worker process for pulling the CodeSearchNet Python subset.

Run as a SEPARATE process (see collect_corpus.py) rather than imported
in-process: streaming a HF `datasets` parquet iterator under this Python
build has been observed to print a spurious "Fatal Python error:
PyInterpreterState_Delete: remaining threads" from pyarrow's background IO
threads during interpreter shutdown, *after* all rows have already been
read and written to disk. Isolating it in a subprocess means that harmless
crash-on-exit can't take down the git-clone / py150 / site-packages work
that runs before and after it in the main collector.

Usage: python _collect_codesearchnet.py <out_dir> <target_count>
"""

from __future__ import annotations

import sys


def main() -> None:
    out_dir, target_count = sys.argv[1], int(sys.argv[2])

    import os

    os.makedirs(out_dir, exist_ok=True)

    from datasets import load_dataset

    # The dataset's script-based loader ("code_search_net", config "python")
    # is no longer usable under recent `datasets` releases (loading-script
    # execution was removed). The community mirror below exposes the same
    # rows pre-converted to parquet, which needs no script execution and
    # works with plain streaming -- avoids downloading the full split.
    ds = load_dataset(
        "code-search-net/code_search_net",
        "default",
        split="train",
        revision="refs/convert/parquet",
        streaming=True,
    )

    written = 0
    seen_funcs = set()
    for row in ds:
        if written >= target_count:
            break
        if row.get("language") != "python":
            continue
        code = row.get("func_code_string") or ""
        if not code.strip():
            continue
        # CodeSearchNet has near-duplicate entries scraped from forks of the
        # same repo; skip exact repeats so the sample isn't padded with
        # copies of the same function.
        key = (row.get("repository_name"), row.get("func_path_in_repository"), row.get("func_name"))
        if key in seen_funcs:
            continue
        seen_funcs.add(key)

        fname = os.path.join(out_dir, f"csn_{written:05d}.py")
        with open(fname, "w", encoding="utf-8") as f:
            f.write(code)
        written += 1

    print(f"WRITTEN={written}")


if __name__ == "__main__":
    main()
