"""Production status needs deployment evidence, not just presence on the prod branch."""


def test_fixture_both_commits_are_on_prod_branch(production):
    repo, c = production
    for sha in (c["deployed"], c["not_deployed"]):
        assert "prod" in repo.git("branch", "--contains", sha)
    assert [r["kind"] for r in repo.notes(c["deployed"])] == ["test", "deploy"]
    assert repo.notes(c["not_deployed"]) == []


def test_fixture_clone_has_no_notes(production_clone):
    clone, c = production_clone
    assert clone.git("notes", "--ref=tests", "list", check=False).stdout == ""


def test_deployed_commit_is_production(tool, production):
    repo, c = production
    assert tool.status(repo, c["deployed"])["status"] == "production"


def test_undeployed_commit_on_prod_branch_is_not_production(tool, production):
    repo, c = production
    assert tool.status(repo, c["not_deployed"])["status"] == "unverified"


def test_clone_without_metadata_reports_unknown(tool, production_clone):
    """No notes at all in this clone: 'unknown', never 'unverified' or 'failed'."""
    clone, c = production_clone
    assert tool.status(clone, c["deployed"])["status"] == "unknown"
