"""Scenario 4, continued: testing history follows a change across dev -> uat -> prod."""


def test_fixture_each_copy_has_its_own_records(cherry):
    repo, c = cherry
    assert [r["env"] for r in repo.notes(c["dev_fix"])] == ["dev"]
    assert [r["env"] for r in repo.notes(c["uat_copy"])] == ["uat"]
    assert repo.notes(c["prod_copy"]) == []


def test_uat_copy_uses_its_own_uat_result(tool, cherry):
    repo, c = cherry
    out = tool.status(repo, c["uat_copy"])
    assert out["status"] == "uat"
    assert "uat" in str(out.get("evidence"))


def test_original_is_at_least_dev_tested(tool, cherry):
    repo, c = cherry
    assert tool.status(repo, c["dev_fix"])["status"] in {"dev", "uat"}


def test_prod_copy_is_not_production_without_a_deploy(tool, cherry):
    """Being on the prod branch is not evidence of deployment."""
    repo, c = cherry
    assert tool.status(repo, c["prod_copy"])["status"] != "production"
