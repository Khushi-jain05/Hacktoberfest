#!/usr/bin/env python3
"""Run the fixture test cases against the provenance tool.

  python3 run_tests.py --tool "./tool-adapter.sh"            # all cases
  python3 run_tests.py --tool "./tool-adapter.sh" s02 s10     # only scenarios starting with these
  python3 run_tests.py --tool ... --required ci_required.txt  # CI: fail only on required cases
  python3 run_tests.py --self-test                            # check the harness itself

The tool interface (subcommands + JSON shapes) is described in CONTRACT.md.
Each case runs in a fresh temporary copy of its fixture, so nothing in repos/ changes.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "harness"))
from cases import CASES  # noqa: E402

FIXTURES = HERE / "repos"
STATUSES = {"production", "uat", "dev", "unverified", "unknown", "failed", "rolled-back", "flaky"}
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_EDITOR": "true",
    "GIT_AUTHOR_NAME": "Test Runner", "GIT_AUTHOR_EMAIL": "runner@example.com",
    "GIT_COMMITTER_NAME": "Test Runner", "GIT_COMMITTER_EMAIL": "runner@example.com",
}
STATE_FILES = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "REBASE_HEAD",
               "sequencer", "rebase-merge", "rebase-apply")


def nfc(s):
    return unicodedata.normalize("NFC", s) if isinstance(s, str) else s


def same(a, b):
    """SHA equality that accepts abbreviated SHAs (>= 7 chars)."""
    return (isinstance(a, str) and isinstance(b, str) and min(len(a), len(b)) >= 7
            and (a.startswith(b) or b.startswith(a)))


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout


def resolve(obj, commits):
    if isinstance(obj, str):
        return re.sub(r"@([a-z0-9_]+)", lambda m: commits.get(m.group(1), m.group(0)), obj)
    if isinstance(obj, list):
        return [resolve(x, commits) for x in obj]
    if isinstance(obj, dict):
        return {k: resolve(v, commits) for k, v in obj.items()}
    return obj


def load_cases(only):
    out = []
    for scenario, spec in CASES.items():
        if only and not any(scenario.startswith(o) for o in only):
            continue
        expected = json.loads((FIXTURES / scenario / "expected.json").read_text())
        commits = {k: v for k, v in expected["commits"].items() if isinstance(v, str)}
        cases = spec(expected["expected"]) if callable(spec) else spec
        for c in cases:
            out.append({"id": f"{scenario}:{c['name']}", "scenario": scenario,
                        **resolve(c, commits)})
    return out


# --- running ------------------------------------------------------------------

def prepare(case, tmp):
    """Copy the fixture, point clones at the copy, run setup. Returns the work dir."""
    src = FIXTURES / case["scenario"]
    dst = Path(tmp) / case["scenario"]
    shutil.copytree(src, dst, symlinks=True)
    for sub in dst.iterdir():
        url = git(sub, "config", "--get", "remote.origin.url").strip() if sub.is_dir() else ""
        if str(src) in url:
            git(sub, "remote", "set-url", "origin", url.replace(str(src), str(dst)))
    for cmd in case["setup"]:
        subprocess.run(cmd, shell=True, cwd=dst / case["setup_dir"], capture_output=True,
                       env={**os.environ, **GIT_ENV})
    return dst / case["dir"]


def snapshot(work):
    gitdir = Path(work, git(work, "rev-parse", "--git-dir").strip())
    return "\n".join([
        git(work, "for-each-ref", "--format=%(refname) %(objectname)"),
        git(work, "rev-parse", "HEAD"),
        git(work, "status", "--porcelain=v2", "-z"),
        " ".join(f for f in STATE_FILES if (gitdir / f).exists()),
    ])


def call_tool(tool, work, args, env, timeout):
    argv = [tool] if os.path.isfile(tool) else shlex.split(tool)  # paths may contain spaces
    try:
        p = subprocess.run(argv + args, cwd=work, capture_output=True, text=True,
                           env={**os.environ, **GIT_ENV, **env}, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"`{' '.join(args)}` timed out after {timeout}s"
    except OSError as e:
        return None, f"could not start tool: {e}"
    if p.returncode != 0:
        return None, f"`{' '.join(args)}` exited {p.returncode}: {p.stderr.strip()[-300:]}"
    try:
        return json.loads(p.stdout), None
    except json.JSONDecodeError:
        return None, f"`{' '.join(args)}` did not print JSON: {p.stdout.strip()[:200]!r}"


def queries(checks):
    q = {("conflicts",)}
    for c in checks:
        if c["type"] == "status" or (c["type"] == "mentions" and c["query"] == "status"):
            q.add(("status", c["commit"]))
        if c["type"] in ("match", "no_match") or (c["type"] == "mentions" and c["query"] == "matches"):
            q.add(("matches", c["commit"]))
    return sorted(q)


# --- evaluation -----------------------------------------------------------------

def entries(out, path=None, hunk=None):
    es = (out or {}).get("conflicts") or []
    return [e for e in es if isinstance(e, dict)
            and (path is None or nfc(e.get("file")) == nfc(path))
            and (hunk is None or e.get("hunk") == hunk)]


def evaluate(check, answers):
    """Returns None when the check passes, else a failure message."""
    t = check["type"]
    conf = answers.get(("conflicts",))
    if t == "conflict_files":
        got = sorted({nfc(e.get("file")) for e in entries(conf)})
        want = sorted(nfc(f) for f in check["files"])
        return None if got == want else f"conflicted files: want {want}, got {got}"
    if t == "hunk_count":
        n = len(entries(conf, check["file"]))
        return None if n == check["count"] else f"{check['file']}: want {check['count']} hunks, got {n}"
    if t == "operation":
        got = (conf or {}).get("operation")
        return None if got in check["operation"] else f"operation: want {check['operation']}, got {got}"
    if t == "side":
        es = entries(conf, check["file"], check["hunk"])
        where = f"{check['file']}" + (f" hunk {check['hunk']}" if check["hunk"] else "")
        if not es:
            return f"{where}: no conflict entry reported"
        allowed = check["origin"] if isinstance(check["origin"], list) else [check["origin"]]
        for e in es:
            s = e.get(check["side"]) or {}
            origin, st = s.get("origin_commit"), s.get("status")
            if check["origin"] and not any(same(origin, o) for o in allowed):
                return f"{where} {check['side']}: origin want {[o[:10] for o in allowed]}, got {origin}"
            if check["origin_not"] and same(origin, check["origin_not"]):
                return f"{where} {check['side']}: origin must NOT be {check['origin_not'][:10]}"
            if check["status"] and st not in check["status"]:
                return f"{where} {check['side']}: status want {check['status']}, got {st!r}"
        return None
    if t == "field":
        for e in entries(conf, check["file"]):
            v = e.get(check["key"])
            if v == check["value"] or same(v, check["value"]):
                return None
        return f"{check['file']}: want {check['key']}={check['value']!r}"
    if t == "status":
        got = (answers.get(("status", check["commit"])) or {}).get("status")
        return None if got in check["status"] else \
            f"status of {check['commit'][:10]}: want {check['status']}, got {got!r}"
    if t in ("match", "no_match"):
        ms = (answers.get(("matches", check["commit"])) or {}).get("matches") or []
        hits = [m for m in ms if isinstance(m, dict) and same(m.get("commit"), check["original"])
                and m.get("verified", True) is not False]
        label = f"{check['commit'][:10]} -> {check['original'][:10]}"
        if t == "no_match":
            return f"must NOT report a verified match {label}" if hits else None
        if not hits:
            return f"missing match {label}"
        if check["exact"] is not None and not any(m.get("exact") == check["exact"] for m in hits):
            return f"match {label}: want exact={check['exact']}"
        return None
    if t == "mentions":
        out = answers.get((check["query"], check["commit"]))
        return None if check["text"] in json.dumps(out) else \
            f"`{check['query']} {check['commit'][:10]}` output should mention {check['text'][:10]!r}"
    return f"unknown check type {t}"


def unmerged(work):
    raw = git(work, "diff", "--name-only", "-z", "--diff-filter=U")
    return sorted(nfc(f) for f in raw.split("\0") if f)


def in_progress(work):
    # REBASE_HEAD can outlive a finished rebase, so it doesn't count here.
    gitdir = Path(work, git(work, "rev-parse", "--git-dir").strip())
    return [f for f in STATE_FILES if f != "REBASE_HEAD" and (gitdir / f).exists()]


def show(work, path):
    return subprocess.run(["git", "show", f"HEAD:{path}"], cwd=work, capture_output=True).stdout.decode(
        "utf-8", "replace")


def check_resolved(work, spec, head0, notes0, out, timeout):
    fails = []
    if unmerged(work):
        fails.append(f"files still conflicted after 'resolved': {unmerged(work)}")
    if in_progress(work):
        fails.append(f"operation not finished (still present: {in_progress(work)})")
    head = git(work, "rev-parse", "HEAD").strip()
    if head == head0:
        fails.append("no new commit was created")
    parents = len(git(work, "rev-list", "--parents", "-n1", "HEAD").split()) - 1
    if parents != spec["parents"]:
        fails.append(f"result commit has {parents} parents, want {spec['parents']}")
    if spec.get("branch") and git(work, "symbolic-ref", "-q", "HEAD").strip() != f"refs/heads/{spec['branch']}":
        fails.append(f"HEAD should be back on branch {spec['branch']}")
    if git(work, "status", "--porcelain", "--untracked-files=no").strip():
        fails.append("working tree has uncommitted changes after resolving")
    fsck = subprocess.run(["git", "fsck", "--no-progress", "--no-dangling"], cwd=work, capture_output=True)
    if fsck.returncode:
        fails.append("git fsck failed on the result")
    for path in git(work, "diff", "--name-only", "HEAD^1", "HEAD").splitlines():
        if re.search(r"^(<{7}|>{7})( |$)", show(work, path), re.M):
            fails.append(f"conflict markers committed in {path}")
    for path, needles in spec.get("contains", {}).items():
        text = show(work, path)
        fails += [f"{path} should contain {n!r}" for n in needles if n not in text]
    for path, needles in spec.get("not_contains", {}).items():
        text = show(work, path)
        fails += [f"{path} must not contain {n!r}" for n in needles if n in text]
    for path, needles in spec.get("exactly_once", {}).items():
        text = show(work, path)
        fails += [f"{path}: {n!r} appears {text.count(n)} times, want 1" for n in needles if text.count(n) != 1]
    if spec.get("trailer") and spec["trailer"] not in git(work, "log", "-1", "--format=%B"):
        fails.append(f"commit message lost the trailer {spec['trailer']!r}")
    if spec.get("tests"):
        t = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-q"],
                           cwd=work, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        if t.returncode:
            fails.append("project unit tests FAIL on the resolved result: "
                         + (t.stderr.strip().splitlines() or ["?"])[-1])
    notes = set(git(work, "notes", "--ref=tests", "list").splitlines())
    if not notes0 <= notes:
        fails.append("existing test records (git notes) were removed")
    if not (out or {}).get("decisions"):
        fails.append("JSON output has no 'decisions' explaining what was chosen per conflict")
    return fails


def run_resolve(case, tool, env, timeout):
    spec = case["checks"][0]
    with tempfile.TemporaryDirectory() as tmp:
        work = prepare(case, tmp)
        if not unmerged(work):
            return "SETUP-ERROR", ["fixture did not reach a conflict"]
        head0 = git(work, "rev-parse", "HEAD").strip()
        notes0 = set(git(work, "notes", "--ref=tests", "list").splitlines())
        out, err = call_tool(tool, work, ["resolve"], env, timeout)
        if err:
            return "FAIL", [err]
        resolved = bool((out or {}).get("resolved"))
        fails = []
        if spec["expect"] == "resolved" and not resolved:
            fails.append(f"should auto-resolve this conflict but did not (reason: {(out or {}).get('reason')!r})")
        elif spec["expect"] == "refused" and resolved:
            fails.append("auto-resolved a conflict that must be left to a human")
        elif resolved:
            fails += check_resolved(work, spec, head0, notes0, out, timeout)
        else:
            if git(work, "rev-parse", "HEAD").strip() != head0:
                fails.append("refused, but still created a commit")
            missing = [f for f in spec.get("unsafe_files", []) if nfc(f) not in unmerged(work)]
            if missing:
                fails.append(f"refused, but these files are no longer marked conflicted: {missing}")
            if not in_progress(work):
                fails.append("refused, but the merge/cherry-pick/rebase was aborted or finished")
            if not (out or {}).get("reason"):
                fails.append("refused without a 'reason'")
        return ("FAIL" if fails else "PASS"), fails


def run_case(case, tool, env, timeout):
    """Returns (status, [messages]) where status is PASS, FAIL or SETUP-ERROR."""
    timeout = case.get("timeout") or timeout
    if case["checks"] and case["checks"][0]["type"] == "resolve":
        return run_resolve(case, tool, env, timeout)
    with tempfile.TemporaryDirectory() as tmp:
        work = prepare(case, tmp)
        want = [c for c in case["checks"] if c["type"] == "conflict_files"]
        if want and want[0]["files"]:
            raw = git(work, "diff", "--name-only", "-z", "--diff-filter=U")
            got = sorted(nfc(f) for f in raw.split("\0") if f)
            if got != sorted(nfc(f) for f in want[0]["files"]):
                return "SETUP-ERROR", [f"fixture not in expected conflict state: {got}"]
        before = snapshot(work)
        answers, fails = {}, []
        for q in queries(case["checks"]):
            out, err = call_tool(tool, work, list(q), env, timeout)
            if err:
                fails.append(err)
            answers[q] = out
            for e in entries(out) if q == ("conflicts",) else [out or {}]:
                for s in [e.get("status")] + [(e.get(k) or {}).get("status") for k in ("ours", "theirs")]:
                    if s is not None and s not in STATUSES:
                        fails.append(f"invalid status value {s!r} (allowed: {sorted(STATUSES)})")
        again, _ = call_tool(tool, work, ["conflicts"], env, timeout)
        if again != answers.get(("conflicts",)):
            fails.append("`conflicts` output differs between two runs (not deterministic)")
        if snapshot(work) != before:
            fails.append("tool modified the repository while only reporting")
        fails += [m for m in (evaluate(c, answers) for c in case["checks"]) if m]
        return ("FAIL" if fails else "PASS"), list(dict.fromkeys(fails))


# --- self-test ------------------------------------------------------------------

def oracle_answers(checks):
    """The perfect tool output for a case, built from its checks."""
    conf = {"operation": None, "conflicts": []}
    counts, statuses, matches = {}, {}, {}
    for c in checks:
        if c["type"] == "hunk_count":
            counts[c["file"]] = c["count"]
        if c["type"] == "operation":
            conf["operation"] = c["operation"][0]
    for c in checks:
        if c["type"] == "conflict_files":
            for f in c["files"]:
                for h in range(1, counts.get(f, 1) + 1):
                    conf["conflicts"].append({"file": f, "hunk": h, "kind": "content",
                                              "ours": {}, "theirs": {}})
    for c in checks:
        if c["type"] == "side":
            for e in entries(conf, c["file"], c["hunk"]):
                origin = c["origin"][0] if isinstance(c["origin"], list) else c["origin"]
                e[c["side"]] = {"origin_commit": origin,
                                "status": c["status"][0] if c["status"] else "unknown"}
        elif c["type"] == "field":
            for e in entries(conf, c["file"]):
                e[c["key"]] = c["value"]
        elif c["type"] == "status":
            statuses.setdefault(c["commit"], {"commit": c["commit"], "evidence": []})["status"] = c["status"][0]
        elif c["type"] == "match":
            matches.setdefault(c["commit"], {"commit": c["commit"], "matches": []})["matches"].append(
                {"commit": c["original"], "method": "patch-id", "exact": bool(c["exact"]), "verified": True})
    for c in checks:
        if c["type"] == "mentions" and c["query"] == "status":
            statuses.setdefault(c["commit"], {"commit": c["commit"], "status": "unknown",
                                              "evidence": []})["evidence"].append(c["text"])
        if c["type"] == "mentions" and c["query"] == "matches":
            matches.setdefault(c["commit"], {"commit": c["commit"], "matches": []})["matches"].append(
                {"commit": c["text"], "method": "trailer", "exact": False, "verified": False})
    answers = {"conflicts": conf}
    for c in checks:
        if c["type"] == "resolve":
            answers["resolve"] = (
                {"action": "apply", "files": c["solution"], "operation": c["operation"]}
                if c["expect"] != "refused" and c.get("solution")
                else {"resolved": False, "reason": "oracle: needs a human"})
    answers.update({f"status {k}": v for k, v in statuses.items()})
    answers.update({f"matches {k}": v for k, v in matches.items()})
    return answers


def self_test(cases, timeout):
    """A perfect fake tool must pass every case; an empty one must fail every case."""
    oracle = f"{shlex.quote(sys.executable)} {shlex.quote(str(HERE / 'harness' / 'oracle_tool.py'))}"
    empty = f"{shlex.quote(sys.executable)} -c \"print('{{}}')\""
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        for case in cases:
            ans = Path(tmp, case["id"].replace(":", "_") + ".json")
            ans.write_text(json.dumps(oracle_answers(case["checks"])))
            ok, msgs = run_case(case, oracle, {"ORACLE_ANSWERS": str(ans)}, timeout)
            empty_result, _ = run_case(case, empty, {}, timeout)
            good = ok == "PASS" and empty_result == "FAIL"
            bad += not good
            print(f"{'ok  ' if good else 'BAD '} {case['id']:<55} oracle={ok} empty-tool={empty_result}"
                  + ("" if good else f"  {msgs[:2]}"))
    print(f"\nself-test: {len(cases) - bad}/{len(cases)} cases behave correctly")
    return 1 if bad else 0


# --- main -----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("only", nargs="*", help="scenario name prefixes, e.g. s02 s10")
    ap.add_argument("--tool", help="command that runs the tool (see CONTRACT.md)")
    ap.add_argument("--required", help="file listing scenarios/case ids that must pass")
    ap.add_argument("--json-out", help="write per-case results here")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if not (FIXTURES / "index.json").exists():
        subprocess.run([sys.executable, str(HERE / "generate_fixtures.py")], check=True)
    cases = load_cases(a.only)
    if a.self_test:
        sys.exit(self_test(cases, a.timeout))
    if not a.tool:
        ap.error("--tool is required (or use --self-test)")

    required = set()
    if a.required:
        required = {l.split("#")[0].strip() for l in Path(a.required).read_text().splitlines()} - {""}

    results = []
    for case in cases:
        st, msgs = run_case(case, a.tool, {}, a.timeout)
        req = any(case["id"] == r or case["scenario"] == r for r in required)
        results.append({"id": case["id"], "status": st, "required": req, "failures": msgs})
        print(f"{st:<11} {'*' if req else ' '} {case['id']}")
        for m in msgs[:5]:
            print(f"              - {m}")

    passed = sum(r["status"] == "PASS" for r in results)
    print(f"\n{passed}/{len(results)} cases pass   (* = required in CI)")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(results, indent=2) + "\n")
    gate = [r for r in results if r["required"]] if a.required else results
    sys.exit(0 if all(r["status"] == "PASS" for r in gate) else 1)


if __name__ == "__main__":
    main()
