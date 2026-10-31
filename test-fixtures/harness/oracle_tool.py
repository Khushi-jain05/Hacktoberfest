#!/usr/bin/env python3
"""Fake 'perfect' tool used by `run_tests.py --self-test`.

Prints the pre-computed answer for its arguments from $ORACLE_ANSWERS. For `resolve`
it actually applies the reference solution and finishes the git operation. It proves
the harness can tell a correct tool from a broken one before the real tool exists.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

CONTINUE = {"merge": ["commit", "-q", "--no-edit"], "cherry-pick": ["cherry-pick", "--continue"],
            "rebase": ["rebase", "--continue"]}

answers = json.load(open(os.environ["ORACLE_ANSWERS"]))
answer = answers.get(" ".join(sys.argv[1:]), {})

if answer.get("action") == "apply":
    for path, content in answer["files"].items():
        Path(path).write_text(content)
        subprocess.run(["git", "add", "--", path], check=True)
    subprocess.run(["git", *CONTINUE[answer["operation"]]], check=True, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    answer = {"resolved": True, "commit": head,
              "decisions": [{"file": p, "choice": "combined", "reason": "oracle"} for p in answer["files"]]}

print(json.dumps(answer))
