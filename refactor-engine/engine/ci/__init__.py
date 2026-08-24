"""CI integrations: turning a scan into something a code-review tool can show.

Lives inside the installed package rather than in a repo-local scripts/
directory on purpose -- the whole value proposition of the GitHub Action is
that ANY Python project can `pip install refactor-scan` and drop the workflow
in, without also vendoring a copy of the formatting script.
"""
