#!/usr/bin/env bash
# Adapter between run_tests.py and our tool. Copy to the repo root as
# tool-adapter.sh, make it executable, and point each line at the real CLI.
#
#   ./tool-adapter.sh conflicts        -> conflict report JSON
#   ./tool-adapter.sh status  <sha>    -> provenance JSON for one commit
#   ./tool-adapter.sh matches <sha>    -> equivalent-commit JSON
#
# Shapes and allowed status values: test-fixtures/CONTRACT.md
set -euo pipefail

# Absolute path of this repo, because run_tests.py runs us inside fixture repos.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${1:-}" in
  conflicts) exec python3 -m provenance conflicts --json ;;          # TODO: real command
  status)    exec python3 -m provenance status "$2" --json ;;        # TODO: real command
  matches)   exec python3 -m provenance matches "$2" --json ;;       # TODO: real command
  *) echo "usage: $0 conflicts | status <sha> | matches <sha>" >&2; exit 2 ;;
esac
