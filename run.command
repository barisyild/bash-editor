#!/bin/sh
# macOS: double-clicking this in Finder opens a Terminal window and starts the
# editor. It only exists because Finder runs .command files and not .sh ones --
# everything actually happens in run.sh.

exec "$(dirname "$0")/run.sh" "$@"
