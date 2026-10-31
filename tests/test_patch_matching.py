"""Scenario 4: the same change under different SHAs on different branches."""
from fixtures.expect import matched


def test_fixture_copies_have_different_shas_but_same_patch(cherry):
    repo, c = cherry
    shas = {c["dev_fix"], c["uat_copy"], c["prod_copy"]}
    assert len(shas) == 3
    assert len({repo.patch_id(s) for s in shas}) == 1
    assert repo.patch_id(c["dev_other"]) != repo.patch_id(c["dev_fix"])


def test_fixture_copies_carry_cherry_pick_trailer(cherry):
    repo, c = cherry
    for copy in ("uat_copy", "prod_copy"):
        assert f"(cherry picked from commit {c['dev_fix']})" in repo.git("log", "-1", "--format=%B", c[copy])


def test_uat_copy_is_matched_to_original(tool, cherry):
    repo, c = cherry
    m = matched(tool.matches(repo, c["uat_copy"]), c["dev_fix"])
    assert m, "cherry-picked commit not linked to its original"
    assert m.get("exact") is True


def test_prod_copy_is_matched_to_original(tool, cherry):
    repo, c = cherry
    assert matched(tool.matches(repo, c["prod_copy"]), c["dev_fix"])


def test_different_patches_are_not_matched(tool, cherry):
    repo, c = cherry
    assert not matched(tool.matches(repo, c["dev_other"]), c["dev_fix"])
