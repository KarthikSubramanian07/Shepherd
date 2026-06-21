"""
Containment SSRF floor — the cloud browser must never reach internal/metadata
hosts, regardless of how permissive the domain allowlist is.
"""
from services import policy_engine


def _blocked(url: str) -> bool:
    return policy_engine.check_containment("browser", url) is not None


def test_blocks_cloud_metadata_and_internal():
    assert _blocked("http://169.254.169.254/latest/meta-data/")   # AWS/GCP metadata
    assert _blocked("http://metadata.google.internal/")
    assert _blocked("http://localhost:8765/admin")
    assert _blocked("http://127.0.0.1/")
    assert _blocked("http://10.0.0.5/internal")
    assert _blocked("http://192.168.1.1/")


def test_blocks_non_web_schemes():
    assert _blocked("file:///etc/passwd")
    assert _blocked("gopher://internal/")


def test_allows_normal_public_sites():
    # Default allowlist is empty (permissive on domains) — public sites pass the
    # SSRF floor and the (empty) allowlist.
    assert not _blocked("https://example.com/page")
    assert not _blocked("https://www.berkeley.edu/research")


def test_host_match_is_not_naive_substring(monkeypatch):
    # With an allowlist set, a lookalike host must NOT pass via substring.
    monkeypatch.setattr(policy_engine, "_load",
                        lambda: {"containment": {"allowed_domains": ["example.com"]}})
    assert _blocked("https://evil-example.com.attacker.net/")   # not a subdomain
    assert not _blocked("https://app.example.com/")             # true subdomain
