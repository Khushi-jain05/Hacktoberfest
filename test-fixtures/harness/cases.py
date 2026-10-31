"""Machine-checkable test cases for the fixture repos (interface: CONTRACT.md).

CASES maps each scenario to a list of cases. A case runs `setup` (shell commands,
inside `dir` of a fresh copy of the fixture), then queries the tool and evaluates
`checks`. "@name" anywhere in setup or checks is replaced by the SHA that
expected.json lists under "commits".
"""
P = "app/payment.py"
L = "app/legacy.py"

PROD, UAT, DEV = "production", "uat", "dev"
UNVERIFIED, UNKNOWN = "unverified", "unknown"


# --- check builders ---------------------------------------------------------

def files(*paths):
    """Exactly these files are reported as conflicted (none = no conflict)."""
    return {"type": "conflict_files", "files": list(paths)}


def hunks(path, n):
    return {"type": "hunk_count", "file": path, "count": n}


def op(*names):
    """The in-progress operation is one of `names`."""
    return {"type": "operation", "operation": list(names)}


def side(which, origin=None, *status, file=P, hunk=None, origin_not=None):
    """Every reported entry for `file` (or just `hunk`) has this ours/theirs info.

    `origin` may be a list: any of those commits is accepted."""
    return {"type": "side", "side": which, "file": file, "hunk": hunk,
            "origin": origin, "origin_not": origin_not, "status": list(status)}


def field(path, key, value):
    return {"type": "field", "file": path, "key": key, "value": value}


def status(commit, *allowed):
    return {"type": "status", "commit": commit, "status": list(allowed)}


def match(commit, original, exact=None):
    return {"type": "match", "commit": commit, "original": original, "exact": exact}


def no_match(commit, original):
    return {"type": "no_match", "commit": commit, "original": original}


def mentions(query, commit, text):
    """The tool's `query` output for `commit` contains `text` somewhere."""
    return {"type": "mentions", "query": query, "commit": commit, "text": text}


# --- setup builders ---------------------------------------------------------

def on(branch):
    return [f"git checkout -q {branch}"]


def pick(branch, ref):
    return on(branch) + [f"git cherry-pick -x {ref}"]


def merge(branch, other):
    return on(branch) + [f"git merge --no-edit {other}"]


def case(setup, checks, dir="repo", name="main", setup_dir=None, timeout=None):
    """`setup` runs in `setup_dir` (default: `dir`); the tool runs in `dir`."""
    return {"name": name, "dir": dir, "setup_dir": setup_dir or dir, "setup": setup,
            "checks": checks, "timeout": timeout}


def _resolve(expected):
    """Resolve scenarios (r*): the spec lives in expected.json under "resolve"."""
    spec = expected["resolve"]
    return [case(spec["setup"], [{"type": "resolve", **spec}])]


def _s40(expected):
    checks = [op("merge"), files("app/big.py"), hunks("app/big.py", len(expected["hunks"]))]
    for k, h in enumerate(expected["hunks"], start=1):
        checks.append(side("ours", h["ours_origin"], PROD, file="app/big.py", hunk=k))
        checks.append(side("theirs", h["theirs_origin"], DEV, file="app/big.py", hunk=k))
    return [case(merge("prod", "dev"), checks)]


CASES = {
    "s01_clean_backport": [case(on("prod"), [
        files(),
        match("@prod_copy", "@dev_fix", True),
        status("@prod_copy", PROD),
        status("@uat_copy", UAT, PROD),
    ])],
    "s02_prod_hotfix_vs_dev_fix": [case(pick("prod", "@dev_fix"), [
        op("cherry-pick"), files(P), hunks(P, 1),
        side("ours", "@prod_hotfix", PROD),
        side("theirs", "@dev_fix", DEV),
    ])],
    "s03_modified_backport_with_trailer": [case(on("uat"), [
        match("@uat_backport", "@dev_fix", False),
        status("@uat_backport", UAT),
    ])],
    "s04_backport_no_trailer_reworded": [case(on("prod"), [
        match("@prod_retyped", "@dev_change", False),
        status("@prod_retyped", PROD),
    ])],
    "s05_whitespace_only_difference": [case(on("prod"), [
        match("@prod_copy", "@dev_fix", True),
        status("@prod_copy", PROD),
    ])],
    "s06_clone_without_metadata": [
        case(pick("prod", "@dev_fix"), dir="clone", name="no-notes", checks=[
            files(P),
            side("ours", "@prod_hotfix", UNKNOWN),
            side("theirs", "@dev_fix", UNKNOWN),
        ]),
        case(["git fetch -q origin 'refs/notes/*:refs/notes/*'"] + pick("prod", "@dev_fix"),
             dir="clone", name="after-fetching-notes", checks=[
            files(P),
            side("ours", "@prod_hotfix", PROD),
            side("theirs", "@dev_fix", DEV),
        ]),
    ],
    "s07_both_sides_untested": [case(pick("prod", "@dev_fix"), [
        files(P),
        side("ours", "@prod_change", UNVERIFIED),
        side("theirs", "@dev_fix", UNVERIFIED),
    ])],
    "s08_rename_then_conflict": [case(pick("dev", "@prod_hotfix"), [
        files("app/billing.py"),
        side("ours", "@dev_fix", DEV, file="app/billing.py"),
        side("theirs", "@prod_hotfix", PROD, file="app/billing.py"),
    ])],
    "s09_modify_delete": [case(merge("dev", "prod"), [
        op("merge"), files(L), field(L, "kind", "modify/delete"),
        side("ours", "@dev_delete", DEV, file=L),
        side("theirs", "@prod_hotfix", PROD, file=L),
    ])],
    "s10_multi_hunk_mixed_provenance": [case(pick("prod", "@dev_fix"), [
        files(P), hunks(P, 2),
        side("ours", "@prod_deployed", PROD, hunk=1),
        side("theirs", "@dev_fix", UAT, hunk=1),
        side("ours", "@prod_undeployed", UNVERIFIED, hunk=2),
        side("theirs", "@dev_fix", UAT, hunk=2),
    ])],
    "s11_failed_test_reverted_then_reapplied": [case(pick("prod", "@uat_v2"), [
        files(P),
        side("ours", "@prod_hotfix", PROD),
        side("theirs", "@uat_v2", UAT),
        status("@uat_v1_failed", "failed"),
    ])],
    "s12_squashed_backport": [case(on("prod"), [
        match("@prod_squash", "@dev_a"),
        match("@prod_squash", "@dev_b"),
        match("@prod_squash", "@dev_c"),
        status("@prod_squash", PROD),
    ])],
    "s13_test_was_on_older_commit": [case(merge("prod", "uat"), [
        files(P),
        side("ours", "@prod_hotfix", PROD),
        side("theirs", "@uat_untested_tip", UNVERIFIED),
    ])],
    "s14_criss_cross_merge": [case(merge("uat", "dev"), [
        files(P),
        side("ours", "@uat_fee", UAT),
        side("theirs", "@dev_fee", DEV),
    ])],
    "s15_recurring_conflict_rerere": [case(merge("release", "dev"), [
        files(P),
        field(P, "previous_resolution", "@uat_resolution_merge"),
        status("@uat_resolution_merge", UAT),
    ])],
    "s16_chained_backport": [case(on("prod"), [
        match("@prod_copy", "@dev_original", True),
        match("@prod_copy", "@uat_copy", True),
        status("@prod_copy", PROD),
    ])],
    "s17_chain_modified_midway": [case(on("prod"), [
        match("@prod_copy", "@uat_modified_copy", True),
        match("@prod_copy", "@dev_original", False),
        status("@prod_copy", PROD),
    ])],
    "s18_resolution_introduces_new_code": [case(on("prod"), [
        status("@resolution", UNVERIFIED),
        status("@prod_hotfix", PROD),
    ])],
    "s19_resolution_mixes_hunks": [case(on("prod"), [
        status("@resolution", UNVERIFIED),
    ])],
    "s20_same_patch_different_base": [case(on("prod"), [
        match("@prod_copy", "@dev_fix", True),
        status("@prod_copy", UNVERIFIED),
    ])],
    "s21_rebased_after_test": [case(on("feature"), [
        status("@after_rebase", DEV, UNVERIFIED),
        mentions("status", "@after_rebase", "@tested_before_rebase"),
    ])],
    "s22_notes_diverged_between_clones": [case(
        ["git fetch -q origin refs/notes/tests:refs/notes/origin-tests"], dir="clone_ops", checks=[
            status("@commit", PROD),
            mentions("status", "@commit", "uat"),
        ])],
    "s23_trailer_points_to_missing_commit": [case(on("prod"), [
        status("@prod_copy", PROD),
        mentions("matches", "@prod_copy", "@missing_original"),
    ])],
    "s24_lying_trailer": [case(on("prod"), [
        no_match("@prod_commit", "@claimed_original"),
        status("@prod_commit", UNVERIFIED),
    ])],
    "s25_deploy_rollback": [case(pick("prod", "@dev_fix"), [
        files(P),
        side("ours", "@p2_rolled_back", "rolled-back"),
        side("theirs", "@dev_fix", DEV),
        status("@p1", PROD),
        status("@p2_rolled_back", "rolled-back"),
    ])],
    "s26_flaky_test_history": [case(pick("prod", "@dev_fix"), [
        files(P),
        side("ours", "@prod_hotfix", PROD),
        side("theirs", "@dev_fix", "flaky"),
    ])],
    "s27_test_on_descendant": [case(merge("prod", "uat"), [
        files(P),
        side("ours", "@prod_hotfix", PROD),
        side("theirs", "@uat_line_origin", UAT),
    ])],
    "s28_evil_merge": [case(merge("prod", "dev"), [
        files(P),
        side("ours", "@prod_hotfix", PROD),
        side("theirs", "@evil_merge", UNVERIFIED),
    ])],
    "s29_github_squash_merge": [case(on("dev"), [
        match("@squash", "@pr_head"),
        status("@squash", DEV),
    ])],
    "s30_partial_backport": [case(on("prod"), [
        match("@partial_backport", "@original", False),
        status("@partial_backport", PROD),
    ])],
    "s31_conflict_mid_sequence": [case(on("prod") + ["git cherry-pick -x @a^..@c_pending"], [
        op("cherry-pick"), files(P),
        side("ours", "@prod_hotfix", PROD),
        side("theirs", "@b_conflicts", DEV),
    ])],
    "s32_rebase_inverts_ours_theirs": [case(on("feature") + ["git rebase dev"], [
        op("rebase"), files(P),
        side("ours", "@dev_commit", PROD),
        side("theirs", "@feature_commit", DEV),
    ])],
    "s33_revert_conflict": [case(on("prod") + ["git revert --no-edit @reverted"], [
        op("revert"), files(P),
        side("ours", "@later_change", PROD),
        side("theirs", "@base"),
    ])],
    "s34_diff3_and_custom_marker_size": [case(pick("prod", "@dev_fix"), [
        files(P, "docs/fees.md"),
        side("ours", "@prod_hotfix", PROD),
        side("theirs", "@dev_fix", DEV),
        side("ours", "@prod_hotfix", PROD, file="docs/fees.md"),
    ])],
    "s35_literal_markers_in_content": [case(pick("prod", "@dev_fix"), [
        files(P),
    ])],
    "s36_rename_rename_and_add_add": [case(merge("dev", "prod"), [
        files("app/billing.py", "app/config.py", "app/fees.py", P),
        field("app/config.py", "kind", "add/add"),
        side("ours", "@dev_commit", DEV, file="app/config.py"),
        side("theirs", "@prod_commit", PROD, file="app/config.py"),
    ])],
    "s37_unicode_paths_crlf_and_modes": [case(merge("dev", "prod"), [
        files("app/über fees/größe.py"),
        side("ours", "@dev_fix", DEV, file="app/über fees/größe.py"),
        side("theirs", "@prod_hotfix", PROD, file="app/über fees/größe.py"),
    ])],
    "s38_shallow_clone": [case(pick("prod", "@dev_fix"), dir="shallow", checks=[
        files(P),
        side("ours", None, UNKNOWN, origin_not="@prod_boundary"),
    ])],
    "s39_same_fix_two_paths": [case(on("prod"), [
        files(),
        match("@dev_fix", "@prod_copy", True),
        status("@dev_fix", PROD),
    ])],
    "s40_large_history": _s40,
    "s41_both_sides_variants_of_same_fix": [case(merge("prod", "uat"), [
        files(P),
        side("ours", "@prod_variant", PROD),
        side("theirs", "@uat_variant", UAT),
        match("@prod_variant", "@dev_original", False),
        match("@uat_variant", "@dev_original", False),
    ])],
    "s42_code_moved_between_files": [case(merge("dev", "prod"), [
        files(P),
        side("ours", "@dev_move", DEV),
        side("theirs", "@prod_hotfix", PROD),
        field(P, "moved_to", "app/refunds.py"),
    ])],
    "s43_formatter_commit_hides_origin": [case(merge("prod", "dev"), [
        files(P),
        side("ours", "@prod_hotfix", PROD),
        side("theirs", "@real_origin", DEV, origin_not="@formatter"),
    ])],
    "s44_tested_trailer_without_ci": [case(pick("prod", "@dev_fix"), [
        files(P),
        side("ours", "@prod_hotfix", PROD),
        side("theirs", "@dev_fix", UNVERIFIED),
        status("@dev_fix", UNVERIFIED),
    ])],
    "s45_rollback_records_out_of_order": [case(pick("prod", "@dev_fix"), [
        files(P),
        side("ours", "@p2_rolled_back", "rolled-back"),
        status("@p1", PROD),
        status("@p2_rolled_back", "rolled-back"),
    ])],
    "s46_symlink_conflict": [case(merge("dev", "prod"), [
        files("config/current.yml"),
        side("ours", "@dev_commit", DEV, file="config/current.yml"),
        side("theirs", "@prod_commit", PROD, file="config/current.yml"),
    ])],
    "s47_submodule_conflict": [case(merge("dev", "prod"), [
        files("lib"),
        side("ours", "@dev_commit", DEV, file="lib"),
        side("theirs", "@prod_commit", PROD, file="lib"),
    ])],
    "s48_mixed_origins_in_one_hunk": [case(pick("prod", "@dev_fix"), [
        files(P), hunks(P, 1),
        side("ours", ["@prod_deployed_line", "@prod_undeployed_line"], UNVERIFIED),
        side("theirs", "@dev_fix", UAT),
    ])],
    "s49_reverted_fix_reintroduced_by_merge": [case(on("prod"), [
        files(),
        match("@dev_fix", "@prod_copy", True),
        status("@dev_fix", "failed", "rolled-back"),
        mentions("status", "@dev_fix", "@prod_revert"),
    ])],
    "s50_deep_history": [case(merge("prod", "dev"), timeout=60, checks=[
        files(P),
        side("ours", "@prod_hotfix", PROD),
        side("theirs", "@deep_origin", DEV),
    ])],
    "s51_non_utf8_and_no_final_newline": [case(merge("dev", "prod"), [
        files("app/legacy_latin1.py", "app/limits.txt"),
        side("ours", "@dev_commit", DEV, file="app/legacy_latin1.py"),
        side("theirs", "@prod_commit", PROD, file="app/legacy_latin1.py"),
        side("ours", "@dev_commit", DEV, file="app/limits.txt"),
        side("theirs", "@prod_commit", PROD, file="app/limits.txt"),
    ])],
    "s52_deletion_vs_modification": [case(pick("prod", "@dev_fix"), [
        files(P), hunks(P, 1),
        side("ours", "@prod_deleting_hotfix", PROD),
        side("theirs", "@dev_fix", DEV),
    ])],
    "s53_original_only_in_remote_ref": [case(on("prod"), [
        match("@prod_copy", "@upstream_original", True),
        status("@prod_copy", PROD),
    ])],
    "s54_git_am_backport": [case(on("prod") + ["git am -3 ../backport.patch"], [
        op("am"), files(P),
        side("ours", "@prod_hotfix", PROD),
        side("theirs", "@dev_fix", DEV),
    ])],
    "s55_conflict_in_linked_worktree": [case(
        ["git worktree add -q ../wt prod", "git -C ../wt cherry-pick -x @dev_fix"],
        dir="wt", setup_dir="repo", checks=[
            op("cherry-pick"), files(P),
            side("ours", "@prod_hotfix", PROD),
            side("theirs", "@dev_fix", DEV),
        ])],
    "s56_squash_merge_conflict": [case(on("prod") + ["git merge --squash dev"], [
        op("merge", "squash-merge"), files(P),
        side("ours", "@prod_hotfix", PROD),
        side("theirs", "@dev_fix", DEV),
    ])],
    "s57_partially_resolved": [case(
        pick("prod", "@dev_fix") + ["git checkout --theirs app/legacy.py", "git add app/legacy.py"], [
            files(P),
            side("ours", "@prod_hotfix", PROD),
            side("theirs", "@dev_fix", DEV),
        ])],
    "s58_ambiguous_short_sha": [case(on("prod"), [
        match("@prod_copy", "@dev_fix", True),
        no_match("@prod_copy", "@decoy"),
        status("@prod_copy", PROD),
    ])],
    "s59_noisy_provenance_records": [case(pick("prod", "@dev_fix"), [
        files(P),
        side("ours", "@prod_hotfix", PROD),
        side("theirs", "@dev_fix", UAT),
        status("@dev_fix", UAT),
    ])],
    "s60_cherry_picked_merge_commit": [case(on("prod"), [
        match("@prod_copy", "@dev_merge", True),
        status("@prod_copy", PROD),
    ])],
    "s61_empty_cherry_pick": [case(pick("prod", "@dev_fix"), [
        op("cherry-pick"), files(),
        match("@dev_fix", "@prod_hand_applied", True),
        status("@dev_fix", PROD),
    ])],
    **{name: _resolve for name in (
        "r01_changelog_both_added", "r02_requirements_both_added", "r03_whitespace_only_side",
        "r04_pr_carries_stale_copy_of_fix", "r05_previous_tested_resolution",
        "r06_both_sides_logic_unverified", "r07_mixed_safe_and_unsafe_files",
        "r08_code_moved_needs_reapply", "r09_backport_compatible_intents", "r10_rebase_pr_onto_main",
    )},
}
