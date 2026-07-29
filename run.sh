#!/bin/sh
# Launch Bash Editor on macOS or Linux, setting up the environment on first run.
#
# Creates .venv if it is missing, installs the requirements when they change,
# then starts the editor. Any argument is passed through, so
# `./run.sh /path/to/SCUS_945.70` opens that game straight away.
#
# run.command is the Finder-clickable wrapper for macOS; run.bat is the
# Windows equivalent of this script.

set -e
cd "$(dirname "$0")"

MINIMUM="3.10"

PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c \
        'import sys; raise SystemExit(sys.version_info < (3, 10))' 2>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "Bash Editor needs Python $MINIMUM or newer, and none was found on PATH." >&2
    echo "  macOS:  brew install python" >&2
    echo "  Debian: sudo apt install python3 python3-venv" >&2
    echo "  Fedora: sudo dnf install python3" >&2
    exit 1
fi

if [ ! -x .venv/bin/python ]; then
    echo "Creating the virtual environment in .venv ..."
    if ! "$PYTHON" -m venv .venv; then
        echo "Could not create the virtual environment." >&2
        echo "On Debian and Ubuntu it lives in a separate package:" >&2
        echo "  sudo apt install python3-venv" >&2
        exit 1
    fi
fi

# The stamp is a copy of the requirements the venv was last built against, so a
# changed requirements.txt reinstalls and an unchanged one costs nothing.
if ! cmp -s requirements.txt .venv/requirements.stamp; then
    echo "Installing dependencies ..."
    .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
    cp requirements.txt .venv/requirements.stamp
fi

exec .venv/bin/python app/main.py "$@"
