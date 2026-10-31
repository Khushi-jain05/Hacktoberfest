"""The tool points each side of a conflict at the commit that really made the change."""
from fixtures.expect import entries_for, same


def test_fixture_merge_conflicts_on_config(conflict):
    repo, c = conflict
    assert repo.unmerged() == ["config.py"]
    text = (repo.path / "config.py").read_text()
    assert text.count("<<<<<<<") == 1
    assert '"crawl_wait_after_load_ms": 6000,' in text and '"crawl_wait_after_load_ms": 4500,' in text


def test_fixture_unrelated_uat_bug_merged_cleanly(conflict):
    """uat's second change (post-auth pages = 0) is on another line: git merged it silently."""
    repo, c = conflict
    assert '"max_pages_post_auth": 0,' in (repo.path / "config.py").read_text()


def test_conflict_reports_operation_and_file(tool, conflict):
    repo, c = conflict
    out = tool.conflicts(repo)
    assert out["operation"] == "merge"
    assert {e["file"] for e in out["conflicts"]} == {"config.py"}
    assert len(entries_for(out, "config.py")) == 1


def test_each_side_has_the_right_origin_commit(tool, conflict):
    repo, c = conflict
    [entry] = entries_for(tool.conflicts(repo), "config.py")
    assert same(entry["ours"]["origin_commit"], c["dev_change"])
    assert same(entry["theirs"]["origin_commit"], c["uat_change"])


def test_reporting_does_not_change_the_repo(tool, conflict):
    repo, c = conflict
    before = (repo.git("status", "--porcelain=v2"), repo.head(), repo.unmerged())
    tool.conflicts(repo)
    assert (repo.git("status", "--porcelain=v2"), repo.head(), repo.unmerged()) == before
