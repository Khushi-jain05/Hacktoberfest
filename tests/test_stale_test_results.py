"""Scenario 5: a PASS must not carry over to code that changed after the test."""
from fixtures.expect import entries_for, same


def test_fixture_only_the_first_version_was_tested(stale):
    repo, c = stale
    assert [r["result"] for r in repo.notes(c["tested"])] == ["pass"]
    assert repo.notes(c["edited_after_test"]) == []
    assert repo.git("rev-parse", f"{c['edited_after_test']}^") == c["tested"]


def test_tested_commit_keeps_its_pass(tool, stale):
    repo, c = stale
    assert tool.status(repo, c["tested"])["status"] == "dev"


def test_commit_edited_after_test_is_unverified(tool, stale):
    repo, c = stale
    assert tool.status(repo, c["edited_after_test"])["status"] == "unverified"


def test_conflict_side_from_untested_edit_is_unverified(tool, stale, tmp_path):
    """Merging dev into prod: the dev line comes from the untested edit, not the tested commit."""
    repo, c = stale
    work = repo.__class__.__new__(repo.__class__)
    work.path, work.clock = tmp_path / "work", repo.clock
    repo.git("clone", "-q", "--no-local", str(repo.path), str(work.path))
    work.git("fetch", "-q", "origin", "refs/notes/*:refs/notes/*")
    work.git("checkout", "-q", "-B", "prod", "origin/prod")
    work.git("merge", "--no-edit", "origin/dev", check=False)
    assert work.unmerged() == ["config.py"]
    [entry] = entries_for(tool.conflicts(work), "config.py")
    assert same(entry["theirs"]["origin_commit"], c["edited_after_test"])
    assert entry["theirs"]["status"] == "unverified"
    assert entry["ours"]["status"] == "production"
