"""Shared fixtures for the provenance-tool test suite.

Two kinds of tests live here:
  * fixture checks  - prove each scenario repo is what it claims (always run);
  * tool tests      - ask the tool and check its answers (need the tool).

The tool is reached through one command, as described in test-fixtures/CONTRACT.md:
  PROVENANCE_TOOL="python -m provenance" pytest     # or put ./tool-adapter.sh in the repo root
Without it, tool tests are skipped (not failed), so CI stays green until the tool exists.
"""
import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from fixtures import create_branches
from fixtures.expect import STATUSES
from fixtures.sample_repo import clone_without_notes

ROOT = Path(__file__).resolve().parents[1]


class Tool:
    def __init__(self, argv):
        self.argv = argv

    def _call(self, repo, *args):
        p = subprocess.run(self.argv + list(args), cwd=repo.path, capture_output=True, text=True,
                           timeout=120, env=repo.env())
        assert p.returncode == 0, f"`tool {' '.join(args)}` exited {p.returncode}: {p.stderr[-500:]}"
        try:
            out = json.loads(p.stdout)
        except json.JSONDecodeError:
            pytest.fail(f"`tool {' '.join(args)}` did not print JSON: {p.stdout[:300]!r}")
        return out

    def conflicts(self, repo):
        return self._call(repo, "conflicts")

    def status(self, repo, sha):
        out = self._call(repo, "status", sha)
        assert out.get("status") in STATUSES, f"invalid status {out.get('status')!r}"
        return out

    def matches(self, repo, sha):
        return self._call(repo, "matches", sha)

    def resolve(self, repo):
        return self._call(repo, "resolve")


@pytest.fixture(scope="session")
def tool():
    cmd = os.environ.get("PROVENANCE_TOOL")
    if cmd:
        return Tool([cmd] if os.path.isfile(cmd) else shlex.split(cmd))
    if (ROOT / "tool-adapter.sh").is_file():
        return Tool([str(ROOT / "tool-adapter.sh")])
    pytest.skip("tool not connected: set PROVENANCE_TOOL or add tool-adapter.sh (see test-fixtures/CONTRACT.md)")


# Read-only scenarios are built once per session; conflict scenarios once per test.

@pytest.fixture(scope="session")
def pass_fail(tmp_path_factory):
    return create_branches.passing_and_failing(tmp_path_factory.mktemp("pass_fail") / "repo")


@pytest.fixture(scope="session")
def cherry(tmp_path_factory):
    return create_branches.cherry_picked_fix(tmp_path_factory.mktemp("cherry") / "repo")


@pytest.fixture(scope="session")
def stale(tmp_path_factory):
    return create_branches.changed_after_test(tmp_path_factory.mktemp("stale") / "repo")


@pytest.fixture(scope="session")
def production(tmp_path_factory):
    return create_branches.production_history(tmp_path_factory.mktemp("production") / "repo")


@pytest.fixture(scope="session")
def production_clone(production, tmp_path_factory):
    repo, commits = production
    return clone_without_notes(repo, tmp_path_factory.mktemp("clone") / "repo"), commits


@pytest.fixture
def conflict(tmp_path):
    return create_branches.conflict_dev_vs_uat(tmp_path / "repo")

