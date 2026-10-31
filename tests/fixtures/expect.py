"""Small helpers for checking the tool's JSON answers (shapes: test-fixtures/CONTRACT.md)."""

STATUSES = {"production", "uat", "dev", "failed", "flaky", "rolled-back", "unverified", "unknown"}


def same(a, b):
    """SHA equality that accepts abbreviated SHAs (>= 7 chars)."""
    return (isinstance(a, str) and isinstance(b, str) and min(len(a), len(b)) >= 7
            and (a.startswith(b) or b.startswith(a)))


def entries_for(conflicts_out, path):
    return [e for e in (conflicts_out.get("conflicts") or []) if e.get("file") == path]


def matched(matches_out, sha):
    """The verified match entry for `sha`, or None."""
    for m in matches_out.get("matches") or []:
        if same(m.get("commit"), sha) and m.get("verified", True) is not False:
            return m
    return None
