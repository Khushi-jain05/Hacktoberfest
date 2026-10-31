# Tool contract for the fixture tests

`run_tests.py` talks to the tool through **one command** (usually an adapter
script, see `tool-adapter.example.sh`). It runs that command inside a git repo
with one of three subcommands. Each must print **JSON on stdout** and exit 0.

| Call | When | Prints |
|------|------|--------|
| `<tool> conflicts` | a merge / cherry-pick / rebase / revert may be in progress | conflict report |
| `<tool> status <sha>` | any time | provenance of one commit |
| `<tool> matches <sha>` | any time | commits that are the same change as `<sha>` |
| `<tool> resolve` | a merge / cherry-pick / rebase is stopped on conflicts | resolution result (see below) |

Reporting must **not** change the repo (refs, index, working tree, in-progress
operation). Output must be the same when run twice.

Provenance input: test/deploy records live in git notes under
`refs/notes/tests`. Each note line is a JSON record:
`{"env": "uat", "kind": "test|deploy", "result": "pass|fail|success", "at": "..."}`.
If your tool stores provenance differently, the adapter must convert.

## `conflicts`

```json
{
  "operation": "merge | squash-merge | cherry-pick | rebase | revert | am | null",
  "conflicts": [
    {
      "file": "app/payment.py",
      "hunk": 1,
      "kind": "content | modify/delete | add/add | rename/rename | binary | symlink | submodule",
      "ours":   {"origin_commit": "<sha or null>", "status": "<status>"},
      "theirs": {"origin_commit": "<sha or null>", "status": "<status>"},
      "previous_resolution": "<sha, optional — rerere / earlier identical resolution>",
      "moved_to": "<path, optional — the code on one side was moved to another file>"
    }
  ]
}
```

- If a side of one hunk comes from several commits, put the **weakest** one in
  `origin_commit` (the status follows it) and may list all of them in
  `origin_commits`.
- If a side *deleted* the lines, `origin_commit` is the commit that deleted them.
- Paths are reported exactly as in the repo (UTF-8, no octal escaping).

- One entry per conflict hunk; `hunk` counts from 1 within each file. Conflicts
  without hunks (modify/delete, add/add, rename) get one entry.
- `ours` = the HEAD side (index stage 2), `theirs` = the incoming side (stage 3),
  **even during a rebase**, where HEAD is the upstream.
- `origin_commit` = the commit that last changed those lines on that side
  (line-level blame), or `null` if it can't be determined (e.g. shallow clone).
- No conflict → `"conflicts": []`.

## `status <sha>`

```json
{"commit": "<sha>", "status": "<status>", "evidence": ["test:uat:pass@<sha>", "..."],
 "tests": {"passed": 4, "failed": 0}}
```

`tests` = the counts from the test record the status is based on. Test records
may carry `"passed"` / `"failed"` counts (the `tests/` suite writes them); copy
them through when present.

`evidence` is free text, but it must mention the commits and environments it relies on.

## `matches <sha>`

```json
{"commit": "<sha>", "matches": [
  {"commit": "<other sha>", "method": "patch-id | trailer | similarity | pr-ref",
   "exact": true, "verified": true}
]}
```

- `exact`: the code change is identical (same patch-id).
- `verified`: the code confirms the link. A trailer pointing to unrelated code,
  or to a commit that no longer exists, is `"verified": false`.

## `resolve`

Either **fix the conflict and finish the operation**, or **refuse and leave everything
as it is**. Never commit a half-resolved result.

Resolved (the tool committed; merge = 2 parents, cherry-pick/rebase = 1 parent,
rebase finished and HEAD back on the branch, `-x` trailers kept):

```json
{"resolved": true, "commit": "<new sha>",
 "decisions": [{"file": "CHANGELOG.md", "hunk": 1, "choice": "ours|theirs|both|combined|previous",
                "reason": "both sides only added changelog entries"}]}
```

Refused (no commit; files stay conflicted; operation still in progress):

```json
{"resolved": false, "reason": "both sides changed fee logic and neither is tested"}
```

`run_tests.py` grades a resolution by running the fixture's own unit tests
(`python -m unittest discover -s tests -t .`), so any correct combination passes,
not just one exact text.

## Status values (use exactly these)

| Value | Meaning |
|-------|---------|
| `production` | currently running in production (deployed and not rolled back) |
| `uat` | passed tests in UAT, not in production |
| `dev` | passed tests only in dev |
| `failed` | its latest relevant test failed |
| `flaky` | both passing and failing results for the same code |
| `rolled-back` | was deployed, then production rolled back past it |
| `unverified` | provenance records exist in this repo, but none cover this code |
| `unknown` | no provenance available (no notes ref locally, shallow history, …) |
