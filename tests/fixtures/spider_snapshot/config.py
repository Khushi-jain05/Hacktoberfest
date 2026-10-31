"""Per-application crawl configuration."""
# Excerpt of spider-python's config.py at commit 1aeb2516 (zoho app only).
# Used as realistic test data for the provenance tool; the tests parse it, never import it.

APPS = {
    "zoho": {
        "pre_auth_home": "https://www.zoho.com/in/books/",
        "login_url": (
            "https://accounts.zoho.com/signin?servicename=ZohoBooks"
            "&signupurl=https://www.zoho.com%2fin%2fbooks%2fsignup%2f"
        ),
        "post_auth_home": "https://books.zoho.in",
        "max_pages_pre_auth": 5,
        "max_pages_post_auth": 120,
        "max_interactions_per_page": 15,
        "max_ranked_interactions": 18,
        "max_interaction_depth": None,
        "crawl_skip_screenshots": True,
        # Let fonts/CSS settle — 1s was making pages look blurry/incomplete.
        "crawl_wait_after_load_ms": 2000,
        "crawl_use_networkidle": True,
        "priority_url_patterns": [
            "#/home/dashboard",
            "#/invoices",
            "#/quotes",
            "#/contacts",
            "#/inventory/product/index",
            "/new",
        ],
    },
}


def get_app_config(app_name: str) -> dict:
    if app_name not in APPS:
        raise ValueError(f"Unknown app '{app_name}'. Add it to config.py")
    return APPS[app_name]
