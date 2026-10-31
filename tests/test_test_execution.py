"""Scenarios 1 + 2: test results are produced, counted and tied to the right commit."""
from fixtures.expect import same


# --- fixture checks -------------------------------------------------------------

def test_fixture_dev_change_passes(pass_fail):
    repo, c = pass_fail
    rec = c["dev_record"]
    assert same(rec["commit"], c["dev_change"])
    assert (rec["result"], rec["passed"], rec["failed"]) == ("pass", 4, 0)


def test_fixture_uat_change_fails(pass_fail):
    repo, c = pass_fail
    rec = c["uat_record"]
    assert same(rec["commit"], c["uat_change"])
    assert (rec["result"], rec["passed"], rec["failed"]) == ("fail", 2, 1)


def test_fixture_records_are_stored_in_git_notes(pass_fail):
    repo, c = pass_fail
    assert [r["result"] for r in repo.notes(c["dev_change"])] == ["pass"]
    assert [r["result"] for r in repo.notes(c["uat_change"])] == ["fail"]
    assert repo.notes(c["base"]) == []


# --- tool tests -----------------------------------------------------------------

def test_passing_commit_is_reported_as_tested(tool, pass_fail):
    repo, c = pass_fail
    out = tool.status(repo, c["dev_change"])
    assert out["status"] == "dev"
    assert out.get("tests") == {"passed": 4, "failed": 0}


def test_failing_commit_is_reported_as_failed(tool, pass_fail):
    repo, c = pass_fail
    out = tool.status(repo, c["uat_change"])
    assert out["status"] == "failed"
    assert out.get("tests") == {"passed": 2, "failed": 1}


def test_commit_without_tests_is_not_tested(tool, pass_fail):
    """'NOT TESTED': records exist in this repo, just none for this commit."""
    repo, c = pass_fail
    assert tool.status(repo, c["base"])["status"] == "unverified"
