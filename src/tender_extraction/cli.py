"""Provide command-line access to a single local tender extraction run.

main(argv=None) parses the required --input argument and optional --output-dir,
loads configuration, and calls pipeline.run_file(). It prints a short status
and returns exit code 0 for completed, 2 for needs_review, or 1 for failure.
Argument parsing also provides help and rejects missing input arguments.
"""
import argparse
from .config import Config
from .errors import ExtractionError
from .pipeline import run_file


def main(argv=None):
    parser = argparse.ArgumentParser(description="Genau eine Tenderdatei lokal verarbeiten (Azure-API-Aufrufe sind kostenpflichtig).")
    parser.add_argument("--input",required=True,help="Konkrete .xlsx/.docx/.pdf innerhalb sample_inputs/")
    parser.add_argument("--output-dir",default="outputs")
    args = parser.parse_args(argv)
    try:
        folder,run = run_file(args.input,args.output_dir,Config.from_env())
    except ExtractionError as e:
        print(f"{e.code}: {e}")
        return 1
    print(f"{run['status']}: {folder}")
    if run.get("error"):
        print(run["error"]["code"]+": "+run["error"]["message"])
    return {"completed":0,"needs_review":2,"failed":1}[run["status"]]
