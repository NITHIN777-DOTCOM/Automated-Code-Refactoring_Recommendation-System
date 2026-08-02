"""Local dev convenience wrapper: `python run_scan.py <command>`.

The actual CLI app lives in engine.cli.main so it can also be installed as
the `refactor-scan` console script (see pyproject.toml [project.scripts]).
"""

from __future__ import annotations

from engine.cli.main import main

if __name__ == "__main__":
    main()
