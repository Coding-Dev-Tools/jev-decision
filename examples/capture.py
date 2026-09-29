"""Capture a producer before model ingestion; return references, never its output.

Usage: python capture.py --directory ABSOLUTE_NEW_DIRECTORY -- PROGRAM [ARGS...]
The producer's exit code is preserved. Originals are never automatically removed.
"""
import runpy
from pathlib import Path

if __name__ == "__main__":
    # Retain the standalone checkout recipe while sharing the installed helper.
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "jev_decision/capture.py"), run_name="__main__")
