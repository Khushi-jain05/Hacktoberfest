#!/usr/bin/env python3
"""Extract real backport pairs (backport commit -> original commit) from a repo's history.

Ground truth for the judges' "real Django / CPython backport histories" scenario:
every pair is linked by a message reference, and `same_patch_id` tells you whether
the code survived unchanged (False = the backport was edited, the hard case).

Usage:
  python3 real_backports.py <repo-path> <branch> [--limit N] > pairs.json

Recognised message formats:
  Django:  "[5.1.x] Fixed #123 -- ...  Backport of <sha> from main."
  CPython: "(cherry picked from commit <sha>)"   (also plain `git cherry-pick -x`)
"""
import argparse
import json
import re
import subprocess

REFS = re.compile(r"(?:Backport of|cherry picked from commit)\s+([0-9a-f]{7,40})")


def git(repo, *args, stdin=None):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          input=stdin, check=True).stdout


def patch_id(repo, sha):
    out = git(repo, "patch-id", "--stable", stdin=git(repo, "show", sha))
    return out.split()[0] if out else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("branch")
    ap.add_argument("--limit", type=int, default=50)
    a = ap.parse_args()

    log = git(a.repo, "log", a.branch, "--no-merges", "-E",
              "--grep=Backport of|cherry picked from commit", f"-n{a.limit}",
              "--format=%H%x00%s%x00%B%x1e")
    pairs = []
    for entry in filter(str.strip, log.split("\x1e")):
        sha, subject, body = entry.strip().split("\x00", 2)
        for ref in REFS.findall(body):
            try:
                original = git(a.repo, "rev-parse", "--verify", ref + "^{commit}").strip()
            except subprocess.CalledProcessError:
                pairs.append({"backport": sha, "original": ref, "subject": subject,
                              "same_patch_id": None, "note": "original not in this clone"})
                continue
            pairs.append({"backport": sha, "original": original, "subject": subject,
                          "same_patch_id": patch_id(a.repo, sha) == patch_id(a.repo, original)})
    print(json.dumps(pairs, indent=2))


if __name__ == "__main__":
    main()
