"""Launch the single-file extraction CLI through python -m tender_extraction.

The module entry point delegates argument parsing and execution to cli.main()
and propagates its exit code. Executing this module requires an explicit input
file; a configured extraction run can make billable Azure API calls.
"""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
