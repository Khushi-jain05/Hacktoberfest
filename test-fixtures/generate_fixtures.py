#!/usr/bin/env python3
"""Generate fixture git repos for testing the conflict-provenance tool (Problem 01).

Each scenario is written to repos/<scenario>/ and contains:
  repo/               a real git repo with dev / uat / prod style branches
  test_records.json   simulated CI + deploy log (which commit was tested/deployed where)
  expected.json       ground truth: what a correct tool must report

Test records are also mirrored into git notes under refs/notes/tests, so a tool
can read provenance either from the sidecar file or from git itself.

Commit dates and identities are fixed, so SHAs are identical on every machine.
Every scenario self-checks its claims (conflict happens, patch-ids match, ...)
and the script exits non-zero if any fixture does not behave as documented.

Usage: python3 generate_fixtures.py [scenario-name ...]
"""
import json
import os
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "repos"
P = "app/payment.py"
L = "app/legacy.py"

BASE_PAYMENT = '''def calculate_fee(amount):
    rate = 0.02
    return round(amount * rate, 2)


def validate(amount):
    if amount <= 0:
        raise ValueError("amount must be positive")
    return True


def refund(amount, fee):
    return amount - fee
'''

BASE_LEGACY = '''def old_fee(amount):
    return amount * 0.03
'''

ALICE = ("Alice Dev", "alice@example.com")
OPS = ("Omar Ops", "omar@example.com")
QA = ("Quinn QA", "quinn@example.com")
BOB = ("Bob Backport", "bob@example.com")

SCENARIOS = {}


def scenario(fn):
    SCENARIOS[fn.__name__] = fn
    return fn


class Repo:
    def __init__(self, name):
        self.name = name
        self.dir = ROOT / name
        self.path = self.dir / "repo"
        self.records = []
        self.clock = 0
        shutil.rmtree(self.dir, ignore_errors=True)
        self.path.mkdir(parents=True)
        self.git("init", "-q", "-b", "dev")

    @classmethod
    def at(cls, parent, path):
        """Handle on another working copy (a clone) that shares the parent's records."""
        r = cls.__new__(cls)
        r.name, r.dir, r.path = parent.name, parent.dir, Path(path)
        r.records, r.clock = parent.records, parent.clock
        return r

    def _env(self, author):
        self.clock += 60
        ts = f"{1790000000 + self.clock} +0530"
        return {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": author[0],
            "GIT_AUTHOR_EMAIL": author[1],
            "GIT_COMMITTER_NAME": author[0],
            "GIT_COMMITTER_EMAIL": author[1],
            "GIT_AUTHOR_DATE": ts,
            "GIT_COMMITTER_DATE": ts,
            "GIT_EDITOR": "true",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def git(self, *args, check=True, author=ALICE, stdin=None):
        r = subprocess.run(["git", "-C", str(self.path), *args], env=self._env(author),
                           capture_output=True, text=True, input=stdin)
        if check and r.returncode:
            raise RuntimeError(f"[{self.name}] git {' '.join(args)}\n{r.stdout}{r.stderr}")
        return r if not check else r.stdout.strip()

    def sh(self, cmd, author=BOB):
        """Run a shell command (as written in a resolve spec); failures are expected."""
        return subprocess.run(cmd, shell=True, cwd=self.path, env=self._env(author),
                              capture_output=True, text=True)

    # --- working tree helpers -------------------------------------------
    def write(self, rel, content):
        f = self.path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)

    def read(self, rel):
        return (self.path / rel).read_text()

    def edit(self, rel, old, new):
        text = self.read(rel)
        assert old in text, f"[{self.name}] {old!r} not in {rel}"
        self.write(rel, text.replace(old, new, 1))

    def commit(self, msg, author=ALICE):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg, author=author)
        return self.head()

    def head(self):
        return self.git("rev-parse", "HEAD")

    def checkout(self, branch):
        self.git("checkout", "-q", branch)

    def base(self, legacy=False):
        self.write(P, BASE_PAYMENT)
        if legacy:
            self.write(L, BASE_LEGACY)
        sha = self.commit("Initial payment module")
        self.git("branch", "uat")
        self.git("branch", "prod")
        return sha

    # --- git operations ---------------------------------------------------
    def pick(self, sha, author=BOB):
        """cherry-pick -x; returns new SHA, or None if it conflicted (state left in place)."""
        r = self.git("cherry-pick", "-x", sha, check=False, author=author)
        return self.head() if r.returncode == 0 else None

    def merge(self, branch, author=BOB):
        r = self.git("merge", "--no-ff", "--no-edit", branch, check=False, author=author)
        return self.head() if r.returncode == 0 else None

    def unmerged(self):
        out = self.git("diff", "--name-only", "-z", "--diff-filter=U")
        return sorted(unicodedata.normalize("NFC", f) for f in out.split("\0") if f)

    def hunks(self, rel, marker="<<<<<<<"):
        return sum(line.startswith(marker + " ") for line in self.read(rel).splitlines())

    def abort(self):
        for op in ("cherry-pick", "merge", "rebase", "revert", "am"):
            self.git(op, "--abort", check=False)
        if self.unmerged():  # squash merges have nothing to --abort
            self.git("reset", "-q", "--hard")
        (self.path / ".git" / "SQUASH_MSG").unlink(missing_ok=True)
        assert not self.unmerged(), f"[{self.name}] could not abort"

    def patch_id(self, rev, base=None):
        diff = self.git("diff", base, rev) if base else self.git("show", rev)
        out = self.git("patch-id", "--stable", stdin=diff + "\n")
        return out.split()[0] if out else None

    # --- provenance -------------------------------------------------------
    def _record(self, sha, env, kind, result, at=None):
        at = at or datetime.fromtimestamp(1790000000 + self.clock, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rec = {"commit": sha, "env": env, "kind": kind, "result": result,
               "source": "ci" if kind == "test" else "deploy-log", "at": at}
        self.records.append(rec)
        self.git("notes", "--ref=tests", "append", "-m", json.dumps(rec), sha)

    def test(self, sha, env, result="pass", at=None):
        self._record(sha, env, "test", result, at)

    def deploy(self, sha, env="production", result="success", at=None):
        self._record(sha, env, "deploy", result, at)

    def edit_bytes(self, rel, old, new):
        f = self.path / rel
        data = f.read_bytes()
        assert old in data, f"[{self.name}] {old!r} not in {rel}"
        f.write_bytes(data.replace(old, new, 1))

    def finish(self, description, reproduce, commits, expected, checkout):
        self.checkout(checkout)
        (self.dir / "test_records.json").write_text(json.dumps(
            {"records": self.records}, indent=2) + "\n")
        (self.dir / "expected.json").write_text(json.dumps({
            "scenario": self.name,
            "description": description,
            "reproduce": reproduce,
            "commits": commits,
            "expected": expected,
        }, indent=2) + "\n")


def clone(src, dst, *opts):
    subprocess.run(["git", "clone", "-q", *opts, str(src), str(dst)], check=True,
                   capture_output=True, env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull})


def expect_conflict(r, files, hunks=None):
    got = r.unmerged()
    assert got == sorted(files), f"[{r.name}] expected conflict in {files}, got {got}"
    if hunks is not None:
        n = r.hunks(files[0])
        assert n == hunks, f"[{r.name}] expected {hunks} hunks, got {n}"


# ===========================================================================
# Scenarios
# ===========================================================================

@scenario
def s01_clean_backport():
    r = Repo("s01_clean_backport")
    base = r.base()
    r.edit(P, "if amount <= 0:", "if amount <= 0 or amount > 1_000_000:")
    fix = r.commit("Reject amounts above 1M")
    r.test(fix, "dev")
    r.checkout("uat")
    u = r.pick(fix)
    r.test(u, "uat")
    r.checkout("prod")
    p = r.pick(fix, author=OPS)
    r.deploy(p)
    assert r.patch_id(fix) == r.patch_id(u) == r.patch_id(p)
    r.finish(
        "One fix promoted dev -> uat -> prod with cherry-pick -x, no conflicts. Baseline.",
        ["git log --all --oneline", "git cherry -v prod dev"],
        {"base": base, "dev_fix": fix, "uat_copy": u, "prod_copy": p},
        {
            "conflict": False,
            "equivalent_commits": [fix, u, p],
            "match_evidence": ["identical patch-id", "cherry-pick -x trailer"],
            "status_of_change": "deployed to production (via prod copy); tested in uat and dev",
        },
        checkout="prod",
    )


def _hotfix_vs_devfix(r, record_tests=True):
    base = r.base()
    r.checkout("prod")
    r.edit(P, "rate = 0.02", "rate = 0.025")
    hotfix = r.commit("HOTFIX: raise fee to 2.5%", author=OPS)
    if record_tests:
        r.test(hotfix, "production")
        r.deploy(hotfix)
    r.checkout("dev")
    r.edit(P, "rate = 0.02", "rate = 0.02 if amount < 1000 else 0.015")
    devfix = r.commit("Tiered fee for large amounts")
    if record_tests:
        r.test(devfix, "dev")
    r.checkout("prod")
    assert r.pick(devfix) is None
    expect_conflict(r, [P], hunks=1)
    r.abort()
    return base, hotfix, devfix


@scenario
def s02_prod_hotfix_vs_dev_fix():
    r = Repo("s02_prod_hotfix_vs_dev_fix")
    base, hotfix, devfix = _hotfix_vs_devfix(r)
    r.finish(
        "Core case. prod got a hotfix on the same line a dev fix changes. "
        "Backporting the dev fix to prod conflicts.",
        ["git checkout prod", f"git cherry-pick -x {devfix}"],
        {"base": base, "prod_hotfix": hotfix, "dev_fix": devfix},
        {
            "conflict": True, "files": [P], "hunks": 1,
            "ours": {"branch": "prod", "origin_commit": hotfix,
                     "status": "production-tested", "evidence": ["test:production:pass", "deploy:production"]},
            "theirs": {"branch": "dev", "origin_commit": devfix,
                       "status": "tested-in-dev-only", "evidence": ["test:dev:pass"]},
            "must_not": ["call the dev side production-tested", "call the dev side uat-tested"],
            "final_commit": "ordinary git commit; git fsck clean; no custom objects required to read it",
        },
        checkout="prod",
    )


@scenario
def s03_modified_backport_with_trailer():
    r = Repo("s03_modified_backport_with_trailer")
    base = r.base()
    r.checkout("uat")
    r.edit(P, '"amount must be positive"', '"amount must be > 0"')
    uat_msg = r.commit("Shorter validation message", author=QA)
    r.test(uat_msg, "uat")
    r.checkout("dev")
    r.edit(P, "if amount <= 0:", "if amount is None or amount <= 0:")
    devfix = r.commit("Reject None amounts")
    r.test(devfix, "dev")
    r.checkout("uat")
    assert r.pick(devfix) is None
    expect_conflict(r, [P])
    r.abort()
    # Hand-resolved backport: code differs from the original, trailer kept.
    r.edit(P, "if amount <= 0:", "if amount is None or amount <= 0:")
    back = r.commit(f"Reject None amounts\n\n(cherry picked from commit {devfix})", author=BOB)
    r.test(back, "uat")
    assert r.patch_id(back) != r.patch_id(devfix)
    r.finish(
        "Backport had to be edited during conflict resolution, so patch-id no longer "
        "matches. The -x trailer is the only exact link.",
        ["git log uat -1 --format=%B", f"git patch-id --stable < <(git show {back})",
         f"git patch-id --stable < <(git show {devfix})"],
        {"base": base, "uat_message_change": uat_msg, "dev_fix": devfix, "uat_backport": back},
        {
            "match": {"backport": back, "original": devfix, "method": "cherry-pick trailer",
                      "patch_id_equal": False, "modified_during_backport": True},
            "testing": "dev test of original does NOT cover the backport; backport has its own uat pass",
            "must_not": ["report the two as unrelated", "copy the dev test result onto the backport"],
        },
        checkout="uat",
    )


@scenario
def s04_backport_no_trailer_reworded():
    r = Repo("s04_backport_no_trailer_reworded")
    base = r.base()
    r.checkout("dev")
    r.edit(P, "    return round(amount * rate, 2)", "    fee = amount * rate\n    return round(fee, 2)")
    devfix = r.commit("Split fee computation for readability")
    r.test(devfix, "dev")
    r.checkout("prod")
    r.edit(P, "    return round(amount * rate, 2)", "    f = amount * rate\n    return round(f, 2)")
    back = r.commit("Port readability tweak from main", author=BOB)
    r.deploy(back)
    assert r.patch_id(back) != r.patch_id(devfix)
    r.finish(
        "Same change re-typed by hand on prod: different author, message, variable name. "
        "No trailer, patch-id differs. Only fuzzy matching can link them.",
        ["git log --all --oneline"],
        {"base": base, "dev_change": devfix, "prod_retyped": back},
        {
            "match": {"backport": back, "original": devfix, "method": "similarity",
                      "confidence": "probable (must NOT be reported as certain)"},
            "must_not": ["claim an exact/verified match", "silently ignore the relationship"],
        },
        checkout="prod",
    )


@scenario
def s05_whitespace_only_difference():
    r = Repo("s05_whitespace_only_difference")
    base = r.base()
    r.edit(P, "    return amount - fee", "    return max(amount - fee, 0)")
    devfix = r.commit("Never refund a negative amount")
    r.test(devfix, "uat")
    r.checkout("prod")
    r.edit(P, "    return amount - fee", "    return max(amount-fee,0)")
    back = r.commit("Clamp refunds", author=OPS)
    r.deploy(back)
    assert r.patch_id(back) == r.patch_id(devfix)
    r.finish(
        "Backport differs only in whitespace and has no trailer. git patch-id ignores "
        "whitespace, so the match is exact.",
        [f"git show {devfix} | git patch-id --stable", f"git show {back} | git patch-id --stable"],
        {"base": base, "dev_fix": devfix, "prod_copy": back},
        {"match": {"backport": back, "original": devfix, "method": "patch-id", "confidence": "exact"}},
        checkout="prod",
    )


@scenario
def s06_clone_without_metadata():
    r = Repo("s06_clone_without_metadata")
    base, hotfix, devfix = _hotfix_vs_devfix(r)
    r.finish(
        "Same history as s02, but use clone/ — a plain git clone of repo/. Clones do not "
        "fetch refs/notes/*, and the sidecar test_records.json belongs to repo/ only.",
        ["cd clone", "git notes --ref=tests list   # empty",
         "git checkout prod", f"git cherry-pick -x {devfix}",
         "# then: git fetch origin 'refs/notes/*:refs/notes/*' and re-run"],
        {"base": base, "prod_hotfix": hotfix, "dev_fix": devfix},
        {
            "in_clone_before_fetching_notes": {
                "ours": "provenance UNKNOWN (not 'untested')",
                "theirs": "provenance UNKNOWN (not 'untested')",
                "should_suggest": "fetching refs/notes/* from origin",
            },
            "in_clone_after_fetching_notes": "same answer as s02",
            "must_not": ["crash", "report unknown as untested/failed"],
        },
        checkout="prod",
    )
    clone(r.path, r.dir / "clone")
    notes = subprocess.run(["git", "-C", str(r.dir / "clone"), "notes", "--ref=tests", "list"],
                           capture_output=True, text=True).stdout
    assert notes == "", "clone unexpectedly has notes"


@scenario
def s07_both_sides_untested():
    r = Repo("s07_both_sides_untested")
    base, hotfix, devfix = _hotfix_vs_devfix(r, record_tests=False)
    # Provenance tracking exists in this repo (unlike s06's clone), just not for these commits.
    r.test(base, "dev")
    r.finish(
        "Same conflict as s02, but neither side was ever tested or deployed. Records exist "
        "for other commits, so the answer is 'unverified', not 'unknown'.",
        ["git checkout prod", f"git cherry-pick -x {devfix}"],
        {"base": base, "prod_change": hotfix, "dev_fix": devfix},
        {
            "conflict": True,
            "ours": {"origin_commit": hotfix, "status": "unverified"},
            "theirs": {"origin_commit": devfix, "status": "unverified"},
            "must_not": ["treat 'is on prod branch' as 'production-tested'"],
        },
        checkout="prod",
    )


@scenario
def s08_rename_then_conflict():
    r = Repo("s08_rename_then_conflict")
    base = r.base()
    r.git("mv", P, "app/billing.py")
    ren = r.commit("Rename payment module to billing")
    r.edit("app/billing.py", "rate = 0.02", "rate = 0.018")
    devfix = r.commit("Lower base fee")
    r.test(devfix, "dev")
    r.checkout("prod")
    r.edit(P, "rate = 0.02", "rate = 0.025")
    hotfix = r.commit("HOTFIX: raise fee to 2.5%", author=OPS)
    r.deploy(hotfix)
    r.checkout("dev")
    assert r.pick(hotfix) is None
    expect_conflict(r, ["app/billing.py"], hunks=1)
    r.abort()
    r.finish(
        "File renamed on dev; prod hotfix made in the old path. Forward-porting the hotfix "
        "conflicts in the NEW path.",
        ["git checkout dev", f"git cherry-pick -x {hotfix}"],
        {"base": base, "rename": ren, "dev_fix": devfix, "prod_hotfix": hotfix},
        {
            "conflict": True, "files": ["app/billing.py"],
            "ours": {"origin_commit": devfix, "status": "tested-in-dev-only"},
            "theirs": {"origin_commit": hotfix, "origin_path": P, "status": "production-deployed"},
            "must": ["follow the rename when looking up history/blame"],
        },
        checkout="dev",
    )


@scenario
def s09_modify_delete():
    r = Repo("s09_modify_delete")
    base = r.base(legacy=True)
    r.git("rm", "-q", L)
    delete = r.commit("Remove legacy fee module")
    r.test(delete, "dev")
    r.checkout("prod")
    r.edit(L, "0.03", "0.035")
    hotfix = r.commit("HOTFIX: legacy fee for old contracts", author=OPS)
    r.test(hotfix, "production")
    r.deploy(hotfix)
    r.checkout("dev")
    assert r.merge("prod") is None
    expect_conflict(r, [L])
    r.abort()
    r.finish(
        "dev deleted a file that prod hot-fixed. Merging prod back into dev gives a "
        "modify/delete conflict (no conflict markers in any file).",
        ["git checkout dev", "git merge prod"],
        {"base": base, "dev_delete": delete, "prod_hotfix": hotfix},
        {
            "conflict": True, "kind": "modify/delete", "files": [L],
            "ours": {"origin_commit": delete, "action": "deleted", "status": "tested-in-dev-only"},
            "theirs": {"origin_commit": hotfix, "action": "modified", "status": "production-tested"},
            "should_warn": "keeping the delete drops a deployed hotfix",
            "must_not": ["crash because there are no <<<<<<< markers"],
        },
        checkout="dev",
    )


def _mixed_hunks(r):
    base = r.base()
    r.checkout("prod")
    r.edit(P, "rate = 0.02", "rate = 0.025")
    deployed = r.commit("HOTFIX: raise fee to 2.5%", author=OPS)
    r.test(deployed, "production")
    r.deploy(deployed)
    r.edit(P, "    return amount - fee", "    return amount - fee - 1")
    undeployed = r.commit("Charge 1 unit refund processing fee", author=OPS)
    r.checkout("dev")
    r.edit(P, "rate = 0.02", "rate = 0.018")
    r.edit(P, "    return amount - fee", "    return max(amount - fee, 0)")
    devfix = r.commit("Lower fee and clamp refunds")
    r.test(devfix, "uat")
    r.checkout("prod")
    assert r.pick(devfix) is None
    expect_conflict(r, [P], hunks=2)
    return base, deployed, undeployed, devfix


@scenario
def s10_multi_hunk_mixed_provenance():
    r = Repo("s10_multi_hunk_mixed_provenance")
    base, deployed, undeployed, devfix = _mixed_hunks(r)
    r.abort()
    r.finish(
        "Two conflict hunks in one file. On the prod side, hunk 1 comes from a deployed "
        "hotfix but hunk 2 comes from a commit that sits on prod and was NEVER deployed.",
        ["git checkout prod", f"git cherry-pick -x {devfix}"],
        {"base": base, "prod_deployed": deployed, "prod_undeployed": undeployed, "dev_fix": devfix},
        {
            "conflict": True, "files": [P], "hunks": 2,
            "hunk_1_calculate_fee": {"ours_origin": deployed, "ours_status": "production-tested",
                                     "theirs_origin": devfix, "theirs_status": "uat-tested"},
            "hunk_2_refund": {"ours_origin": undeployed, "ours_status": "unverified",
                              "theirs_origin": devfix, "theirs_status": "uat-tested"},
            "must": ["give provenance per hunk (line-level blame), not per branch"],
        },
        checkout="prod",
    )


@scenario
def s11_failed_test_reverted_then_reapplied():
    r = Repo("s11_failed_test_reverted_then_reapplied")
    base = r.base()
    r.checkout("uat")
    r.edit(P, "    return amount - fee", "    return abs(amount - fee)")
    v1 = r.commit("Refund fix v1", author=QA)
    r.test(v1, "uat", result="fail")
    r.git("revert", "--no-edit", v1, author=QA)
    revert = r.head()
    r.edit(P, "    return amount - fee", "    return max(amount - fee, 0)")
    v2 = r.commit("Refund fix v2", author=QA)
    r.test(v2, "uat")
    r.checkout("prod")
    r.edit(P, "    return amount - fee", "    return amount - fee if amount > fee else 0")
    hotfix = r.commit("HOTFIX: no negative refunds", author=OPS)
    r.deploy(hotfix)
    assert r.pick(v2) is None
    expect_conflict(r, [P], hunks=1)
    r.abort()
    r.finish(
        "uat tried a fix (failed test), reverted it, then landed v2 (passed). "
        "Backporting v2 to prod conflicts with a prod hotfix.",
        ["git checkout prod", f"git cherry-pick -x {v2}"],
        {"base": base, "uat_v1_failed": v1, "uat_revert": revert, "uat_v2": v2, "prod_hotfix": hotfix},
        {
            "conflict": True,
            "theirs": {"origin_commit": v2, "status": "uat-tested (pass)"},
            "ours": {"origin_commit": hotfix, "status": "production-deployed, no test record"},
            "should_surface": f"v1 ({v1}) failed in uat and was reverted",
            "must_not": ["attribute v1's failing test to v2", "attribute v2's pass to v1"],
        },
        checkout="prod",
    )


@scenario
def s12_squashed_backport():
    r = Repo("s12_squashed_backport")
    base = r.base()
    r.edit(P, "if amount <= 0:", "if amount <= 0 or amount > 1_000_000:")
    a = r.commit("Reject amounts above 1M")
    r.edit(P, "rate = 0.02", "rate = 0.018")
    b = r.commit("Lower fee")
    r.edit(P, "    return amount - fee", "    return max(amount - fee, 0)")
    c = r.commit("Clamp refunds")
    r.test(c, "uat")
    r.checkout("prod")
    r.git("checkout", c, "--", P)
    squash = r.commit("Backport payment fixes\n\n"
                      f"Backport of {a} from dev.\nBackport of {b} from dev.\nBackport of {c} from dev.",
                      author=BOB)
    r.deploy(squash)
    assert r.patch_id(squash) == r.patch_id(c, base=base)
    assert r.patch_id(squash) not in {r.patch_id(x) for x in (a, b, c)}
    r.finish(
        "Three dev commits squashed into one prod commit (Django-style 'Backport of' lines).",
        ["git log prod -1 --format=%B", f"git diff {base} {c} | git patch-id --stable"],
        {"base": base, "dev_a": a, "dev_b": b, "dev_c": c, "prod_squash": squash},
        {
            "match": {"backport": squash, "originals": [a, b, c],
                      "method": "message references + range patch-id equals squash patch-id"},
            "testing": "uat test was on the tip (dev_c) only; a and b alone were never tested",
        },
        checkout="prod",
    )


@scenario
def s13_test_was_on_older_commit():
    r = Repo("s13_test_was_on_older_commit")
    base = r.base()
    r.checkout("uat")
    r.edit(P, "rate = 0.02", "rate = 0.021")
    x = r.commit("Fee 2.1%", author=QA)
    r.test(x, "uat")
    r.edit(P, "rate = 0.021", "rate = 0.022")
    y = r.commit("Fee 2.2% (late change, no CI run)", author=QA)
    r.checkout("prod")
    r.edit(P, "rate = 0.02", "rate = 0.025")
    hotfix = r.commit("HOTFIX: raise fee to 2.5%", author=OPS)
    r.deploy(hotfix)
    assert r.merge("uat") is None
    expect_conflict(r, [P], hunks=1)
    r.abort()
    r.finish(
        "uat has a passing test, but on an OLDER commit. The line in conflict was last "
        "changed by a later, untested commit.",
        ["git checkout prod", "git merge uat"],
        {"base": base, "uat_tested": x, "uat_untested_tip": y, "prod_hotfix": hotfix},
        {
            "conflict": True,
            "theirs": {"origin_commit": y, "status": "unverified",
                       "note": f"older commit {x} was tested, but its version of this line was replaced"},
            "ours": {"origin_commit": hotfix, "status": "production-deployed"},
            "must_not": ["mark the uat side as tested because the uat branch has a passing run"],
        },
        checkout="prod",
    )


@scenario
def s14_criss_cross_merge():
    r = Repo("s14_criss_cross_merge")
    base = r.base()
    r.edit(P, '"amount must be positive"', '"amount must be positive (dev)"')
    d1 = r.commit("dev: message tweak")
    r.checkout("uat")
    r.edit(P, "    return amount - fee", "    return round(amount - fee, 2)")
    u1 = r.commit("uat: round refunds", author=QA)
    r.checkout("dev")
    assert r.merge(u1)
    r.checkout("uat")
    assert r.merge(d1, author=QA)
    r.checkout("dev")
    r.edit(P, "rate = 0.02", "rate = 0.03")
    d2 = r.commit("dev: fee 3%")
    r.test(d2, "dev")
    r.checkout("uat")
    r.edit(P, "rate = 0.02", "rate = 0.04")
    u2 = r.commit("uat: fee 4%", author=QA)
    r.test(u2, "uat")
    bases = r.git("merge-base", "--all", "dev", "uat").splitlines()
    assert len(bases) == 2, bases
    assert r.merge("dev") is None
    expect_conflict(r, [P], hunks=1)
    r.abort()
    r.finish(
        "Branches merged into each other (criss-cross), so there are TWO merge bases.",
        ["git merge-base --all dev uat", "git checkout uat", "git merge dev"],
        {"base": base, "dev_1": d1, "uat_1": u1, "dev_fee": d2, "uat_fee": u2, "merge_bases": bases},
        {
            "conflict": True,
            "ours": {"origin_commit": u2, "status": "uat-tested"},
            "theirs": {"origin_commit": d2, "status": "tested-in-dev-only"},
            "must_not": ["assume a single merge base", "crash"],
        },
        checkout="uat",
    )


@scenario
def s15_recurring_conflict_rerere():
    r = Repo("s15_recurring_conflict_rerere")
    r.git("config", "rerere.enabled", "true")
    base = r.base()
    r.git("branch", "release", base)
    r.checkout("uat")
    r.edit(P, "rate = 0.02", "rate = 0.022")
    uat_change = r.commit("uat: fee 2.2%", author=QA)
    r.checkout("release")
    rel_change = r.pick(uat_change, author=QA)
    r.checkout("dev")
    r.edit(P, "rate = 0.02", "rate = 0.018")
    devfix = r.commit("dev: fee 1.8%")
    r.checkout("uat")
    assert r.merge("dev") is None
    expect_conflict(r, [P])
    r.write(P, BASE_PAYMENT.replace("rate = 0.02", "rate = 0.019"))
    r.git("add", P)
    r.git("commit", "-q", "--no-edit", author=QA)
    resolution = r.head()
    r.test(resolution, "uat")
    r.checkout("release")
    assert r.merge("dev") is None
    assert "rate = 0.019" in r.read(P) and r.hunks(P) == 0, "rerere did not replay"
    r.abort()
    r.finish(
        "The same conflict was already resolved (and tested) on uat. Merging dev into "
        "release hits it again; rerere replays the old resolution.",
        ["git checkout release", "git merge dev", "git rerere status; git rerere diff"],
        {"base": base, "uat_change": uat_change, "release_copy": rel_change,
         "dev_fix": devfix, "uat_resolution_merge": resolution},
        {
            "conflict": True, "auto_resolved_by_rerere": True,
            "should_report": f"resolution was previously used in {resolution}, which passed uat",
            "note": "rr-cache lives in .git/ and is NOT cloned — a clone must fall back gracefully",
        },
        checkout="release",
    )


# ===========================================================================
# Advanced scenarios (s16-s40)
# ===========================================================================

@scenario
def s16_chained_backport():
    r = Repo("s16_chained_backport")
    base = r.base()
    r.edit(P, "if amount <= 0:", "if amount <= 0 or amount > 1_000_000:")
    a = r.commit("Reject amounts above 1M")
    r.test(a, "dev")
    r.checkout("uat")
    b = r.pick(a, author=QA)
    r.test(b, "uat")
    r.checkout("prod")
    c = r.pick(b, author=OPS)
    r.deploy(c)
    msg = r.git("log", "-1", "--format=%B", c)
    assert f"cherry picked from commit {a}" in msg and f"cherry picked from commit {b}" in msg
    assert r.patch_id(a) == r.patch_id(b) == r.patch_id(c)
    r.finish(
        "dev -> uat -> prod, where prod picked the UAT copy (not the dev original). "
        "The prod commit carries TWO cherry-pick trailers.",
        ["git log prod -1 --format=%B"],
        {"base": base, "dev_original": a, "uat_copy": b, "prod_copy": c},
        {
            "chain": [c, b, a],
            "evidence_for_prod_copy": ["test:dev:pass (via a)", "test:uat:pass (via b)", "deploy:production"],
            "must": ["follow trailers transitively", "parse multiple trailers in one message"],
        },
        checkout="prod",
    )


@scenario
def s17_chain_modified_midway():
    r = Repo("s17_chain_modified_midway")
    base = r.base()
    r.checkout("uat")
    r.edit(P, '"amount must be positive"', '"amount must be > 0"')
    u = r.commit("Shorter validation message", author=QA)
    r.checkout("prod")
    up = r.pick(u, author=OPS)
    r.checkout("dev")
    r.edit(P, "if amount <= 0:", "if amount is None or amount <= 0:")
    a = r.commit("Reject None amounts")
    r.test(a, "dev")
    r.checkout("uat")
    assert r.pick(a) is None
    r.abort()
    r.edit(P, "if amount <= 0:", "if amount is None or amount <= 0:")
    b = r.commit(f"Reject None amounts\n\n(cherry picked from commit {a})", author=BOB)
    r.test(b, "uat")
    r.checkout("prod")
    c = r.pick(b, author=OPS)
    assert c
    r.deploy(c)
    assert r.patch_id(c) == r.patch_id(b) != r.patch_id(a)
    r.finish(
        "Chain where the FIRST hop was hand-edited and the second hop was clean. "
        "prod copy is identical to the UAT version, not to the dev original.",
        ["git log prod -1 --format=%B"],
        {"base": base, "uat_msg_change": u, "prod_msg_copy": up,
         "dev_original": a, "uat_modified_copy": b, "prod_copy": c},
        {
            "links": [{"from": c, "to": b, "method": "patch-id + trailer", "exact": True},
                      {"from": b, "to": a, "method": "trailer", "exact": False}],
            "testing_that_covers_prod_code": ["test:uat:pass (b)"],
            "testing_that_does_NOT_cover_prod_code": ["test:dev:pass (a) — different code"],
        },
        checkout="prod",
    )


@scenario
def s18_resolution_introduces_new_code():
    r = Repo("s18_resolution_introduces_new_code")
    base, hotfix, devfix = _hotfix_vs_devfix(r)
    assert r.pick(devfix) is None
    r.write(P, BASE_PAYMENT.replace("rate = 0.02", "rate = 0.025 if amount < 1000 else 0.015"))
    r.git("add", P)
    r.git("cherry-pick", "--continue", author=BOB)
    resolved = r.head()
    assert f"cherry picked from commit {devfix}" in r.git("log", "-1", "--format=%B")
    assert r.git("rev-list", "--parents", "-n1", resolved).count(" ") == 1
    r.finish(
        "The s02 conflict was resolved by WRITING NEW CODE (0.025 from ours + tiers from theirs). "
        "Both inputs had test evidence; the result has none.",
        [f"git show {resolved}", f"# re-create the conflict: git checkout --detach {hotfix} && git cherry-pick -x {devfix}"],
        {"base": base, "prod_hotfix": hotfix, "dev_fix": devfix, "resolution": resolved},
        {
            "resolved_line_status": "UNVERIFIED — new code written during resolution",
            "inputs": {"ours": "production-tested", "theirs": "tested-in-dev-only"},
            "final_commit": {"parents": 1, "keeps_trailer": True, "git_fsck": "clean",
                             "provenance_trailers_if_added": "must parse with git interpret-trailers --parse"},
            "must_not": ["inherit 'production-tested' for the resolved line", "inherit theirs' dev test either"],
        },
        checkout="prod",
    )


@scenario
def s19_resolution_mixes_hunks():
    r = Repo("s19_resolution_mixes_hunks")
    base, deployed, undeployed, devfix = _mixed_hunks(r)
    r.write(P, BASE_PAYMENT.replace("rate = 0.02", "rate = 0.025")
            .replace("    return amount - fee", "    return max(amount - fee, 0)"))
    r.git("add", P)
    r.git("cherry-pick", "--continue", author=BOB)
    resolved = r.head()
    r.finish(
        "Two-hunk conflict (s10) resolved by taking OURS for hunk 1 and THEIRS for hunk 2.",
        [f"git show {resolved}", f"git blame {resolved} -- {P}"],
        {"base": base, "prod_deployed": deployed, "prod_undeployed": undeployed,
         "dev_fix": devfix, "resolution": resolved},
        {
            "line_provenance_after_resolution": {
                "rate = 0.025": {"from": deployed, "status": "production-tested"},
                "return max(amount - fee, 0)": {"from": devfix, "status": "uat-tested"},
            },
            "whole_commit_status": "UNVERIFIED — this combination was never tested together",
            "dropped": {"commit": undeployed, "note": "its refund change was discarded by the resolution"},
        },
        checkout="prod",
    )


@scenario
def s20_same_patch_different_base():
    r = Repo("s20_same_patch_different_base")
    base = r.base()
    r.write(P, "def normalize(x):\n    return round(x, 2)\n\n\n" + r.read(P))
    helper = r.commit("Add normalize helper")
    r.edit(P, "    return amount - fee", "    return normalize(amount - fee)")
    fix = r.commit("Normalize refunds")
    r.test(fix, "dev")
    r.checkout("prod")
    p = r.pick(fix, author=OPS)
    assert p and "def normalize" not in r.read(P)
    assert r.patch_id(p) == r.patch_id(fix)
    r.finish(
        "Clean cherry-pick with IDENTICAL patch-id, but the fix calls normalize(), which is "
        "defined in an earlier dev commit that was never backported. prod is now broken.",
        ["git checkout prod", "python3 -c \"import app.payment as m; m.refund(10, 1)\"   # NameError"],
        {"base": base, "dev_helper": helper, "dev_fix": fix, "prod_copy": p},
        {
            "match": {"backport": p, "original": fix, "method": "patch-id", "exact": True},
            "testing": "dev test of the fix ran on a base that HAD normalize(); it does not cover prod",
            "should_flag": f"missing prerequisite {helper} (defines normalize)",
            "must_not": ["transfer 'tested' just because patch-ids match"],
        },
        checkout="prod",
    )


@scenario
def s21_rebased_after_test():
    r = Repo("s21_rebased_after_test")
    base = r.base()
    r.git("checkout", "-q", "-b", "feature")
    r.edit(P, "    return amount - fee", "    return max(amount - fee, 0)")
    x = r.commit("Clamp refunds")
    r.test(x, "dev")
    r.checkout("dev")
    r.edit(P, '"amount must be positive"', '"amount must be > 0"')
    d = r.commit("Message tweak")
    r.checkout("feature")
    r.git("rebase", "-q", "dev")
    x2 = r.head()
    assert x2 != x and r.patch_id(x2) == r.patch_id(x)
    assert r.git("branch", "--contains", x) == ""
    r.finish(
        "A commit was tested, then the branch was rebased: the tested SHA is no longer on "
        "any branch, and the new SHA has no record of its own.",
        ["git log feature -1", f"git notes --ref=tests show {x}", f"git notes --ref=tests show {x2}   # none"],
        {"base": base, "tested_before_rebase": x, "dev_change": d, "after_rebase": x2},
        {
            "status_of_after_rebase": "tested BEFORE rebase (same patch-id as the tested commit); base changed",
            "must_not": ["say 'never tested'", "say 'tested' with no caveat"],
        },
        checkout="feature",
    )


@scenario
def s22_notes_diverged_between_clones():
    r = Repo("s22_notes_diverged_between_clones")
    base = r.base()
    r.edit(P, "rate = 0.02", "rate = 0.018")
    c = r.commit("Lower fee")
    r.test(c, "dev")
    origin = r.dir / "origin.git"
    clone(r.path, origin, "--mirror")
    qa, ops = Repo.at(r, r.dir / "clone_qa"), Repo.at(r, r.dir / "clone_ops")
    for w in (qa, ops):
        clone(origin, w.path)
        w.git("fetch", "-q", "origin", "refs/notes/*:refs/notes/*")
    qa.test(c, "uat")
    qa.git("push", "-q", "origin", "refs/notes/tests")
    ops.deploy(c)
    rejected = ops.git("fetch", "origin", "refs/notes/tests:refs/notes/tests", check=False)
    assert rejected.returncode != 0, "diverged notes fetch should be rejected"
    r.finish(
        "Two people added provenance to the same commit in different clones. QA pushed first; "
        "Ops' plain notes fetch is now rejected (non-fast-forward).",
        ["cd clone_ops", "git fetch origin refs/notes/tests:refs/notes/tests   # rejected",
         "git fetch origin refs/notes/tests:refs/notes/origin-tests",
         "git notes --ref=tests merge -s cat_sort_uniq refs/notes/origin-tests"],
        {"base": base, "commit": c},
        {
            "after_sync_in_clone_ops": {"commit": c, "records": ["test:dev:pass", "test:uat:pass", "deploy:production"]},
            "must_not": ["lose either side's records", "overwrite notes with --force"],
        },
        checkout="dev",
    )


@scenario
def s23_trailer_points_to_missing_commit():
    r = Repo("s23_trailer_points_to_missing_commit")
    base = r.base()
    r.git("checkout", "-q", "-b", "tmp-fix")
    r.edit(P, "rate = 0.02", "rate = 0.018")
    orig = r.commit("Lower fee")
    r.test(orig, "dev")
    r.checkout("prod")
    p = r.pick(orig, author=OPS)
    r.deploy(p)
    r.git("branch", "-D", "tmp-fix")
    r.git("reflog", "expire", "--expire=now", "--all")
    r.git("gc", "-q", "--prune=now")
    assert r.git("cat-file", "-e", orig, check=False).returncode != 0, "original still present"
    r.finish(
        "prod commit says '(cherry picked from commit X)' but X was force-pushed away and "
        "garbage-collected. A test note for X still exists.",
        ["git log prod -1 --format=%B", f"git cat-file -e {orig}   # fails",
         "git notes --ref=tests list"],
        {"base": base, "missing_original": orig, "prod_copy": p},
        {
            "link": {"to": orig, "status": "original NOT in repository (rewritten/deleted)"},
            "evidence": "a dev pass is recorded for the missing SHA, but its content can't be verified",
            "must_not": ["crash on the missing object", "silently drop the trailer"],
        },
        checkout="prod",
    )


@scenario
def s24_lying_trailer():
    r = Repo("s24_lying_trailer")
    base = r.base()
    r.edit(P, "if amount <= 0:", "if amount <= 0 or amount > 1_000_000:")
    a = r.commit("Reject amounts above 1M")
    r.test(a, "uat")
    r.checkout("prod")
    r.edit(P, "    return amount - fee", "    return amount")
    fake = r.commit(f"Reject amounts above 1M\n\n(cherry picked from commit {a})", author=BOB)
    r.finish(
        "Commit message claims to be a cherry-pick of a tested fix, but the code is unrelated "
        "(it removes the refund fee). Messages are claims, not evidence.",
        [f"git show {fake}", f"git show {a}"],
        {"base": base, "claimed_original": a, "prod_commit": fake},
        {
            "link": {"claimed": a, "verified": False, "reason": "no overlapping changes; patch-id differs; similarity ~0"},
            "status_of_prod_commit": "unverified",
            "must_not": ["inherit the uat test of the claimed original"],
        },
        checkout="prod",
    )


@scenario
def s25_deploy_rollback():
    r = Repo("s25_deploy_rollback")
    base = r.base()
    r.checkout("prod")
    r.edit(P, '"amount must be positive"', '"amount must be > 0"')
    p1 = r.commit("Shorter validation message", author=OPS)
    r.test(p1, "production")
    r.deploy(p1)
    r.edit(P, "rate = 0.02", "rate = 0.025")
    p2 = r.commit("Raise fee to 2.5%", author=OPS)
    r.deploy(p2)
    r.deploy(p1)  # rollback
    r.checkout("dev")
    r.edit(P, "rate = 0.02", "rate = 0.018")
    devfix = r.commit("Lower fee")
    r.test(devfix, "dev")
    r.checkout("prod")
    assert r.pick(devfix) is None
    expect_conflict(r, [P], hunks=1)
    r.abort()
    r.finish(
        "p2 was deployed, then production was rolled back to p1 (see 'at' order in records). "
        "The prod branch still points at p2.",
        ["git checkout prod", f"git cherry-pick -x {devfix}"],
        {"base": base, "p1": p1, "p2_rolled_back": p2, "dev_fix": devfix},
        {
            "currently_in_production": p1,
            "ours": {"origin_commit": p2, "status": "deployed once, ROLLED BACK — not running in production"},
            "theirs": {"origin_commit": devfix, "status": "tested-in-dev-only"},
            "must_not": ["say ours is running in production", "use 'tip of prod branch' as 'what is deployed'"],
        },
        checkout="prod",
    )


@scenario
def s26_flaky_test_history():
    r = Repo("s26_flaky_test_history")
    base, hotfix, devfix = _hotfix_vs_devfix(r, record_tests=False)
    r.deploy(hotfix)
    r.test(devfix, "uat")
    r.test(devfix, "uat", result="fail")
    r.test(devfix, "uat")
    r.finish(
        "The dev side was tested three times in uat: pass, FAIL, pass.",
        ["git checkout prod", f"git cherry-pick -x {devfix}"],
        {"base": base, "prod_hotfix": hotfix, "dev_fix": devfix},
        {
            "theirs": {"origin_commit": devfix, "status": "uat: inconsistent (2 pass / 1 fail) — possibly flaky"},
            "ours": {"origin_commit": hotfix, "status": "production-deployed"},
            "must_not": ["report only the latest result", "report only 'pass'"],
        },
        checkout="prod",
    )


@scenario
def s27_test_on_descendant():
    r = Repo("s27_test_on_descendant")
    base = r.base()
    r.checkout("uat")
    r.edit(P, "rate = 0.02", "rate = 0.021")
    c = r.commit("Fee 2.1%", author=QA)
    r.edit(P, "    return amount - fee", "    return max(amount - fee, 0)")
    d = r.commit("Clamp refunds", author=QA)
    r.test(d, "uat")
    r.checkout("prod")
    r.edit(P, "rate = 0.02", "rate = 0.025")
    hotfix = r.commit("HOTFIX: raise fee to 2.5%", author=OPS)
    r.deploy(hotfix)
    assert r.merge("uat") is None
    expect_conflict(r, [P], hunks=1)
    r.abort()
    r.finish(
        "CI tested only the uat tip. The conflicted line comes from an earlier commit, "
        "but it is unchanged in the tested tip. (Opposite of s13.)",
        ["git checkout prod", "git merge uat"],
        {"base": base, "uat_line_origin": c, "uat_tested_tip": d, "prod_hotfix": hotfix},
        {
            "theirs": {"origin_commit": c, "status": f"uat-tested via descendant {d} (line identical in tested tree)"},
            "must_not": ["call theirs untested just because the origin commit has no direct record"],
        },
        checkout="prod",
    )


@scenario
def s28_evil_merge():
    r = Repo("s28_evil_merge")
    base = r.base()
    r.git("checkout", "-q", "-b", "feature")
    r.edit(P, '"amount must be positive"', '"amount must be > 0"')
    f = r.commit("Message tweak")
    r.test(f, "dev")
    r.checkout("dev")
    r.git("merge", "--no-ff", "--no-commit", "feature")
    r.edit(P, "rate = 0.02", "rate = 0.03")
    r.git("add", "-A")
    r.git("commit", "-q", "--no-edit")
    m = r.head()
    for parent in (f"{m}^1", f"{m}^2"):
        assert "rate = 0.03" not in r.git("show", f"{parent}:{P}")
    r.checkout("prod")
    r.edit(P, "rate = 0.02", "rate = 0.025")
    hotfix = r.commit("HOTFIX: raise fee to 2.5%", author=OPS)
    r.deploy(hotfix)
    assert r.merge("dev") is None
    expect_conflict(r, [P], hunks=1)
    r.abort()
    r.finish(
        "The conflicting dev line was introduced INSIDE a merge commit (an 'evil merge'), "
        "not by any regular commit.",
        ["git checkout prod", "git merge dev", f"git show --cc {m}"],
        {"base": base, "feature_commit": f, "evil_merge": m, "prod_hotfix": hotfix},
        {
            "theirs": {"origin_commit": m, "kind": "merge commit (change not in either parent)", "status": "unverified"},
            "must_not": [f"attribute the line to {f}", "crash on a merge-commit origin"],
        },
        checkout="prod",
    )


@scenario
def s29_github_squash_merge():
    r = Repo("s29_github_squash_merge")
    base = r.base()
    r.git("checkout", "-q", "-b", "feature/fee-cap")
    r.edit(P, "if amount <= 0:", "if amount <= 0 or amount > 1_000_000:")
    f1 = r.commit("Add amount cap")
    r.edit(P, "    return round(amount * rate, 2)", "    return min(round(amount * rate, 2), 500)")
    f2 = r.commit("Cap fee at 500")
    r.test(f2, "dev")
    r.git("update-ref", "refs/pull/42/head", f2)
    r.checkout("dev")
    r.git("merge", "--squash", "-q", "feature/fee-cap")
    sq = r.commit("Fee cap (#42)")
    r.git("branch", "-D", "feature/fee-cap")
    assert r.patch_id(sq) == r.patch_id(f2, base=base)
    r.finish(
        "GitHub-style squash merge: PR #42 (2 commits, CI passed on its head) became ONE commit "
        "on dev with a new SHA and only '(#42)' in the subject. The branch is deleted; "
        "refs/pull/42/head remains, like on GitHub.",
        ["git log dev -1", "git log refs/pull/42/head"],
        {"base": base, "pr_commit_1": f1, "pr_head": f2, "squash": sq},
        {
            "match": {"squash": sq, "pr": 42, "originals": [f1, f2],
                      "method": "'(#42)' reference + squash patch-id == PR range patch-id"},
            "status_of_squash": "dev-tested (as PR head; identical change on identical base)",
        },
        checkout="dev",
    )


@scenario
def s30_partial_backport():
    r = Repo("s30_partial_backport")
    base = r.base(legacy=True)
    r.edit(P, "rate = 0.02", "rate = 0.018")
    r.edit(L, "0.03", "0.028")
    a = r.commit("Lower all fees")
    r.test(a, "uat")
    r.checkout("prod")
    r.git("checkout", a, "--", P)
    p = r.commit(f"Lower base fee\n\nPartial backport of {a} (app/payment.py only)", author=OPS)
    r.deploy(p)
    assert r.patch_id(p) != r.patch_id(a)
    r.finish(
        "Only one of the two files changed by the original was backported.",
        [f"git show --stat {a}", f"git show --stat {p}"],
        {"base": base, "original": a, "partial_backport": p},
        {
            "match": {"backport": p, "original": a, "kind": "partial (1 of 2 files)",
                      "per_file": {P: "identical", L: "not backported"}},
            "testing": "uat test covered the full change, not this subset",
        },
        checkout="prod",
    )


@scenario
def s31_conflict_mid_sequence():
    r = Repo("s31_conflict_mid_sequence")
    base = r.base()
    r.edit(P, "if amount <= 0:", "if amount <= 0 or amount > 1_000_000:")
    a = r.commit("Reject amounts above 1M")
    r.edit(P, "rate = 0.02", "rate = 0.018")
    b = r.commit("Lower fee")
    r.edit(P, "    return amount - fee", "    return max(amount - fee, 0)")
    c = r.commit("Clamp refunds")
    for x in (a, b, c):
        r.test(x, "dev")
    r.checkout("prod")
    r.edit(P, "rate = 0.02", "rate = 0.025")
    hotfix = r.commit("HOTFIX: raise fee to 2.5%", author=OPS)
    r.deploy(hotfix)
    res = r.git("cherry-pick", "-x", f"{a}^..{c}", check=False, author=BOB)
    assert res.returncode != 0
    expect_conflict(r, [P], hunks=1)
    assert (r.path / ".git" / "sequencer" / "todo").exists()
    assert r.git("rev-parse", "CHERRY_PICK_HEAD") == b
    r.abort()
    r.finish(
        "Backporting a RANGE of 3 commits: the first applies, the second conflicts, "
        "the third is still pending in .git/sequencer/todo.",
        ["git checkout prod", f"git cherry-pick -x {a}^..{c}"],
        {"base": base, "a": a, "b_conflicts": b, "c_pending": c, "prod_hotfix": hotfix},
        {
            "state": {"applied": [a], "in_conflict": b, "pending": [c]},
            "conflict": {"ours": {"origin_commit": hotfix, "status": "production-deployed"},
                         "theirs": {"origin_commit": b, "status": "tested-in-dev-only"}},
            "must": ["read CHERRY_PICK_HEAD, not assume the range tip", "survive 'git cherry-pick --continue' into the next commit"],
        },
        checkout="prod",
    )


@scenario
def s32_rebase_inverts_ours_theirs():
    r = Repo("s32_rebase_inverts_ours_theirs")
    base = r.base()
    r.git("checkout", "-q", "-b", "feature")
    r.edit(P, "rate = 0.02", "rate = 0.018")
    x = r.commit("Lower fee")
    r.test(x, "dev")
    r.checkout("dev")
    r.edit(P, "rate = 0.02", "rate = 0.025")
    y = r.commit("Raise fee", author=OPS)
    r.test(y, "uat")
    r.deploy(y)
    r.checkout("feature")
    assert r.git("rebase", "dev", check=False).returncode != 0
    expect_conflict(r, [P], hunks=1)
    assert r.head() == y
    r.abort()
    r.finish(
        "Rebasing 'feature' onto dev conflicts. During a rebase, HEAD/'ours' is the UPSTREAM (dev), "
        "and 'theirs' is YOUR commit being replayed — the reverse of a merge.",
        ["git checkout feature", "git rebase dev"],
        {"base": base, "feature_commit": x, "dev_commit": y},
        {
            "HEAD_side": {"origin_commit": y, "status": "uat-tested + production-deployed"},
            "incoming_side": {"origin_commit": x, "status": "tested-in-dev-only"},
            "must_not": ["label the feature commit as 'ours' because the user started on feature"],
        },
        checkout="feature",
    )


@scenario
def s33_revert_conflict():
    r = Repo("s33_revert_conflict")
    base = r.base()
    r.checkout("prod")
    r.edit(P, "rate = 0.02", "rate = 0.025")
    a = r.commit("Raise fee to 2.5%", author=OPS)
    r.deploy(a)
    r.edit(P, "rate = 0.025", "rate = 0.03")
    b = r.commit("Raise fee to 3%", author=OPS)
    r.test(b, "production")
    r.deploy(b)
    assert r.git("revert", "--no-edit", a, check=False, author=OPS).returncode != 0
    expect_conflict(r, [P], hunks=1)
    r.abort()
    r.finish(
        "Reverting an old commit whose line was changed again later.",
        ["git checkout prod", f"git revert {a}"],
        {"base": base, "reverted": a, "later_change": b},
        {
            "ours": {"origin_commit": b, "status": "production-tested, currently deployed"},
            "theirs": {"meaning": f"undo of {a}", "restores_text_from": base},
            "should_warn": "reverting would undo part of what is running in production",
        },
        checkout="prod",
    )


@scenario
def s34_diff3_and_custom_marker_size():
    r = Repo("s34_diff3_and_custom_marker_size")
    r.write(".gitattributes", "*.md conflict-marker-size=12\n")
    r.write("docs/fees.md", "Fee is 2%.\n")
    r.git("config", "merge.conflictStyle", "diff3")
    base = r.base()
    r.checkout("prod")
    r.edit(P, "rate = 0.02", "rate = 0.025")
    r.edit("docs/fees.md", "2%", "2.5%")
    hotfix = r.commit("HOTFIX: raise fee to 2.5%", author=OPS)
    r.deploy(hotfix)
    r.checkout("dev")
    r.edit(P, "rate = 0.02", "rate = 0.02 if amount < 1000 else 0.015")
    r.edit("docs/fees.md", "2%.", "2% (1.5% above 1000).")
    devfix = r.commit("Tiered fee")
    r.test(devfix, "dev")
    r.checkout("prod")
    assert r.pick(devfix) is None
    expect_conflict(r, ["app/payment.py", "docs/fees.md"])
    assert "|||||||" in r.read(P)
    assert r.hunks("docs/fees.md", marker="<" * 12) == 1
    r.abort()
    r.finish(
        "Repo uses merge.conflictStyle=diff3 (extra '||||||| base' section) and .md files use "
        "12-character conflict markers.",
        ["git checkout prod", f"git cherry-pick -x {devfix}"],
        {"base": base, "prod_hotfix": hotfix, "dev_fix": devfix},
        {
            "conflict": True, "files": ["app/payment.py", "docs/fees.md"],
            "must": ["parse the base section", "honour conflict-marker-size (or read index stages instead of markers)"],
        },
        checkout="prod",
    )


@scenario
def s35_literal_markers_in_content():
    r = Repo("s35_literal_markers_in_content")
    r.write("tests/fixtures/conflict_sample.txt",
            "<<<<<<< HEAD\nfoo\n=======\nbar\n>>>>>>> branch\n")
    base, hotfix, devfix = _hotfix_vs_devfix(r)
    r.finish(
        "A committed test fixture legitimately contains conflict markers. Only app/payment.py "
        "is actually in conflict.",
        ["git checkout prod", f"git cherry-pick -x {devfix}", "git ls-files -u"],
        {"base": base, "prod_hotfix": hotfix, "dev_fix": devfix},
        {
            "conflicted_files": [P],
            "must_not": ["report tests/fixtures/conflict_sample.txt as conflicted (don't grep the tree for markers)"],
        },
        checkout="prod",
    )


@scenario
def s36_rename_rename_and_add_add():
    r = Repo("s36_rename_rename_and_add_add")
    base = r.base()
    r.git("mv", P, "app/billing.py")
    r.write("app/config.py", "FEE_CAP = 500\n")
    d = r.commit("Rename to billing, add config")
    r.test(d, "dev")
    r.checkout("prod")
    r.git("mv", P, "app/fees.py")
    r.write("app/config.py", "FEE_CAP = 1000\n")
    p = r.commit("Rename to fees, add config", author=OPS)
    r.deploy(p)
    r.checkout("dev")
    assert r.merge("prod") is None
    files = r.unmerged()
    assert "app/config.py" in files and len(files) >= 2, files
    r.abort()
    r.finish(
        "Both sides renamed the same file to DIFFERENT names, and both added app/config.py "
        "with different contents.",
        ["git checkout dev", "git merge prod", "git status"],
        {"base": base, "dev_commit": d, "prod_commit": p},
        {
            "unmerged_paths": files,
            "kinds": {"app/payment.py": "rename/rename (billing.py vs fees.py)", "app/config.py": "add/add"},
            "ours": {"origin_commit": d, "status": "tested-in-dev-only"},
            "theirs": {"origin_commit": p, "status": "production-deployed"},
        },
        checkout="dev",
    )


@scenario
def s37_unicode_paths_crlf_and_modes():
    r = Repo("s37_unicode_paths_crlf_and_modes")
    weird = "app/über fees/größe.py"
    script = "bin/run fees.sh"
    r.write(weird, "RATE = 0.02\r\nCAP = 500\r\n")
    r.write(script, "#!/bin/sh\necho fees\n")
    base = r.base()
    r.checkout("prod")
    r.edit(weird, "RATE = 0.02", "RATE = 0.025")
    os.chmod(r.path / script, 0o755)
    hotfix = r.commit("HOTFIX: rate + make script executable", author=OPS)
    r.deploy(hotfix)
    r.checkout("dev")
    r.edit(weird, "RATE = 0.02", "RATE = 0.018")
    r.edit(script, "echo fees", "echo fees v2")
    devfix = r.commit("Lower rate, update script")
    r.test(devfix, "dev")
    assert r.merge("prod") is None
    expect_conflict(r, [unicodedata.normalize("NFC", weird)])
    r.abort()
    r.finish(
        "Conflict in a path with spaces and non-ASCII characters, in a CRLF file. The script "
        "merges cleanly but must end up executable (mode from prod, content from dev).",
        ["git checkout dev", "git merge prod",
         "git -c core.quotepath=false diff --name-only --diff-filter=U"],
        {"base": base, "prod_hotfix": hotfix, "dev_fix": devfix},
        {
            "conflicted_path": weird,
            "must": ["handle the path without octal escaping (use -z or core.quotepath=false)",
                     "keep CRLF line endings in the resolved file",
                     f"keep mode 100755 on '{script}'"],
        },
        checkout="dev",
    )


@scenario
def s38_shallow_clone():
    r = Repo("s38_shallow_clone")
    base = r.base()
    r.checkout("prod")
    r.edit(P, "rate = 0.02", "rate = 0.025")
    hotfix = r.commit("HOTFIX: raise fee to 2.5%", author=OPS)
    r.deploy(hotfix)
    r.edit(P, '"amount must be positive"', '"amount must be > 0"')
    p2 = r.commit("Shorter message", author=OPS)
    r.edit(P, "    return amount - fee", "    return max(amount - fee, 0)")
    p3 = r.commit("Clamp refunds", author=OPS)
    r.checkout("dev")
    r.edit(P, "rate = 0.02", "rate = 0.02 if amount < 1000 else 0.015")
    devfix = r.commit("Tiered fee")
    r.test(devfix, "dev")
    r.finish(
        "Use shallow/ — a --depth 2 clone. The prod line's real origin (the hotfix) is beyond "
        "the shallow boundary, so blame wrongly points at the boundary commit.",
        ["cd shallow", "git rev-parse --is-shallow-repository", "git checkout prod",
         f"git cherry-pick -x {devfix}", "# fix: git fetch --unshallow"],
        {"base": base, "prod_hotfix_beyond_boundary": hotfix, "prod_boundary": p2,
         "prod_tip": p3, "dev_fix": devfix},
        {
            "ours": {"origin": "UNKNOWN — beyond shallow boundary", "should_suggest": "git fetch --unshallow"},
            "must_not": [f"attribute the line to boundary commit {p2}", "crash on missing parents"],
        },
        checkout="prod",
    )
    s = Repo.at(r, r.dir / "shallow")
    clone(f"file://{r.path}", s.path, "--depth", "2", "--no-single-branch")
    assert s.git("rev-parse", "--is-shallow-repository") == "true"
    assert s.git("cat-file", "-e", hotfix, check=False).returncode != 0
    s.checkout("prod")
    assert s.pick(devfix) is None
    expect_conflict(s, [P], hunks=1)
    s.abort()


@scenario
def s39_same_fix_two_paths():
    r = Repo("s39_same_fix_two_paths")
    base = r.base()
    r.edit(P, "    return amount - fee", "    return max(amount - fee, 0)")
    f = r.commit("Clamp refunds")
    r.test(f, "dev")
    r.checkout("prod")
    p = r.pick(f, author=OPS)
    r.deploy(p)
    r.checkout("uat")
    m = r.merge("dev", author=QA)
    assert m
    r.test(m, "uat")
    r.checkout("prod")
    assert f"- {f}" in r.git("cherry", "prod", "uat")
    assert r.merge("uat", author=OPS)
    r.git("reset", "-q", "--hard", p)
    r.finish(
        "The same fix reached prod twice: once by cherry-pick (deployed), and again via a "
        "uat -> prod promotion. git merges it cleanly.",
        ["git checkout prod", "git cherry -v prod uat", "git merge uat"],
        {"base": base, "dev_fix": f, "prod_copy": p, "uat_merge": m},
        {
            "conflict": False,
            "promotion_report": {f: f"already in prod as {p} (deployed) — not new"},
            "must_not": [f"list {f} as an unverified change entering prod"],
        },
        checkout="prod",
    )


@scenario
def s40_large_history():
    r = Repo("s40_large_history")
    n = 200
    big = "app/big.py"
    r.write(big, "".join(f"def f{i}(x):\n    return x + {i}\n\n\n" for i in range(n)))
    base = r.commit("Big module")
    r.git("branch", "uat")
    r.git("branch", "prod")
    dev_sha, prod_sha = {}, {}
    for i in range(0, n, 2):
        r.edit(big, f"    return x + {i}\n", f"    return x * {i}\n")
        dev_sha[i] = r.commit(f"dev: change f{i}")
    r.test(dev_sha[n - 2], "dev")
    r.checkout("prod")
    for i in range(n):
        if i % 2 or i % 8 == 0:
            r.edit(big, f"    return x + {i}\n", f"    return x - {i}\n")
            prod_sha[i] = r.commit(f"prod: change f{i}", author=OPS)
    r.deploy(r.head())
    assert r.merge("dev") is None
    expect_conflict(r, [big], hunks=n // 8)
    r.abort()
    r.finish(
        f"{len(dev_sha) + len(prod_sha)} commits, one 800-line file, {n // 8} conflict hunks. Every hunk "
        "has a different origin commit on each side. CI ran only on dev tip; deploy only on prod tip.",
        ["git checkout prod", "git merge dev"],
        {"base": base, "dev_tip": dev_sha[n - 2], "prod_tip": prod_sha[n - 1]},
        {
            "hunks": [{"function": f"f{i}", "ours_origin": prod_sha[i], "theirs_origin": dev_sha[i]}
                      for i in range(0, n, 8)],
            "ours_status": "production-deployed (via prod tip)",
            "theirs_status": "dev-tested (via dev tip)",
            "performance": "should finish in a few seconds; no per-commit subprocess storm",
        },
        checkout="prod",
    )


# ===========================================================================
# Expert scenarios (s41-s61): git internals, hostile data, scale
# ===========================================================================

@scenario
def s41_both_sides_variants_of_same_fix():
    r = Repo("s41_both_sides_variants_of_same_fix")
    base = r.base()
    r.edit(P, "if amount <= 0:", "if amount is None or amount <= 0:")
    f = r.commit("Reject None amounts")
    r.test(f, "dev")
    r.checkout("uat")
    r.edit(P, "if amount <= 0:", "if not amount or amount <= 0:")
    b1 = r.commit(f"Reject None amounts\n\n(cherry picked from commit {f})", author=QA)
    r.test(b1, "uat")
    r.checkout("prod")
    r.edit(P, "if amount <= 0:", "if amount is None or amount < 1:")
    b2 = r.commit(f"Reject None amounts\n\n(cherry picked from commit {f})", author=OPS)
    r.test(b2, "production")
    r.deploy(b2)
    assert r.merge("uat") is None
    expect_conflict(r, [P], hunks=1)
    r.abort()
    r.finish(
        "The SAME dev fix was hand-backported twice, differently, to uat and prod. Merging uat "
        "into prod makes the two variants of one fix conflict with each other.",
        ["git checkout prod", "git merge uat"],
        {"base": base, "dev_original": f, "uat_variant": b1, "prod_variant": b2},
        {
            "ours": {"origin_commit": b2, "status": "production", "variant_of": f},
            "theirs": {"origin_commit": b1, "status": "uat", "variant_of": f},
            "should_explain": "both sides are versions of the same fix; prod's version is the one running",
        },
        checkout="prod",
    )


@scenario
def s42_code_moved_between_files():
    r = Repo("s42_code_moved_between_files")
    base = r.base()
    r.write(P, r.read(P).replace("\n\n\ndef refund(amount, fee):\n    return amount - fee\n", "\n"))
    r.write("app/refunds.py", "def refund(amount, fee):\n    return amount - fee\n")
    move = r.commit("Move refund() to app/refunds.py")
    r.test(move, "dev")
    r.checkout("prod")
    r.edit(P, "    return amount - fee", "    return max(amount - fee, 0)")
    hotfix = r.commit("HOTFIX: no negative refunds", author=OPS)
    r.deploy(hotfix)
    r.checkout("dev")
    assert r.merge("prod") is None
    expect_conflict(r, [P])
    r.abort()
    r.finish(
        "dev MOVED refund() into another file; prod hot-fixed it in the old place. git sees "
        "'deleted vs modified lines' — the hotfix would be silently lost if 'ours' is taken.",
        ["git checkout dev", "git merge prod", "git log -S'def refund' --oneline --all"],
        {"base": base, "dev_move": move, "prod_hotfix": hotfix},
        {
            "ours": {"origin_commit": move, "status": "dev", "note": "lines deleted here because they moved"},
            "theirs": {"origin_commit": hotfix, "status": "production"},
            "moved_to": "app/refunds.py",
            "should_warn": "re-apply the hotfix in app/refunds.py",
        },
        checkout="dev",
    )


@scenario
def s43_formatter_commit_hides_origin():
    r = Repo("s43_formatter_commit_hides_origin")
    base = r.base()
    r.edit(P, "rate = 0.02", "rate = 0.02 if amount < 1000 else 0.015")
    f = r.commit("Tiered fee")
    r.test(f, "dev")
    r.edit(P, "rate = 0.02 if amount < 1000 else 0.015", "rate = 0.02  if amount<1000 else  0.015")
    fmt = r.commit("Run code formatter")
    assert r.git("diff", "-w", f, fmt) == ""
    r.write(".git-blame-ignore-revs", f"# code formatter\n{fmt}\n")
    r.commit("Ignore formatter commit in blame")
    r.git("config", "blame.ignoreRevsFile", ".git-blame-ignore-revs")
    r.checkout("prod")
    r.edit(P, "rate = 0.02", "rate = 0.025")
    hotfix = r.commit("HOTFIX: raise fee to 2.5%", author=OPS)
    r.deploy(hotfix)
    assert r.merge("dev") is None
    expect_conflict(r, [P], hunks=1)
    r.abort()
    r.finish(
        "The dev line was last touched by a whitespace-only formatter commit listed in "
        ".git-blame-ignore-revs. The real origin is the commit before it.",
        ["git checkout prod", "git merge dev", f"git blame -w --ignore-revs-file .git-blame-ignore-revs dev -- {P}"],
        {"base": base, "real_origin": f, "formatter": fmt, "prod_hotfix": hotfix},
        {
            "theirs": {"origin_commit": f, "status": "dev"},
            "must_not": [f"report the formatter commit {fmt} as the origin"],
        },
        checkout="prod",
    )


@scenario
def s44_tested_trailer_without_ci():
    r = Repo("s44_tested_trailer_without_ci")
    base = r.base()
    r.test(base, "dev")
    r.checkout("prod")
    r.edit(P, "rate = 0.02", "rate = 0.025")
    hotfix = r.commit("HOTFIX: raise fee to 2.5%", author=OPS)
    r.test(hotfix, "production")
    r.deploy(hotfix)
    r.checkout("dev")
    r.edit(P, "rate = 0.02", "rate = 0.02 if amount < 1000 else 0.015")
    devfix = r.commit("Tiered fee\n\nTested-in: production\nTested-by: Bob Backport <bob@example.com>")
    r.checkout("prod")
    assert r.pick(devfix) is None
    expect_conflict(r, [P], hunks=1)
    r.abort()
    r.finish(
        "The dev commit's message CLAIMS 'Tested-in: production', but CI/deploy records have "
        "nothing for it. A self-written trailer is not evidence.",
        ["git checkout prod", f"git cherry-pick -x {devfix}", f"git log -1 --format=%B {devfix}"],
        {"base": base, "prod_hotfix": hotfix, "dev_fix": devfix},
        {
            "theirs": {"origin_commit": devfix, "status": "unverified"},
            "must_not": ["trust Tested-in / Tested-by trailers as test records"],
        },
        checkout="prod",
    )


@scenario
def s45_rollback_records_out_of_order():
    r = Repo("s45_rollback_records_out_of_order")
    base = r.base()
    r.checkout("prod")
    r.edit(P, '"amount must be positive"', '"amount must be > 0"')
    p1 = r.commit("Shorter validation message", author=OPS)
    r.test(p1, "production", at="2026-09-30T09:00:00Z")
    r.edit(P, "rate = 0.02", "rate = 0.025")
    p2 = r.commit("Raise fee to 2.5%", author=OPS)
    # The rollback was imported FIRST from a late log; append order != time order.
    r.deploy(p1, at="2026-10-03T09:00:00Z")
    r.deploy(p1, at="2026-10-01T09:00:00Z")
    r.deploy(p2, at="2026-10-02T09:00:00Z")
    r.checkout("dev")
    r.edit(P, "rate = 0.02", "rate = 0.018")
    devfix = r.commit("Lower fee")
    r.test(devfix, "dev")
    r.checkout("prod")
    assert r.pick(devfix) is None
    expect_conflict(r, [P], hunks=1)
    r.abort()
    r.finish(
        "Same rollback story as s25, but the records were appended out of order. Only the "
        "'at' timestamps tell the truth: p1 (Oct 1) -> p2 (Oct 2) -> p1 again (Oct 3).",
        ["git checkout prod", f"git notes --ref=tests show {p1}", f"git cherry-pick -x {devfix}"],
        {"base": base, "p1": p1, "p2_rolled_back": p2, "dev_fix": devfix},
        {
            "currently_in_production": p1,
            "ours": {"origin_commit": p2, "status": "rolled-back"},
            "must_not": ["order records by position in the note instead of by 'at'"],
        },
        checkout="prod",
    )


@scenario
def s46_symlink_conflict():
    r = Repo("s46_symlink_conflict")
    for env in ("base", "dev", "prod"):
        r.write(f"envs/{env}.yml", f"profile: {env}\n")
    (r.path / "config").mkdir()
    link = r.path / "config" / "current.yml"
    os.symlink("../envs/base.yml", link)
    base = r.base()
    r.checkout("prod")
    link.unlink()
    os.symlink("../envs/prod.yml", link)
    p = r.commit("Point config at prod profile", author=OPS)
    r.deploy(p)
    r.checkout("dev")
    link.unlink()
    os.symlink("../envs/dev.yml", link)
    d = r.commit("Point config at dev profile")
    r.test(d, "dev")
    assert r.merge("prod") is None
    expect_conflict(r, ["config/current.yml"])
    r.abort()
    r.finish(
        "A symlink was re-pointed differently on each side. No text, no markers — the "
        "conflict is between two link targets (mode 120000).",
        ["git checkout dev", "git merge prod", "git ls-files -u -s"],
        {"base": base, "dev_commit": d, "prod_commit": p},
        {
            "ours": {"origin_commit": d, "status": "dev", "target": "../envs/dev.yml"},
            "theirs": {"origin_commit": p, "status": "production", "target": "../envs/prod.yml"},
            "must_not": ["follow the link and diff the target files", "crash on mode 120000"],
        },
        checkout="dev",
    )


@scenario
def s47_submodule_conflict():
    r = Repo("s47_submodule_conflict")
    lib = Repo.at(r, r.dir / "lib-src")
    lib.path.mkdir()
    lib.git("init", "-q", "-b", "main")
    lib.write("lib.py", "VERSION = 1\n")
    l1 = lib.commit("v1")
    lib.write("lib.py", "VERSION = 2\n")
    l2 = lib.commit("v2")
    lib.git("checkout", "-q", "-b", "hotfix", l1)
    lib.write("lib.py", "VERSION = 1.1\n")
    l3 = lib.commit("v1.1")
    r.write(".gitmodules", '[submodule "lib"]\n\tpath = lib\n\turl = ../lib-src\n')
    (r.path / "lib").mkdir()
    r.git("update-index", "--add", "--cacheinfo", f"160000,{l1},lib")
    base = r.base()

    def bump(sha, msg, author=ALICE):
        r.git("update-index", "--cacheinfo", f"160000,{sha},lib")
        r.git("commit", "-q", "-m", msg, author=author)
        return r.head()

    r.checkout("prod")
    p = bump(l3, "Bump lib to v1.1 (security fix)", OPS)
    r.deploy(p)
    r.checkout("dev")
    d = bump(l2, "Bump lib to v2")
    r.test(d, "dev")
    assert r.git("ls-files", "-s", "lib").startswith("160000")
    assert r.merge("prod") is None
    expect_conflict(r, ["lib"])
    r.abort()
    r.finish(
        "Submodule pointer conflict: dev moved lib to v2, prod moved it to a v1.1 security "
        "fix. The 'file' in conflict is a gitlink, not text.",
        ["git checkout dev", "git merge prod", "git ls-files -u -s lib"],
        {"base": base, "lib_v1": l1, "lib_v2": l2, "lib_v1_1": l3, "dev_commit": d, "prod_commit": p},
        {
            "ours": {"origin_commit": d, "status": "dev", "points_to": l2},
            "theirs": {"origin_commit": p, "status": "production", "points_to": l3},
            "should_warn": "taking ours drops the production security fix in lib",
        },
        checkout="dev",
    )


@scenario
def s48_mixed_origins_in_one_hunk():
    r = Repo("s48_mixed_origins_in_one_hunk")
    base = r.base()
    r.checkout("prod")
    r.edit(P, "rate = 0.02", "rate = 0.025")
    h1 = r.commit("HOTFIX: raise fee to 2.5%", author=OPS)
    r.test(h1, "production")
    r.deploy(h1)
    r.edit(P, "    return round(amount * rate, 2)", "    return round(amount * rate, 3)")
    h2 = r.commit("Round fees to 3 places", author=OPS)
    r.checkout("dev")
    r.edit(P, "rate = 0.02", "rate = 0.018")
    r.edit(P, "    return round(amount * rate, 2)", "    return round(amount * rate, 4)")
    devfix = r.commit("Lower fee, finer rounding")
    r.test(devfix, "uat")
    r.checkout("prod")
    assert r.pick(devfix) is None
    expect_conflict(r, [P], hunks=1)
    r.abort()
    r.finish(
        "ONE hunk whose prod side mixes two commits: line 1 deployed, line 2 never deployed. "
        "The hunk is only as trustworthy as its weakest line.",
        ["git checkout prod", f"git cherry-pick -x {devfix}", f"git blame prod -- {P}"],
        {"base": base, "prod_deployed_line": h1, "prod_undeployed_line": h2, "dev_fix": devfix},
        {
            "ours": {"origin_commits": [h1, h2], "status": "unverified (weakest line wins)"},
            "theirs": {"origin_commit": devfix, "status": "uat"},
            "must_not": ["report only the first line's commit and call the hunk production"],
        },
        checkout="prod",
    )


@scenario
def s49_reverted_fix_reintroduced_by_merge():
    r = Repo("s49_reverted_fix_reintroduced_by_merge")
    base = r.base()
    r.edit(P, "    return amount - fee", "    return max(amount - fee, 0)")
    f = r.commit("Clamp refunds")
    r.test(f, "dev")
    r.checkout("prod")
    p = r.pick(f, author=OPS)
    r.deploy(p)
    r.test(p, "production", result="fail")
    r.git("revert", "--no-edit", p, author=OPS)
    rv = r.head()
    r.deploy(rv)
    assert r.merge("dev", author=OPS), "merge should be clean"
    assert "max(amount - fee, 0)" in r.read(P)
    r.git("reset", "-q", "--hard", rv)
    r.finish(
        "NO conflict — that's the trap. The fix failed in production and was reverted there, "
        "but dev still has the original. 'git merge dev' silently brings the bad fix back.",
        ["git checkout prod", "git merge dev   # clean, re-introduces the reverted change"],
        {"base": base, "dev_fix": f, "prod_copy": p, "prod_revert": rv},
        {
            "conflict": False,
            "dev_fix_status": "failed / rolled-back (its production copy failed and was reverted)",
            "should_warn": f"merging dev re-introduces {f}, equivalent of reverted {p}",
        },
        checkout="prod",
    )


@scenario
def s50_deep_history():
    r = Repo("s50_deep_history")
    n = 5000
    t0 = 1790000000

    def data(text):
        return f"data {len(text.encode())}\n{text}\n"

    def commit(mark, ts, msg, frm, path, content):
        return (f"commit refs/heads/dev\nmark :{mark}\n"
                f"committer Alice Dev <alice@example.com> {ts} +0530\n" + data(msg)
                + (f"from :{frm}\n" if frm else "") + f"M 100644 inline {path}\n" + data(content))

    stream = [commit(1, t0, "Initial payment module", None, P, BASE_PAYMENT),
              "reset refs/heads/uat\nfrom :1\n\nreset refs/heads/prod\nfrom :1\n\n",
              commit(2, t0 + 60, "Fee 2.1%", 1, P, BASE_PAYMENT.replace("rate = 0.02", "rate = 0.021"))]
    stream += [commit(i + 3, t0 + 120 + i, f"chore: housekeeping {i}", i + 2, f"filler/{i % 50}.txt", f"{i}\n")
               for i in range(n)]
    r.git("fast-import", "--quiet", stdin="".join(stream))
    r.git("reset", "-q", "--hard")
    origin = r.git("rev-parse", f"dev~{n}")
    base = r.git("rev-parse", f"{origin}^")
    tip = r.head()
    r.test(tip, "dev")
    r.checkout("prod")
    r.edit(P, "rate = 0.02", "rate = 0.025")
    hotfix = r.commit("HOTFIX: raise fee to 2.5%", author=OPS)
    r.deploy(hotfix)
    assert r.merge("dev") is None
    expect_conflict(r, [P], hunks=1)
    r.abort()
    r.finish(
        f"The dev line's origin is {n} commits deep; CI only ran on the dev tip. "
        "Any per-commit subprocess loop will time out.",
        ["git checkout prod", "git merge dev"],
        {"base": base, "deep_origin": origin, "dev_tip_tested": tip, "prod_hotfix": hotfix},
        {
            "theirs": {"origin_commit": origin, "status": "dev (via tested tip, line unchanged since)"},
            "ours": {"origin_commit": hotfix, "status": "production"},
            "performance": "must answer within 60 s",
        },
        checkout="prod",
    )


@scenario
def s51_non_utf8_and_no_final_newline():
    r = Repo("s51_non_utf8_and_no_final_newline")
    lat, lim = "app/legacy_latin1.py", "app/limits.txt"
    (r.path / "app").mkdir()
    (r.path / lat).write_bytes(b"# Caf\xe9 fees\nRATE = 0.02\n")
    (r.path / lim).write_bytes(b"MAX=100")
    base = r.base()
    r.checkout("prod")
    r.edit_bytes(lat, b"RATE = 0.02", b"RATE = 0.025")
    r.edit_bytes(lim, b"MAX=100", b"MAX=200")
    p = r.commit("HOTFIX: limits", author=OPS)
    r.deploy(p)
    r.checkout("dev")
    r.edit_bytes(lat, b"RATE = 0.02", b"RATE = 0.018")
    r.edit_bytes(lim, b"MAX=100", b"MAX=50")
    d = r.commit("Lower limits")
    r.test(d, "dev")
    assert r.merge("prod") is None
    expect_conflict(r, [lat, lim])
    r.abort()
    r.finish(
        "One conflicted file is Latin-1 (not UTF-8); the other has no newline at end of file.",
        ["git checkout dev", "git merge prod"],
        {"base": base, "dev_commit": d, "prod_commit": p},
        {
            "files": [lat, lim],
            "must_not": ["crash with UnicodeDecodeError", "add or drop the final newline when resolving",
                         "re-encode the Latin-1 file as UTF-8"],
        },
        checkout="dev",
    )


@scenario
def s52_deletion_vs_modification():
    r = Repo("s52_deletion_vs_modification")
    base = r.base()
    r.checkout("prod")
    r.edit(P, '    if amount <= 0:\n        raise ValueError("amount must be positive")\n', "")
    hotfix = r.commit("HOTFIX: allow zero-amount transactions", author=OPS)
    r.test(hotfix, "production")
    r.deploy(hotfix)
    r.checkout("dev")
    r.edit(P, "if amount <= 0:", "if amount is None or amount <= 0:")
    devfix = r.commit("Reject None amounts")
    r.test(devfix, "dev")
    r.checkout("prod")
    assert r.pick(devfix) is None
    expect_conflict(r, [P], hunks=1)
    r.abort()
    r.finish(
        "prod's side of the hunk is EMPTY: the hotfix deleted those lines. Blame has nothing "
        "to point at; the origin is the commit that deleted them.",
        ["git checkout prod", f"git cherry-pick -x {devfix}",
         f"git log -S'amount must be positive' --oneline prod -- {P}"],
        {"base": base, "prod_deleting_hotfix": hotfix, "dev_fix": devfix},
        {
            "ours": {"origin_commit": hotfix, "status": "production", "change": "deletion"},
            "theirs": {"origin_commit": devfix, "status": "dev"},
            "must_not": ["report origin null/unknown just because the side has no lines"],
        },
        checkout="prod",
    )


@scenario
def s53_original_only_in_remote_ref():
    r = Repo("s53_original_only_in_remote_ref")
    base = r.base()
    r.git("checkout", "-q", "-b", "upstream-work")
    r.edit(P, "rate = 0.02", "rate = 0.018")
    f = r.commit("Lower fee")
    r.test(f, "dev")
    r.checkout("prod")
    p = r.pick(f, author=OPS)
    r.deploy(p)
    r.git("update-ref", "refs/remotes/upstream/main", f)
    r.git("branch", "-D", "upstream-work")
    assert r.git("branch", "--contains", f) == ""
    r.finish(
        "The original commit is reachable ONLY from refs/remotes/upstream/main (like a fork "
        "tracking upstream). No local branch contains it.",
        ["git branch -a --contains " + f, "git log prod -1 --format=%B"],
        {"base": base, "upstream_original": f, "prod_copy": p},
        {
            "match": {"backport": p, "original": f, "exact": True},
            "must": ["search all refs (git log --all / for-each-ref), not just local branches"],
        },
        checkout="prod",
    )


@scenario
def s54_git_am_backport():
    r = Repo("s54_git_am_backport")
    base, hotfix, devfix = _hotfix_vs_devfix(r)
    patch = r.git("format-patch", "-1", "--stdout", devfix)
    (r.dir / "backport.patch").write_text(patch + "\n")
    res = r.git("am", "-3", str(r.dir / "backport.patch"), check=False, author=BOB)
    assert res.returncode != 0
    expect_conflict(r, [P], hunks=1)
    r.abort()
    r.finish(
        "Backport by e-mailed patch (git format-patch | git am -3), Linux-kernel style. There is "
        "no CHERRY_PICK_HEAD and no trailer — the original is only identifiable by its diff.",
        ["git checkout prod", "git am -3 ../backport.patch", "cat .git/rebase-apply/patch"],
        {"base": base, "prod_hotfix": hotfix, "dev_fix": devfix},
        {
            "operation": "am",
            "theirs": {"origin_commit": devfix, "status": "dev", "found_by": "patch-id of the mailed patch"},
            "ours": {"origin_commit": hotfix, "status": "production"},
        },
        checkout="prod",
    )


@scenario
def s55_conflict_in_linked_worktree():
    r = Repo("s55_conflict_in_linked_worktree")
    base, hotfix, devfix = _hotfix_vs_devfix(r)
    r.checkout("dev")
    wt = r.dir / "wt-check"
    r.git("worktree", "add", "-q", str(wt), "prod")
    w = Repo.at(r, wt)
    assert w.pick(devfix) is None
    expect_conflict(w, [P], hunks=1)
    w.git("cherry-pick", "--abort")
    r.git("worktree", "remove", "--force", str(wt))
    r.finish(
        "The conflict happens in a linked worktree, where .git is a FILE pointing elsewhere "
        "and CHERRY_PICK_HEAD lives in .git/worktrees/<name>/.",
        ["git worktree add ../wt prod", "cd ../wt", f"git cherry-pick -x {devfix}"],
        {"base": base, "prod_hotfix": hotfix, "dev_fix": devfix},
        {
            "same_answer_as": "s02",
            "must": ["use git rev-parse --git-dir / --git-path, never hard-code .git/"],
        },
        checkout="dev",
    )


@scenario
def s56_squash_merge_conflict():
    r = Repo("s56_squash_merge_conflict")
    base, hotfix, devfix = _hotfix_vs_devfix(r)
    assert r.git("merge", "--squash", "dev", check=False).returncode != 0
    expect_conflict(r, [P], hunks=1)
    assert not (r.path / ".git" / "MERGE_HEAD").exists()
    r.abort()
    r.finish(
        "'git merge --squash' conflict: there is NO MERGE_HEAD. The incoming commits are only "
        "listed in .git/SQUASH_MSG.",
        ["git checkout prod", "git merge --squash dev", "cat .git/SQUASH_MSG"],
        {"base": base, "prod_hotfix": hotfix, "dev_fix": devfix},
        {
            "operation": "merge or squash-merge",
            "theirs": {"origin_commit": devfix, "status": "dev"},
            "must_not": ["crash or report 'no operation' because MERGE_HEAD is missing"],
        },
        checkout="prod",
    )


@scenario
def s57_partially_resolved():
    r = Repo("s57_partially_resolved")
    base = r.base(legacy=True)
    r.checkout("prod")
    r.edit(P, "rate = 0.02", "rate = 0.025")
    r.edit(L, "0.03", "0.035")
    hotfix = r.commit("HOTFIX: fees", author=OPS)
    r.test(hotfix, "production")
    r.deploy(hotfix)
    r.checkout("dev")
    r.edit(P, "rate = 0.02", "rate = 0.018")
    r.edit(L, "0.03", "0.028")
    devfix = r.commit("Lower fees")
    r.test(devfix, "dev")
    r.checkout("prod")
    assert r.pick(devfix) is None
    expect_conflict(r, [L, P])
    r.abort()
    r.finish(
        "Two files conflict; the developer already resolved app/legacy.py (git add). The tool "
        "is run halfway through the resolution.",
        ["git checkout prod", f"git cherry-pick -x {devfix}",
         "git checkout --theirs app/legacy.py && git add app/legacy.py"],
        {"base": base, "prod_hotfix": hotfix, "dev_fix": devfix},
        {
            "still_conflicted": [P],
            "must_not": ["report app/legacy.py as still conflicted", "lose the user's staged resolution"],
        },
        checkout="prod",
    )


@scenario
def s58_ambiguous_short_sha():
    import hashlib
    r = Repo("s58_ambiguous_short_sha")
    base = r.base()
    r.edit(P, "    return amount - fee", "    return max(amount - fee, 0)")
    f = r.commit("Clamp refunds")
    r.test(f, "dev")
    tree = r.git("rev-parse", f"{base}^{{tree}}")
    prefix = f[:5]
    ident = "Dee Coy <decoy@example.com> 1790000000 +0530"
    nonce = 0
    while True:
        body = f"tree {tree}\nparent {base}\nauthor {ident}\ncommitter {ident}\n\nexperiment {nonce}\n"
        raw = body.encode()
        if hashlib.sha1(b"commit %d\0" % len(raw) + raw).hexdigest().startswith(prefix):
            break
        nonce += 1
    decoy = r.git("hash-object", "-t", "commit", "-w", "--stdin", stdin=body)
    assert decoy.startswith(prefix) and decoy != f
    r.git("update-ref", "refs/heads/experiment", decoy)
    assert r.git("rev-parse", "--verify", "-q", prefix, check=False).returncode != 0
    r.checkout("prod")
    r.edit(P, "    return amount - fee", "    return max(amount - fee, 0)")
    p = r.commit(f"Clamp refunds\n\nBackport of {prefix} from dev.", author=OPS)
    r.deploy(p)
    assert r.patch_id(p) == r.patch_id(f)
    r.finish(
        f"The backport message says 'Backport of {prefix}', but TWO commits start with that "
        "prefix (git rev-parse refuses: ambiguous). Only the code tells which one is meant.",
        [f"git rev-parse {prefix}   # error: ambiguous", "git log prod -1 --format=%B"],
        {"base": base, "dev_fix": f, "decoy": decoy, "prod_copy": p},
        {
            "match": {"backport": p, "original": f, "exact": True},
            "must_not": [f"match the decoy {decoy}", "crash on the ambiguous prefix"],
        },
        checkout="prod",
    )


@scenario
def s59_noisy_provenance_records():
    r = Repo("s59_noisy_provenance_records")
    base, hotfix, devfix = _hotfix_vs_devfix(r, record_tests=False)
    r.test(hotfix, "production")
    r.deploy(hotfix)
    r.test(devfix, "uat")
    r.deploy(devfix, result="failure")
    r.test(devfix, "perf-lab")
    r._record(devfix, "uat", "smoke", "pass")
    r.git("notes", "--ref=tests", "append", "-m", "not json {{", devfix)
    r.finish(
        "The dev commit's note mixes good and bad data: a uat pass, a FAILED production deploy, "
        "an unknown env, an unknown record kind, and a line that isn't JSON.",
        ["git checkout prod", f"git notes --ref=tests show {devfix}", f"git cherry-pick -x {devfix}"],
        {"base": base, "prod_hotfix": hotfix, "dev_fix": devfix},
        {
            "theirs": {"origin_commit": devfix, "status": "uat (the production deploy FAILED)"},
            "must_not": ["crash on the malformed line", "count a failed deploy as production"],
        },
        checkout="prod",
    )


@scenario
def s60_cherry_picked_merge_commit():
    r = Repo("s60_cherry_picked_merge_commit")
    base = r.base()
    r.git("checkout", "-q", "-b", "feature")
    r.edit(P, "if amount <= 0:", "if amount <= 0 or amount > 1_000_000:")
    r.commit("Add amount cap")
    r.edit(P, "    return amount - fee", "    return max(amount - fee, 0)")
    r.commit("Clamp refunds")
    r.checkout("dev")
    r.git("merge", "--no-ff", "--no-edit", "feature")
    m = r.head()
    r.test(m, "uat")
    r.checkout("prod")
    assert r.git("cherry-pick", "-x", "-m", "1", m, check=False, author=OPS).returncode == 0
    p = r.head()
    r.deploy(p)
    assert r.patch_id(m) is None, "clean merge should have an empty combined diff"
    assert r.patch_id(p) == r.patch_id(m, base=f"{m}^1")
    r.finish(
        "A whole merged PR was backported with 'git cherry-pick -m 1 <merge>'. The trailer "
        "names a MERGE commit, whose plain 'git show' diff is empty.",
        ["git log prod -1 --format=%B", f"git show {m}   # no diff", f"git diff {m}^1 {m}"],
        {"base": base, "dev_merge": m, "prod_copy": p},
        {
            "match": {"backport": p, "original": m, "exact": True, "how": f"compare against diff {m}^1..{m}"},
        },
        checkout="prod",
    )


@scenario
def s61_empty_cherry_pick():
    r = Repo("s61_empty_cherry_pick")
    base = r.base()
    r.edit(P, "    return amount - fee", "    return max(amount - fee, 0)")
    f = r.commit("Clamp refunds")
    r.test(f, "dev")
    r.checkout("prod")
    r.edit(P, "    return amount - fee", "    return max(amount - fee, 0)")
    h = r.commit("Clamp refunds (applied by hand)", author=OPS)
    r.deploy(h)
    assert r.git("cherry-pick", "-x", f, check=False).returncode != 0
    assert r.unmerged() == [] and (r.path / ".git" / "CHERRY_PICK_HEAD").exists()
    r.abort()
    r.finish(
        "Cherry-pick stops with NO conflict: the change is already on prod (applied by hand), "
        "so the pick is empty. git just says 'now empty'.",
        ["git checkout prod", f"git cherry-pick -x {f}"],
        {"base": base, "dev_fix": f, "prod_hand_applied": h},
        {
            "operation": "cherry-pick", "conflicts": [],
            "should_explain": f"already applied as {h}, which is in production",
        },
        checkout="prod",
    )


# ===========================================================================
# Resolve scenarios (r01-r10): the tool must FIX the conflict (or refuse)
#
# expected.json gets a "resolve" spec: how to trigger the conflict, whether the
# tool must resolve / refuse / may do either, and what the result must satisfy.
# Each repo has real unit tests; a correct resolution makes them pass.
# ===========================================================================

TEST_CMD = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-q"]
CONTINUE = {"merge": ("commit", "-q", "--no-edit"), "cherry-pick": ("cherry-pick", "--continue"),
            "rebase": ("rebase", "--continue")}


def testfile(imports, *asserts):
    body = "\n".join(f"        {a}" for a in asserts)
    return f"import unittest\n{imports}\n\n\nclass T(unittest.TestCase):\n    def test_it(self):\n{body}\n"


def pr_base(r, extra=None):
    """A small project on branch 'main' with app/ and a unittest suite."""
    r.write(P, BASE_PAYMENT)
    r.write("app/__init__.py", "")
    r.write("tests/__init__.py", "")
    r.write("tests/test_base.py", testfile("from app.payment import validate", "self.assertTrue(validate(5))"))
    for path, content in (extra or {}).items():
        r.write(path, content)
    base = r.commit("Initial project")
    r.git("branch", "-m", "main")
    return base


def tests_pass(r):
    return subprocess.run(TEST_CMD, cwd=r.path, capture_output=True,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}).returncode == 0


def verify_resolve(r, spec, conflicted, naive_sides_fail=False):
    """Trigger the conflict, prove the spec is right, then restore every branch."""
    start = r.git("symbolic-ref", "--short", "HEAD")
    refs = r.git("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads").splitlines()
    for cmd in spec["setup"]:
        r.sh(cmd)
    expect_conflict(r, conflicted)
    if naive_sides_fail:
        for side_flag in ("--ours", "--theirs"):
            r.git("checkout", side_flag, "--", *conflicted)
            assert not tests_pass(r), f"[{r.name}] taking {side_flag} alone should fail the tests"
            r.git("checkout", "-m", "--", *conflicted)
    if spec.get("solution"):
        for path, content in spec["solution"].items():
            r.write(path, content)
            r.git("add", "--", path)
        r.git(*CONTINUE[spec["operation"]], author=BOB)
        assert not r.unmerged(), f"[{r.name}] solution left conflicts"
        assert tests_pass(r), f"[{r.name}] the reference solution fails the tests"
        parents = len(r.git("rev-list", "--parents", "-n1", "HEAD").split()) - 1
        assert parents == spec["parents"], f"[{r.name}] solution has {parents} parents"
        for path, needles in spec.get("contains", {}).items():
            text = r.git("show", f"HEAD:{path}")
            assert all(n in text for n in needles), f"[{r.name}] solution misses {needles} in {path}"
        for path, needles in spec.get("exactly_once", {}).items():
            text = r.git("show", f"HEAD:{path}")
            assert all(text.count(n) == 1 for n in needles), f"[{r.name}] duplicates in {path}"
        if spec.get("trailer"):
            assert spec["trailer"] in r.git("log", "-1", "--format=%B")
    r.abort()
    for line in refs:
        ref, sha = line.split()
        r.git("update-ref", ref, sha)
    r.git("checkout", "-q", "-f", start)
    r.git("reset", "-q", "--hard")


def finish_resolve(r, description, commits, spec, checkout="main"):
    r.finish(description, spec["setup"], commits, {"resolve": spec}, checkout=checkout)


@scenario
def r01_changelog_both_added():
    r = Repo("r01_changelog_both_added")
    base = pr_base(r, {"CHANGELOG.md": "# Changelog\n\n## Unreleased\n\n- Initial release\n"})
    r.git("checkout", "-q", "-b", "pr-12")
    r.edit("CHANGELOG.md", "## Unreleased\n\n", "## Unreleased\n\n- Fix negative refunds (#12)\n")
    r.edit(P, "    return amount - fee", "    return max(amount - fee, 0)")
    r.write("tests/test_refund.py", testfile("from app.payment import refund", "self.assertEqual(refund(1, 5), 0)"))
    pr = r.commit("Fix negative refunds (#12)")
    r.test(pr, "dev")
    r.checkout("main")
    r.edit("CHANGELOG.md", "## Unreleased\n\n", "## Unreleased\n\n- Add amount cap (#11)\n")
    r.edit(P, "if amount <= 0:", "if amount <= 0 or amount > 1_000_000:")
    m = r.commit("Add amount cap (#11)")
    r.test(m, "uat")
    spec = {
        "expect": "resolved", "operation": "merge", "parents": 2, "tests": True,
        "setup": ["git checkout -q main", "git merge --no-edit pr-12"],
        "contains": {"CHANGELOG.md": ["- Add amount cap (#11)", "- Fix negative refunds (#12)", "- Initial release"],
                     P: ["max(amount - fee, 0)", "amount > 1_000_000"]},
        "exactly_once": {"CHANGELOG.md": ["- Add amount cap (#11)", "- Fix negative refunds (#12)"]},
        "solution": {"CHANGELOG.md": "# Changelog\n\n## Unreleased\n\n- Add amount cap (#11)\n"
                                     "- Fix negative refunds (#12)\n- Initial release\n"},
    }
    verify_resolve(r, spec, ["CHANGELOG.md"])
    finish_resolve(r, "Merging PR #12: both PRs added a line at the same spot in CHANGELOG.md. "
                      "Safe to fix automatically: keep both entries.",
                   {"base": base, "pr_commit": pr, "main_commit": m}, spec)


@scenario
def r02_requirements_both_added():
    r = Repo("r02_requirements_both_added")
    base = pr_base(r, {"requirements.txt": "requests==2.31.0\n"})
    r.git("checkout", "-q", "-b", "pr-20")
    r.write("requirements.txt", "requests==2.31.0\nclick==8.1.7\nrich==13.7.0\n")
    pr = r.commit("Add CLI (#20)")
    r.checkout("main")
    r.write("requirements.txt", "requests==2.31.0\nrich==13.7.0\n")
    m = r.commit("Pretty output (#19)")
    spec = {
        "expect": "resolved", "operation": "merge", "parents": 2, "tests": True,
        "setup": ["git checkout -q main", "git merge --no-edit pr-20"],
        "contains": {"requirements.txt": ["requests==2.31.0", "click==8.1.7", "rich==13.7.0"]},
        "exactly_once": {"requirements.txt": ["requests==2.31.0", "click==8.1.7", "rich==13.7.0"]},
        "solution": {"requirements.txt": "requests==2.31.0\nrich==13.7.0\nclick==8.1.7\n"},
    }
    verify_resolve(r, spec, ["requirements.txt"])
    finish_resolve(r, "Both sides appended dependencies at the end of requirements.txt, and BOTH added "
                      "rich==13.7.0. Keep every dependency exactly once (naive 'keep both' duplicates rich).",
                   {"base": base, "pr_commit": pr, "main_commit": m}, spec)


@scenario
def r03_whitespace_only_side():
    r = Repo("r03_whitespace_only_side")
    base = pr_base(r)
    r.git("checkout", "-q", "-b", "pr-7")
    r.edit(P, "rate = 0.02", "rate = 0.02 if amount < 1000 else 0.015")
    r.write("tests/test_tier.py", testfile("from app.payment import calculate_fee",
                                           "self.assertEqual(calculate_fee(2000), 30.0)"))
    pr = r.commit("Tiered fee (#7)")
    r.test(pr, "dev")
    r.checkout("main")
    r.edit(P, "rate = 0.02", "rate  =  0.02")
    fmt = r.commit("Run code formatter")
    spec = {
        "expect": "resolved", "operation": "merge", "parents": 2, "tests": True,
        "setup": ["git checkout -q main", "git merge --no-edit pr-7"],
        "contains": {P: ["if amount < 1000 else 0.015"]},
        "solution": {P: BASE_PAYMENT.replace("rate = 0.02", "rate = 0.02 if amount < 1000 else 0.015")},
    }
    verify_resolve(r, spec, [P], naive_sides_fail=False)
    finish_resolve(r, "main's side of the conflict is a whitespace-only formatter change; the PR's side "
                      "is real logic. Safe to fix automatically: keep the PR's logic.",
                   {"base": base, "pr_commit": pr, "formatter": fmt}, spec)


@scenario
def r04_pr_carries_stale_copy_of_fix():
    r = Repo("r04_pr_carries_stale_copy_of_fix")
    base = pr_base(r)
    r.edit(P, "rate = 0.02", "rate = 0.02 if amount < 1000 else 0.015")
    f = r.commit("Tiered fee")
    r.edit(P, "if amount < 1000", "if amount < 5000")
    r.write("tests/test_threshold.py", testfile("from app.payment import calculate_fee",
                                                "self.assertEqual(calculate_fee(2000), 40.0)"))
    e = r.commit("Raise tier threshold to 5000")
    r.test(e, "production")
    r.deploy(e)
    r.git("checkout", "-q", "-b", "pr-15", base)
    stale = r.pick(f)
    r.edit(P, "    return amount - fee", "    return max(amount - fee, 0)")
    r.write("tests/test_refund.py", testfile("from app.payment import refund", "self.assertEqual(refund(1, 5), 0)"))
    pr = r.commit("Clamp refunds (#15)")
    r.checkout("main")
    solution = BASE_PAYMENT.replace("rate = 0.02", "rate = 0.02 if amount < 5000 else 0.015") \
                           .replace("    return amount - fee", "    return max(amount - fee, 0)")
    spec = {
        "expect": "resolved", "operation": "merge", "parents": 2, "tests": True,
        "setup": ["git checkout -q main", "git merge --no-edit pr-15"],
        "contains": {P: ["if amount < 5000", "max(amount - fee, 0)"]},
        "not_contains": {P: ["if amount < 1000"]},
        "solution": {P: solution},
    }
    verify_resolve(r, spec, [P])
    finish_resolve(r, "The PR branch carries an OLD copy of a fix (cherry-picked) that main has since "
                      "improved and deployed. Keep main's line (the PR's version is already in main's "
                      "history, by patch-id) plus the PR's own refund change.",
                   {"base": base, "main_fix": f, "main_improvement": e, "pr_stale_copy": stale,
                    "pr_commit": pr}, spec)


@scenario
def r05_previous_tested_resolution():
    r = Repo("r05_previous_tested_resolution")
    r.git("config", "rerere.enabled", "true")
    base = pr_base(r)
    r.git("branch", "release")
    r.edit(P, "rate = 0.02", "rate = 0.022")
    a = r.commit("Fee 2.2%")
    r.checkout("release")
    rel = r.pick(a)
    r.git("checkout", "-q", "-b", "pr-20", base)
    r.edit(P, "rate = 0.02", "rate = 0.018")
    pr = r.commit("Fee 1.8% (#20)")
    r.checkout("main")
    assert r.merge("pr-20") is None
    r.write(P, BASE_PAYMENT.replace("rate = 0.02", "rate = 0.019"))
    r.git("add", P)
    r.git("commit", "-q", "--no-edit")
    resolution = r.head()
    r.test(resolution, "uat")
    r.checkout("release")
    spec = {
        "expect": "resolved", "operation": "merge", "parents": 2, "tests": True,
        "setup": ["git checkout -q release", "git merge --no-edit pr-20"],
        "contains": {P: ["rate = 0.019"]},
        "solution": {P: BASE_PAYMENT.replace("rate = 0.02", "rate = 0.019")},
    }
    verify_resolve(r, spec, [P])
    finish_resolve(r, "The identical conflict was already resolved on main (rate = 0.019) and that "
                      "resolution passed uat. Reuse it (rerere is enabled and has it recorded).",
                   {"base": base, "release_copy": rel, "pr_commit": pr, "main_resolution": resolution},
                   spec, checkout="release")


@scenario
def r06_both_sides_logic_unverified():
    r = Repo("r06_both_sides_logic_unverified")
    base = pr_base(r)
    r.test(base, "dev")
    r.git("checkout", "-q", "-b", "pr-31")
    r.edit(P, "rate = 0.02", "rate = 0.02 if amount < 1000 else 0.015")
    pr = r.commit("Tiered fee (#31)")
    r.checkout("main")
    r.edit(P, "rate = 0.02", "rate = 0.025")
    m = r.commit("Raise fee to 2.5% (#30)")
    spec = {
        "expect": "refused", "operation": "merge", "unsafe_files": [P],
        "setup": ["git checkout -q main", "git merge --no-edit pr-31"],
    }
    verify_resolve(r, spec, [P])
    finish_resolve(r, "Both sides changed the fee logic differently and neither was tested. Picking one "
                      "is a business decision: the tool must NOT auto-resolve; it must stop and explain.",
                   {"base": base, "pr_commit": pr, "main_commit": m}, spec)


@scenario
def r07_mixed_safe_and_unsafe_files():
    r = Repo("r07_mixed_safe_and_unsafe_files")
    base = pr_base(r, {"CHANGELOG.md": "# Changelog\n\n## Unreleased\n\n- Initial release\n"})
    r.test(base, "dev")
    r.git("checkout", "-q", "-b", "pr-41")
    r.edit("CHANGELOG.md", "## Unreleased\n\n", "## Unreleased\n\n- Tiered fee (#41)\n")
    r.edit(P, "rate = 0.02", "rate = 0.02 if amount < 1000 else 0.015")
    pr = r.commit("Tiered fee (#41)")
    r.checkout("main")
    r.edit("CHANGELOG.md", "## Unreleased\n\n", "## Unreleased\n\n- Fee 2.5% (#40)\n")
    r.edit(P, "rate = 0.02", "rate = 0.025")
    m = r.commit("Fee 2.5% (#40)")
    spec = {
        "expect": "refused", "operation": "merge", "unsafe_files": [P],
        "setup": ["git checkout -q main", "git merge --no-edit pr-41"],
    }
    verify_resolve(r, spec, ["CHANGELOG.md", P])
    finish_resolve(r, "CHANGELOG.md is safe to fix, app/payment.py is not. The tool must not commit a "
                      "half-resolved merge: app/payment.py must still be in conflict afterwards.",
                   {"base": base, "pr_commit": pr, "main_commit": m}, spec)


@scenario
def r08_code_moved_needs_reapply():
    r = Repo("r08_code_moved_needs_reapply")
    base = pr_base(r)
    r.git("checkout", "-q", "-b", "pr-30")
    r.edit(P, "    return amount - fee", "    return max(amount - fee, 0)")
    r.write("tests/test_refund_clamp.py", testfile(
        "try:\n    from app.refunds import refund\nexcept ImportError:\n    from app.payment import refund",
        "self.assertEqual(refund(1, 5), 0)"))
    pr = r.commit("HOTFIX: no negative refunds (#30)", author=OPS)
    r.deploy(pr)
    r.checkout("main")
    moved = BASE_PAYMENT.replace("\n\n\ndef refund(amount, fee):\n    return amount - fee\n", "\n")
    r.write(P, moved)
    r.write("app/refunds.py", "def refund(amount, fee):\n    return amount - fee\n")
    r.write("tests/test_refunds_module.py", testfile("from app.refunds import refund",
                                                     "self.assertEqual(refund(10, 3), 7)"))
    m = r.commit("Move refund() to app/refunds.py")
    r.test(m, "dev")
    spec = {
        "expect": "either", "operation": "merge", "parents": 2, "tests": True, "unsafe_files": [P],
        "setup": ["git checkout -q main", "git merge --no-edit pr-30"],
        "contains": {"app/refunds.py": ["max(amount - fee, 0)"]},
        "not_contains": {P: ["def refund"]},
        "solution": {P: moved, "app/refunds.py": "def refund(amount, fee):\n    return max(amount - fee, 0)\n"},
    }
    verify_resolve(r, spec, [P], naive_sides_fail=True)
    finish_resolve(r, "main moved refund() to app/refunds.py; the PR hot-fixed it in the old place. "
                      "Taking either side loses something (tests fail). Correct fix: keep the move AND "
                      "re-apply the hotfix in app/refunds.py — or refuse and explain.",
                   {"base": base, "pr_hotfix": pr, "main_move": m}, spec)


@scenario
def r09_backport_compatible_intents():
    r = Repo("r09_backport_compatible_intents")
    base = pr_base(r)
    r.git("branch", "release")
    r.edit(P, "    return round(amount * rate, 2)", "    return round(amount * rate, 2) if amount > 0 else 0")
    r.write("tests/test_guard.py", testfile("from app.payment import calculate_fee",
                                            "self.assertEqual(calculate_fee(-5), 0)"))
    fix = r.commit("Negative amounts have no fee")
    r.test(fix, "uat")
    r.checkout("release")
    r.edit(P, "rate = 0.02", "rate = 0.025")
    r.write("tests/test_rate.py", testfile("from app.payment import calculate_fee",
                                           "self.assertEqual(calculate_fee(100), 2.5)"))
    hotfix = r.commit("HOTFIX: fee 2.5%", author=OPS)
    r.test(hotfix, "production")
    r.deploy(hotfix)
    spec = {
        "expect": "either", "operation": "cherry-pick", "parents": 1, "tests": True, "unsafe_files": [P],
        "setup": ["git checkout -q release", f"git cherry-pick -x {fix}"],
        "contains": {P: ["rate = 0.025", "if amount > 0 else 0"]},
        "trailer": f"(cherry picked from commit {fix})",
        "solution": {P: BASE_PAYMENT.replace("rate = 0.02", "rate = 0.025").replace(
            "    return round(amount * rate, 2)", "    return round(amount * rate, 2) if amount > 0 else 0")},
    }
    verify_resolve(r, spec, [P], naive_sides_fail=True)
    finish_resolve(r, "Backporting a fix to release conflicts with a production hotfix on the next line. "
                      "The two intents are compatible; only a COMBINATION passes both sides' tests. The "
                      "result must keep the -x trailer and be a 1-parent commit.",
                   {"base": base, "main_fix": fix, "release_hotfix": hotfix}, spec, checkout="release")


@scenario
def r10_rebase_pr_onto_main():
    r = Repo("r10_rebase_pr_onto_main")
    base = pr_base(r)
    r.git("checkout", "-q", "-b", "pr-7")
    r.edit(P, "rate = 0.02", "rate = 0.02 if amount < 1000 else 0.015")
    r.write("tests/test_tier.py", testfile("from app.payment import calculate_fee",
                                           "self.assertEqual(calculate_fee(2000), 30.0)"))
    pr = r.commit("Tiered fee (#7)")
    r.checkout("main")
    r.edit(P, "rate = 0.02", "rate  =  0.02")
    fmt = r.commit("Run code formatter")
    spec = {
        "expect": "resolved", "operation": "rebase", "parents": 1, "tests": True, "branch": "pr-7",
        "setup": ["git checkout -q pr-7", "git rebase main"],
        "contains": {P: ["if amount < 1000 else 0.015"]},
        "solution": {P: BASE_PAYMENT.replace("rate = 0.02", "rate = 0.02 if amount < 1000 else 0.015")},
    }
    verify_resolve(r, spec, [P])
    finish_resolve(r, "'Rebase and merge' style PR: rebasing pr-7 onto main conflicts with a formatter "
                      "commit. The tool must resolve AND finish the rebase (HEAD back on pr-7, on top of main). "
                      "Remember: during a rebase 'ours' is main.",
                   {"base": base, "pr_commit": pr, "formatter": fmt}, spec)


def main(names):
    ROOT.mkdir(exist_ok=True)
    unknown = set(names) - set(SCENARIOS)
    if unknown:
        sys.exit(f"unknown scenario(s): {', '.join(sorted(unknown))}")
    failed = 0
    for name, fn in SCENARIOS.items():
        if names and name not in names:
            continue
        try:
            fn()
            print(f"ok    {name}")
        except Exception as e:  # report every broken fixture, not just the first
            failed += 1
            print(f"FAIL  {name}: {e}")
    index = {n: json.loads((ROOT / n / "expected.json").read_text())["description"]
             for n in SCENARIOS if (ROOT / n / "expected.json").exists()}
    (ROOT / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main(sys.argv[1:])
