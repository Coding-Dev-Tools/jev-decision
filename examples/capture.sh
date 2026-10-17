#!/bin/sh
# Usage: ./capture.sh ABSOLUTE_NEW_DIRECTORY PROGRAM [ARGS...]
# PYTHON selects the installed Python. No producer output enters this pipe.
if [ "$#" -lt 2 ]; then
  printf '%s\n' 'usage: capture.sh ABSOLUTE_NEW_DIRECTORY PROGRAM [ARGS...]' >&2
  exit 2
fi
capture_directory=$1
shift
capture_script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) || exit 2
exec "${PYTHON:-python3}" "$capture_script_dir/capture.py" --directory "$capture_directory" -- "$@"
