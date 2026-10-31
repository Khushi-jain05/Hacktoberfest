"""The concrete code changes and app tests used in the spider-python scenarios.

The app tests read config.py with `ast` instead of importing it, so they run
without Playwright, network access or any spider-python dependency.
"""

PYTEST_INI = "[pytest]\n"

LOADER = '''import ast
import pathlib


def load_apps():
    source = pathlib.Path(__file__).resolve().parent.parent.joinpath("config.py").read_text()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "APPS":
            return ast.literal_eval(node.value)
    raise AssertionError("APPS not found in config.py")
'''

# 3 tests that pass on the unmodified snapshot.
APP_TESTS = LOADER + '''

def test_zoho_is_configured():
    assert "zoho" in load_apps()


def test_wait_after_load_is_positive():
    assert load_apps()["zoho"]["crawl_wait_after_load_ms"] > 0


def test_post_auth_page_budget_is_sane():
    assert 0 < load_apps()["zoho"]["max_pages_post_auth"] <= 500
'''

# Added together with the dev change: pages need >= 5 s to settle.
WAIT_TEST = LOADER + '''

def test_wait_is_long_enough_for_fonts():
    assert load_apps()["zoho"]["crawl_wait_after_load_ms"] >= 5000
'''


def setting(key, value):
    """The exact config.py line for a zoho setting."""
    return f'"{key}": {value},'


# Settings the scenarios change (value in the snapshot in the comment).
WAIT = "crawl_wait_after_load_ms"            # 2000
POST_AUTH_PAGES = "max_pages_post_auth"      # 120
INTERACTIONS = "max_interactions_per_page"   # 15
PRE_AUTH_PAGES = "max_pages_pre_auth"        # 5
RANKED = "max_ranked_interactions"           # 18
