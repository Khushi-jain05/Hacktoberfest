#!/usr/bin/env bash
# Clone spider-python as a real-world test bed for the Problem 01 tool.
#   ./spider_setup.sh [dir]            plain clone, no provenance (TC-S13/S14)
#   ./spider_setup.sh [dir] --notes    also add SIMULATED test/deploy notes (TC-S15)
set -euo pipefail
DIR=${1:-spider-python}
git clone -q https://github.com/Koray-afk/spider-python.git "$DIR"
cd "$DIR"
# local branch for every remote branch, so test cases can name them directly
for b in $(git branch -r | grep -v HEAD | sed 's#origin/##'); do
  git branch -q -f "$b" "origin/$b" 2>/dev/null || true
done

if [[ "${2:-}" == "--notes" ]]; then
  # Simulated records on real commits — these runs never happened.
  note() { git notes --ref=tests append -m "$2" "$1"; }
  note 3b795a84 '{"env":"uat","kind":"test","result":"pass","source":"ci","simulated":true}'
  note 3b795a84 '{"env":"production","kind":"deploy","result":"success","source":"deploy-log","simulated":true}'
  note de3e1de7 '{"env":"dev","kind":"test","result":"pass","source":"ci","simulated":true}'
  note 95583a8f '{"env":"uat","kind":"test","result":"fail","source":"ci","simulated":true}'
fi
echo "ready: $(pwd)"
