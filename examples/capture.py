"""Capture a producer before model ingestion; return references, never its output.

Usage: python capture.py --directory ABSOLUTE_NEW_DIRECTORY -- PROGRAM [ARGS...]
The producer's exit code is preserved. Originals are never automatically removed.
"""
import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            result.update(block)
    return result.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or not Path(args.directory).is_absolute():
        parser.error("An absolute new directory and a producer command are required")
    directory = Path(args.directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    stdout, stderr = directory / "stdout.log", directory / "stderr.log"
    started = time.monotonic()
    with stdout.open("xb") as out, stderr.open("xb") as err:
        try:
            process = subprocess.run(command, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            status, error = process.returncode, None
        except OSError:
            status, error = 127, "producer_launch_failed"
    bundle = {"version": 1, "producer_exit_status": status, "error_code": error,
              "elapsed_ms": (time.monotonic() - started) * 1000,
              "stream_order": "stdout_and_stderr_preserved_separately", "originals_user_owned": True,
              "streams": {name: {"path": str(path), "sha256": digest(path), "bytes": path.stat().st_size}
                          for name, path in (("stdout", stdout), ("stderr", stderr))}}
    manifest = directory / "capture.json"
    with manifest.open("x", encoding="utf-8") as stream:
        json.dump(bundle, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"capture": str(manifest), "source_sha256": digest(manifest),
                      "producer_exit_status": status}))
    return status if status >= 0 else 128 - status


if __name__ == "__main__":
    raise SystemExit(main())
