"""Builds the spider-python branch scenarios. Each returns (repo, commits-by-name).

Branches: dev -> uat -> prod. Test results are produced by really running the
app tests on each commit and are stored in git notes (refs/notes/tests).
"""
from . import sample_commits as sc
from .sample_repo import OPS, QA, SpiderRepo


def passing_and_failing(path):
    """Scenario 1 + 2: a dev change that passes, a uat change that fails."""
    repo = SpiderRepo(path)
    c = {"base": repo.seed()}
    repo.checkout("dev")
    repo.change(sc.WAIT, 6000)
    repo.write("tests/test_wait.py", sc.WAIT_TEST)
    c["dev_change"] = repo.commit("Wait 6s after load: pages looked blurry")
    c["dev_record"] = repo.record_tests("dev")
    repo.checkout("uat")
    repo.change(sc.WAIT, 4500)
    repo.change(sc.POST_AUTH_PAGES, 0)  # the deliberate bug
    c["uat_change"] = repo.commit("Wait 4.5s; disable post-auth crawl while debugging timeouts", author=QA)
    c["uat_record"] = repo.record_tests("uat")
    return repo, c


def conflict_dev_vs_uat(path):
    """Scenario 3: dev and uat changed the same line; merging uat into dev conflicts."""
    repo, c = passing_and_failing(path)
    repo.checkout("dev")
    repo.git("merge", "--no-edit", "uat", check=False)
    return repo, c


def cherry_picked_fix(path):
    """Scenario 4: the same fix on three branches with three different SHAs."""
    repo = SpiderRepo(path)
    c = {"base": repo.seed()}
    repo.change(sc.INTERACTIONS, 12)
    c["dev_fix"] = repo.commit("Fewer interactions per page to reduce flakiness")
    repo.record_tests("dev")
    repo.change(sc.WAIT, 6000)
    repo.write("tests/test_wait.py", sc.WAIT_TEST)
    c["dev_other"] = repo.commit("Wait 6s after load")
    repo.checkout("uat")
    repo.git("cherry-pick", "-x", c["dev_fix"], author=QA)
    c["uat_copy"] = repo.head()
    repo.record_tests("uat")
    repo.checkout("prod")
    repo.git("cherry-pick", "-x", c["dev_fix"], author=OPS)
    c["prod_copy"] = repo.head()  # on the prod branch, but never deployed
    return repo, c


def changed_after_test(path):
    """Scenario 5: a tested commit, then an untested edit of the same line."""
    repo = SpiderRepo(path)
    c = {"base": repo.seed()}
    repo.change(sc.WAIT, 6000)
    repo.write("tests/test_wait.py", sc.WAIT_TEST)
    c["tested"] = repo.commit("Wait 6s after load")
    repo.record_tests("dev")
    repo.change(sc.WAIT, 6500)
    c["edited_after_test"] = repo.commit("Wait 6.5s (quick tweak, no CI run)")
    repo.checkout("prod")
    repo.change(sc.WAIT, 3000)
    c["prod_hotfix"] = repo.commit("HOTFIX: wait 3s", author=OPS)
    repo.record_tests("production")
    repo.record_deploy(c["prod_hotfix"])
    return repo, c


def production_history(path):
    """prod branch with one deployed commit and one that was never deployed."""
    repo = SpiderRepo(path)
    c = {"base": repo.seed()}
    repo.checkout("prod")
    repo.change(sc.PRE_AUTH_PAGES, 3)
    c["deployed"] = repo.commit("Crawl fewer pre-auth pages", author=OPS)
    repo.record_tests("production")
    repo.record_deploy(c["deployed"])
    repo.change(sc.RANKED, 20)
    c["not_deployed"] = repo.commit("Rank more interactions", author=OPS)
    return repo, c
