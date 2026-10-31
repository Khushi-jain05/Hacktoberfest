# Test fixtures — Problem 01 (conflict resolution with testing provenance)

Shared test repos for the whole team. Run your tool against them and compare
its output with `expected.json`.

```bash
python3 generate_fixtures.py                              # all 71 scenarios (~30 s)
python3 generate_fixtures.py s02_prod_hotfix_vs_dev_fix   # just one
```

Python 3.8+ and git 2.28+, no other dependencies. Commit IDs are identical on every
machine and every run, so `expected.json` can name exact SHAs. The script checks
each scenario itself (the conflict really happens, the patch-ids really match or
differ, the shallow clone really is shallow, …) and exits non-zero if a fixture
is wrong.

`repos/` is ignored by git (nested repos can't be committed). Commit only the
script and re-generate in CI. Real-repo cases are in
[SPIDER_TEST_CASES.md](SPIDER_TEST_CASES.md).

## What each scenario contains

```
repos/<scenario>/
  repo/               git repo, branches dev / uat / prod (some also feature, release)
  test_records.json   simulated CI + deploy log:
                      {commit, env, kind: test|deploy, result, source, at}
  expected.json       reproduce commands, named commits, what a correct tool must say
  clone/ shallow/ clone_qa/ clone_ops/ origin.git    (only in s06, s22, s38)
```

Records are also stored in git notes (`git notes --ref=tests show <sha>`).
`at` is a timestamp; order matters for rollbacks (s25) and flaky tests (s26).

## Scenarios

### Basics (s01–s15)

| # | Scenario | Breaks a tool that… |
|---|----------|---------------------|
| 01 | clean_backport | can't link the same fix across 3 branches (baseline) |
| 02 | prod_hotfix_vs_dev_fix | **core demo**: can't say "ours is production-tested, theirs only dev-tested" |
| 03 | modified_backport_with_trailer | relies on patch-id only (fails when the backport was edited) |
| 04 | backport_no_trailer_reworded | has no fuzzy matching, or reports a fuzzy match as certain |
| 05 | whitespace_only_difference | hashes raw diffs instead of using `git patch-id` |
| 06 | clone_without_metadata | says "untested" when it should say "unknown" (notes don't clone) |
| 07 | both_sides_untested | treats "on the prod branch" as "production-tested" |
| 08 | rename_then_conflict | loses history across a file rename |
| 09 | modify_delete | expects `<<<<<<<` markers, so it crashes on modify/delete |
| 10 | multi_hunk_mixed_provenance | gives one status per branch instead of per hunk |
| 11 | failed_test_reverted_then_reapplied | mixes up test results between v1 and v2 of a fix |
| 12 | squashed_backport | assumes one backport maps to exactly one original |
| 13 | test_was_on_older_commit | marks a side as tested because *some* commit on that branch passed |
| 14 | criss_cross_merge | assumes there is exactly one merge base |
| 15 | recurring_conflict_rerere | ignores a resolution that was already made and tested |

### Advanced (s16–s40)

| # | Scenario | Breaks a tool that… |
|---|----------|---------------------|
| 16 | chained_backport | doesn't follow trailers through dev → uat → prod, or reads only one trailer |
| 17 | chain_modified_midway | can't tell which hop changed the code, so it credits the wrong test |
| 18 | resolution_introduces_new_code | marks hand-written resolution code as "tested" because its inputs were |
| 19 | resolution_mixes_hunks | can't carry provenance per line *through* a resolution, or misses the dropped change |
| 20 | same_patch_different_base | trusts an identical patch-id as "tested" when a prerequisite is missing (prod is actually broken) |
| 21 | rebased_after_test | loses test records when a SHA is rewritten by rebase |
| 22 | notes_diverged_between_clones | loses or force-overwrites provenance when two clones both added records |
| 23 | trailer_points_to_missing_commit | crashes when a referenced SHA no longer exists |
| 24 | lying_trailer | trusts a commit message's claim without checking the code |
| 25 | deploy_rollback | thinks "tip of prod branch" = "running in production" |
| 26 | flaky_test_history | reports only the latest test result |
| 27 | test_on_descendant | calls a line untested when CI ran on a later commit containing the same line |
| 28 | evil_merge | can't handle a line that originates *inside* a merge commit |
| 29 | github_squash_merge | can't map a GitHub squash commit `(#42)` back to the tested PR |
| 30 | partial_backport | assumes a backport carries the whole original commit |
| 31 | conflict_mid_sequence | breaks on a multi-commit cherry-pick (`.git/sequencer`) |
| 32 | rebase_inverts_ours_theirs | labels ours/theirs backwards during a rebase |
| 33 | revert_conflict | doesn't understand that "theirs" in a revert means "undo" |
| 34 | diff3_and_custom_marker_size | parses markers by hand and breaks on diff3 style or 12-char markers |
| 35 | literal_markers_in_content | greps for `<<<<<<<` and reports false conflicts |
| 36 | rename_rename_and_add_add | supports only content conflicts |
| 37 | unicode_paths_crlf_and_modes | mangles paths with spaces/ü, rewrites CRLF, or drops the executable bit |
| 38 | shallow_clone | blames the shallow boundary commit as the origin |
| 39 | same_fix_two_paths | reports an already-deployed fix as a new unverified change |
| 40 | large_history | is too slow (175 commits, 25 hunks, all with different origins) |

### Expert (s41–s61): git internals, hostile data, scale

| # | Scenario | Breaks a tool that… |
|---|----------|---------------------|
| 41 | both_sides_variants_of_same_fix | can't see that both sides are versions of the SAME fix |
| 42 | code_moved_between_files | silently drops a prod hotfix to code that dev moved to another file |
| 43 | formatter_commit_hides_origin | blames a whitespace-only formatter commit (ignores `.git-blame-ignore-revs`) |
| 44 | tested_trailer_without_ci | trusts a `Tested-in: production` line someone typed in a commit message |
| 45 | rollback_records_out_of_order | orders records by position instead of by timestamp |
| 46 | symlink_conflict | crashes on, or follows, a conflicting symlink |
| 47 | submodule_conflict | can't handle a gitlink (submodule pointer) conflict |
| 48 | mixed_origins_in_one_hunk | reports one origin per hunk when lines inside it differ in trust |
| 49 | reverted_fix_reintroduced_by_merge | stays silent when a clean merge brings back a fix that failed in prod |
| 50 | deep_history | is slow: the line's origin is 5,000 commits deep (60 s limit) |
| 51 | non_utf8_and_no_final_newline | assumes UTF-8, or adds/drops the final newline |
| 52 | deletion_vs_modification | returns "unknown" when one side deleted the lines (nothing to blame) |
| 53 | original_only_in_remote_ref | searches only local branches |
| 54 | git_am_backport | only understands cherry-pick (no trailer, no CHERRY_PICK_HEAD) |
| 55 | conflict_in_linked_worktree | hard-codes `.git/` paths |
| 56 | squash_merge_conflict | needs MERGE_HEAD (a squash merge has none) |
| 57 | partially_resolved | re-reports files the user already resolved |
| 58 | ambiguous_short_sha | trusts a 5-char SHA in a message when two commits share it |
| 59 | noisy_provenance_records | crashes on malformed notes, or counts a FAILED deploy |
| 60 | cherry_picked_merge_commit | can't match `cherry-pick -m 1` of a merge (empty `git show` diff) |
| 61 | empty_cherry_pick | treats "pick is empty, already applied" as an error |

### Resolve (r01–r10): merge the PR → the tool must FIX the conflict (or refuse)

Each repo contains real unit tests. A resolution passes only if the operation is
finished with a normal commit **and** the tests pass. See `resolve` in CONTRACT.md.

| # | Scenario | Tool must | Breaks a tool that… |
|---|----------|-----------|---------------------|
| r01 | changelog_both_added | resolve | can't keep both PRs' changelog lines |
| r02 | requirements_both_added | resolve | duplicates a dependency both sides added |
| r03 | whitespace_only_side | resolve | doesn't see one side is formatting only |
| r04 | pr_carries_stale_copy_of_fix | resolve | takes the PR's old copy of a fix main already improved |
| r05 | previous_tested_resolution | resolve | ignores the earlier tested resolution (rerere) |
| r06 | both_sides_logic_unverified | **refuse** | auto-picks a side in an untested business-logic conflict |
| r07 | mixed_safe_and_unsafe_files | **refuse** | commits a half-resolved merge |
| r08 | code_moved_needs_reapply | resolve correctly or refuse | takes one side (tests fail: hotfix or move is lost) |
| r09 | backport_compatible_intents | resolve correctly or refuse | takes one side (tests fail), or drops the `-x` trailer |
| r10 | rebase_pr_onto_main | resolve | resolves but doesn't finish the rebase, or mixes up ours/theirs |

## Coverage: every requirement in the problem statement → scenarios

| Problem-statement item | Scenarios |
|------------------------|-----------|
| Identify the origin of conflicting changes | 02, 08, 10, 14, 28, 32, 33, 36, 38, 40, 42, 43, 48, 50, 52, 54, 56 |
| Track whether each side has been tested | 02, 07, 11, 13, 26, 27, 44, 59 |
| Distinguish production-tested from unverified | 02, 07, 10, 18, 25, 39, 45, 48, 49 |
| Preserve provenance across branches | 01, 16, 17, 21, 29, 30, 39, 41, 53, 60, 61 |
| Help make informed resolution decisions | 09, 11, 19, 20, 25, 33, 42, 47, 49, 57 |
| Final commit stays standard Git | 18, 19 + universal checks below |
| *Consideration:* identical changes modified during resolution | 03, 04, 05, 12, 17, 24, 30, 41, 54, 58, 60 |
| *Consideration:* how provenance is stored and transferred | 06, 22, 23, 44, 45, 59 |
| *Consideration:* clone without extra metadata | 06, 15 (rr-cache), 22, 38 |
| *Consideration:* compatibility with standard Git | 18, 19, 31, 34, 35, 37, 46, 47, 51, 55, 56 |
| *Evaluation:* real Django / CPython backports | `real_backports.py` |
| *Evaluation:* commit messages referencing originals | 01, 03, 12, 16, 23, 24, 29 |
| *Evaluation:* simulated dev / UAT / prod repos | all s01–s40 |
| Hidden traps judges like to use | 20, 24, 25, 28, 32, 35, 42, 44, 49, 58 |

## Universal checks (run after EVERY scenario your tool resolves)

1. `git fsck --strict` is clean.
2. The result is an ordinary commit: 2 parents for a merge, 1 for a cherry-pick,
   rebase or revert. No custom object types, no history rewrite of existing commits.
3. `git log`, `git blame`, `git cherry` and `git cherry-pick` work in a
   **fresh clone without your tool installed**.
4. Any trailers your tool adds parse with `git interpret-trailers --parse`.
5. `-x` trailers that were already there are kept.
6. The tool never edits or deletes existing test records; it only adds new ones.
7. "Unknown", "untested" and "tested and failed" are always different outputs.
8. Running the tool twice gives the same output, and it changes nothing when only asked to report.
9. Non-zero exit code and a readable message on errors, never a traceback.

## Known gaps (not generated; check manually if time allows)

- Git LFS pointer files.
- Signed commits (`commit.gpgsign=true`): the final commit must still be signable.
- Windows paths / case-insensitive file systems (rename that only changes case).

## Real-world histories (the judges' scenario 1 and 2)

```bash
git clone --filter=blob:none https://github.com/django/django.git
python3 real_backports.py django stable/5.1.x --limit 50 > django_pairs.json

git clone --filter=blob:none https://github.com/python/cpython.git
python3 real_backports.py cpython 3.13 --limit 50 > cpython_pairs.json
```

Each pair is `{backport, original, same_patch_id}`. Pairs with
`same_patch_id: false` are backports that were edited by hand. Those are the
cases your matcher has to handle beyond `git patch-id`.

## Suggested CI use

In `.github/workflows/ci.yml`, generate the fixtures, run the tool on each
`reproduce` sequence, and assert on the fields in `expected.json`, plus the
universal checks above. If CI time is tight, run at least 02, 06, 09, 10, 18,
20, 25, 32, 35, 37, 42, 49 and 52. Each one tests a different kind of failure.
