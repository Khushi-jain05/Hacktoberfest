"""Scenario 3: dev (tests pass) and uat (tests fail) changed the same setting."""
from fixtures.expect import entries_for


def test_both_sides_are_shown_with_their_test_evidence(tool, conflict):
    repo, c = conflict
    [entry] = entries_for(tool.conflicts(repo), "config.py")
    assert entry["ours"]["status"] == "dev"        # dev change: 4 passed
    assert entry["theirs"]["status"] == "failed"   # uat change: 1 failed


def test_tool_does_not_pick_a_side_just_because_its_tests_passed(tool, conflict):
    repo, c = conflict
    head = repo.head()
    out = tool.resolve(repo)
    assert out.get("resolved") is False, "6000 vs 4500 ms is a human decision; the tool must not pick"
    assert out.get("reason")
    assert repo.head() == head
    assert repo.unmerged() == ["config.py"]


def test_output_is_deterministic(tool, conflict):
    repo, c = conflict
    assert tool.conflicts(repo) == tool.conflicts(repo)
