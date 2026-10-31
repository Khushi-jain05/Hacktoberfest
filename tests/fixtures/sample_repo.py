"""Temporary git repos seeded from a spider-python snapshot.

Every scenario gets its own repo under pytest's tmp_path; the real spider-python
repository is never touched. Identities and dates are fixed, so commit SHAs are
the same on every machine.
"""
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from . import sample_commits as sc

SNAPSHOT = Path(__file__).parent / "spider_snapshot" / "config.py"
SPIDER_COMMIT = "1aeb25169761d428848debb5ef3883160eba6a3f"

DEV = ("Alice Dev", "alice@example.com")
QA = ("Quinn QA", "quinn@example.com")
OPS = ("Omar Ops", "omar@example.com")


class SpiderRepo:
    def __init__(self, path):
        self.path = Path(path)
        self.clock = 0
        self.path.mkdir(parents=True)
        self.git("init", "-q", "-b", "dev")

    def env(self, author=DEV):
        self.clock += 60
        ts = f"{1790000000 + self.clock} +0530"
        return {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_AUTHOR_NAME": author[0], "GIT_AUTHOR_EMAIL": author[1],
                "GIT_COMMITTER_NAME": author[0], "GIT_COMMITTER_EMAIL": author[1],
                "GIT_AUTHOR_DATE": ts, "GIT_COMMITTER_DATE": ts, "GIT_EDITOR": "true",
                "PYTHONDONTWRITEBYTECODE": "1"}

    def git(self, *args, author=DEV, check=True, stdin=None):
        r = subprocess.run(["git", *args], cwd=self.path, env=self.env(author),
                           capture_output=True, text=True, input=stdin)
        if check and r.returncode:
            raise RuntimeError(f"git {' '.join(args)} failed:\n{r.stdout}{r.stderr}")
        return r.stdout.strip() if check else r

    # --- files and commits ----------------------------------------------------
    def write(self, rel, content):
        f = self.path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)

    def change(self, key, new):
        """Change one zoho setting in config.py, e.g. change(sc.WAIT, 6000)."""
        cfg = self.path / "config.py"
        text = cfg.read_text()
        current = next(line.strip() for line in text.splitlines() if line.strip().startswith(f'"{key}":'))
        cfg.write_text(text.replace(current, sc.setting(key, new), 1))

    def commit(self, msg, author=DEV):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg, author=author)
        return self.head()

    def head(self):
        return self.git("rev-parse", "HEAD")

    def checkout(self, branch):
        self.git("checkout", "-q", branch)

    def seed(self):
        """Base commit: the spider-python config snapshot + its app tests."""
        self.write("config.py", SNAPSHOT.read_text())
        self.write("tests/test_config.py", sc.APP_TESTS)
        self.write("pytest.ini", sc.PYTEST_INI)
        base = self.commit(f"Import spider-python config (snapshot of {SPIDER_COMMIT[:8]})")
        self.git("branch", "uat")
        self.git("branch", "prod")
        return base

    # --- the "testing module": run app tests, attach results to a commit -------
    def run_app_tests(self):
        xml = self.path.parent / f"junit-{self.clock}.xml"
        subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        f"--junitxml={xml}", "tests"], cwd=self.path, capture_output=True,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        suites = ET.parse(xml).getroot().iter("testsuite")
        total = failed = skipped = 0
        for s in suites:
            total += int(s.get("tests", 0))
            failed += int(s.get("failures", 0)) + int(s.get("errors", 0))
            skipped += int(s.get("skipped", 0))
        xml.unlink()
        return {"passed": total - failed - skipped, "failed": failed}

    def _note(self, sha, record):
        at = datetime.fromtimestamp(1790000000 + self.clock, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        record = {"commit": sha, **record, "at": at}
        self.git("notes", "--ref=tests", "append", "-m", json.dumps(record), sha)
        return record

    def record_tests(self, env):
        """Run the app tests on the checked-out commit and store the result in git notes."""
        counts = self.run_app_tests()
        result = "pass" if counts["failed"] == 0 else "fail"
        return self._note(self.head(), {"env": env, "kind": "test", "result": result,
                                        "source": "ci", **counts})

    def record_deploy(self, sha, env="production", result="success"):
        return self._note(sha, {"env": env, "kind": "deploy", "result": result, "source": "deploy-log"})

    # --- inspection -----------------------------------------------------------
    def notes(self, sha):
        r = self.git("notes", "--ref=tests", "show", sha, check=False)
        return [json.loads(line) for line in r.stdout.splitlines() if line.strip()] if r.returncode == 0 else []

    def patch_id(self, sha):
        out = self.git("patch-id", "--stable", stdin=self.git("show", sha) + "\n")
        return out.split()[0] if out else None

    def unmerged(self):
        return sorted(self.git("diff", "--name-only", "--diff-filter=U").splitlines())


def clone_without_notes(src, dst):
    """A plain `git clone`: branches come along, refs/notes/* do not."""
    subprocess.run(["git", "clone", "-q", "--no-local", str(src.path), str(dst)], check=True,
                   capture_output=True, env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull})
    repo = SpiderRepo.__new__(SpiderRepo)
    repo.path, repo.clock = Path(dst), src.clock
    for branch in ("dev", "uat", "prod"):
        repo.git("branch", "-f", branch, f"origin/{branch}", check=False)
    return repo
