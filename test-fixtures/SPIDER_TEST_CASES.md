# Real-repo test cases — spider-python

Test cases for the Problem 01 tool (conflict resolution with testing
provenance), taken from the real history of
[Koray-afk/spider-python](https://github.com/Koray-afk/spider-python):
114 commits, 18 merges, 11 branches, 4 people.

All cases below were reproduced on 2026-10-31. Every merge listed was replayed
and gave the stated conflicts. The synthetic cases in `repos/` (README.md) cover
backports and test provenance in depth. These real cases cover what synthetic
repos can't: real code, scale, binaries, messy identities and odd histories.

## Setup

```bash
./spider_setup.sh /tmp/spider            # plain clone
./spider_setup.sh /tmp/spider --notes    # + simulated test/deploy records for TC-S15
cd /tmp/spider
```

Each case starts from a clean tree: `git merge --abort; git reset --hard; git clean -fdx`.
"Real resolution" is what the team actually committed. A good tool should explain
the conflict well enough that a developer could arrive at that resolution.

## What this repo does NOT have (important)

- **No backports.** No two commits share a `git patch-id`, and no commit has a
  `(cherry picked from …)` line.
- **No test or deploy records.** There are no notes, CI or deploy logs.

So on this repo, the correct answers are "no equivalent commits found" and
"provenance unknown". Any match or "tested" status the tool reports here is a
**false positive**.

## Test cases

| ID | Setup | Conflicts | What it tests |
|----|-------|-----------|---------------|
| S01 | `git checkout --detach 3b795a84 && git merge de3e1de7` | 3 files, **9 hunks** (config.py 2, crawler_v2.py 6, stitcher_v1.py 1) | Main real demo: multi-file, multi-hunk, two different authors |
| S02 | `git checkout --detach 95583a8f && git merge a78c717c` | 1 file, 1 hunk (stitcher_v1.py) | Smallest real conflict, good for a live demo |
| S03 | `git checkout --detach 6fdfc809 && git merge e6ca5622` | **700 files** (699 generated `storage/**/page.html`) | Scale + generated files |
| S04 | `git checkout --detach c4dcf794 && git merge dcadd302` | 9 files: content + modify/delete + files missing on one side | Conflicts with no `<<<<<<<` markers |
| S05 | `git checkout --detach 05d746e2 && git merge 67e4c86c` | 4 files incl. a binary `.pyc` | Binary files; mixed per-file resolutions |
| S06 | `git checkout --detach fa3722f4 && git merge 26ddb965` | crawler.py + `pages/page-1.{html,png,txt}` | Binary PNG + deleted files |
| S07 | `git checkout --detach 8a820baa && git merge 739fbbe4` | README.md only | Doc-only conflict, large rewrite |
| S08 | `git checkout --detach main && git merge abhiman-prod` | 3 files (.gitignore, README.md, main.py), 4 hunks | Live branch named "prod" |
| S09 | `git checkout --detach main && git merge gauri` | **57 files**, mostly binary/assets/.pyc | Very stale branch (97 behind) |
| S10 | `git checkout --detach main && git merge mock` | *git refuses: "unrelated histories"* | No merge base at all |
| S11 | `git checkout prod && git merge main` | none — fast-forward | Promotion with no conflict |
| S12 | `git log --all --format='%an <%ae>' \| sort -u` | — | One person, several identities |
| S13 | run backport/equivalence detection on all branches | — | False-positive check |
| S14 | S01 on a plain clone | — | Missing provenance metadata |
| S15 | S01 / S02 after `spider_setup.sh --notes` | — | Provenance shown per side |
| S16 | merge commit `6d3fa038` ("Merge branch 'main' of github.com:…") | none | Pull merge of a branch into itself |

## Expected behaviour per case

**S01 — multi-hunk, multi-author** (real merge `142a2067`, "solved conflicts")
- Ours = `3b795a84` (Abhi Jain). Theirs = `de3e1de7` (Abhiman Singh, the PR #6 merge).
- The tool must list all **9 hunks** and give each one its own origin commit via
  line blame. A single answer per branch is wrong.
- Real resolution: all three files were **blended** (crawler_v2.py +404/−15
  compared with ours). The tool must still produce an ordinary 2-parent merge
  commit, and `git log --graph` and `git fsck` must work without the tool.

**S02 — small single hunk** (real merge `e6ca5622`)
- Ours `95583a8f` (Abhi Jain) vs theirs `a78c717c` (koray-afk), 1 hunk in stitcher_v1.py.
- Real resolution: a blend (+20/−4). Use this for the live demo; it fits on one screen.

**S03 — 700-file conflict** (real merge `8a820baa`, "Keep my changes")
- 699 of the 700 files are generated HTML under `storage/apps/likwid/stitched/`.
- Real resolution: **all 699 kept ours**, and stitcher_v1.py also kept ours.
- Pass: the tool finishes in reasonable time, groups generated files instead of
  showing 700 prompts, and lets you resolve them in bulk. Fail: it hangs, runs
  out of memory, or asks about each file one by one.

**S04 — conflicts without markers** (real merge `03313040`, "abhi change")
- `experiments/*.py` (3 files): modified on one side, deleted on the other.
  Real resolution: **deleted**.
- `api/routes.py`, `pipeline.py`, `pipeline_io.py`: missing on theirs. Real
  resolution: kept ours (routes.py) or a small blend.
- `main.py` and README.md: ordinary content conflicts, both blended.
- Pass: the tool reports these 9 files with the right conflict kind for each.
  Fail: it crashes or skips them because the deleted files have no `<<<<<<<` markers.

**S05 — binary + mixed resolutions** (real merge `ccf6e1a8`)
- `services/__pycache__/gemini_service.cpython-313.pyc` is a **binary** conflict.
  Real resolution: deleted. The tool must not try to show text hunks for it.
- Per file, the real resolution took different sides: crawler.py **ours**,
  requirements.txt and crawl_authenticated_pages.py **theirs**. The tool must
  allow different choices per file.
- Bonus: flag that a `.pyc` / `__pycache__` file shouldn't be tracked at all.

**S06 — binary PNG** (real merge `2e8f3392`)
- `pages/page-1.png` conflicts. Real resolution: deleted along with the
  matching .html and .txt files. crawler.py got a 1-line blend.

**S07 — documentation only** (real merge `0a7507ce`)
- Only README.md conflicts (+197/−495). The tool should classify it as low-risk
  (docs only), because no code provenance is at stake.

**S08 — a branch named "prod" is not production**
- The `abhiman-prod` → `main` merge conflicts in .gitignore, README.md and main.py.
- **Must not** label the `abhiman-prod` side "production-tested" because of the
  branch name. With no records, it is **unknown**.

**S09 — very stale branch**
- `gauri` is 97 commits behind `main`. Merging it gives 57 conflicts, almost all
  binary (PNG, `.pyc`) or generated (`assets/`, `pages/`); 1 text hunk.
- The tool should point out how stale the branch is and that the conflicted
  files are mostly build output, not code.

**S10 — no merge base**
- `mock` is an orphan branch: it shares no history with `main`, and git refuses the
  merge ("refusing to merge unrelated histories").
- Pass: a clear message ("no common ancestor; provenance can't be compared").
  Fail: a Python traceback from an empty `git merge-base` result.

**S11 — fast-forward promotion**
- `prod` is a strict ancestor of `main` (83 commits behind, 0 ahead).
  Promoting `main` → `prod` is a fast-forward with no conflict.
- The tool must not invent a conflict or create an unnecessary merge commit.
  It *should* list the 83 commits being promoted and show that none has a test
  record, because that is exactly the "unverified change heading to prod" warning
  the problem statement asks for.

**S12 — one person, many identities**
- `koray-afk` and `Abhiman Singh` share `abhiman030@gmail.com`.
- Abhi Jain appears as `abhijain3002@gmail.com` (positiveabhii),
  `abhijain@Abhis-MacBook-Air.local`, and a GitHub `noreply` address.
- `Jgauri24` and `Gauri Jindal` share an email.
- The tool's origin view should group these (e.g. via `.mailmap`), not show
  5+ different "people" for 4 humans.

**S13 — no false backport matches**
- Run the tool's equivalence detection (patch-id + fuzzy matching) across all branches.
- Expected: **0 exact matches** (verified: no shared patch-ids, no `-x` trailers).
  Any fuzzy match must have a low score and be labelled "possible", never "same change".

**S14 — no metadata**
- Run S01 on a plain clone (no `--notes`). Every side must be
  **"provenance unknown"**, never "untested" or "failed". "No record" and
  "tested and failed" are different things.

**S15 — simulated provenance on real commits** (`spider_setup.sh --notes`)
- S01: ours `3b795a84` → **uat pass + deployed to production**; theirs
  `de3e1de7` → **dev pass only**. The tool must show exactly that.
- S02: ours `95583a8f` → **uat FAIL**. The tool must show the failure clearly
  instead of hiding it; a side with a failed test is a strong signal for the resolver.
- After `git clone /tmp/spider /tmp/spider2`, the notes are gone, so the answers
  go back to S14 ("unknown") until `git fetch origin 'refs/notes/*:refs/notes/*'`.

**S16 — pull merges**
- `6d3fa038` and `478e3fa4` merge `main` into a local `main`. The tool should treat
  both parents as the same branch and not invent a "cross-branch" provenance story.

## Minimum set for CI

S02 (fast, 1 hunk), S04 (no markers), S10 (no merge base), S13 (false
positives) and S14 (unknown ≠ untested). These run in seconds and catch the
most common crashes and wrong claims.
