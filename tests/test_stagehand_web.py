"""
Stagehand web body — result parsing + containment gating + graceful degradation.

The extract result is a nested SessionExtractResponse(data=Data(result={...}));
we must pull the text out of it. Web reads must be containment-gated (SSRF), and
the whole module must degrade to None when Stagehand/keys are absent so the
raw-CDP path takes over.
"""
from services import stagehand_web


class _Data:
    def __init__(self, result):
        self.result = result


class _Resp:
    def __init__(self, result):
        self.data = _Data(result)


def test_text_reads_nested_extraction():
    # The live shape: SessionExtractResponse(data=Data(result={'extraction': ...}))
    assert stagehand_web._text(_Resp({"extraction": "Example Domain"})) == "Example Domain"
    assert stagehand_web._text(_Resp({"result": "fallback key"})) == "fallback key"


def test_text_handles_plain_shapes():
    assert stagehand_web._text({"extraction": "from dict"}) == "from dict"
    assert stagehand_web._text(None) == ""


def test_web_extract_blocks_internal_hosts(monkeypatch):
    # Even if Stagehand were available, a containment-blocked URL never runs.
    monkeypatch.setattr(stagehand_web, "available", lambda: True)
    called = {"n": 0}
    # _contained must veto first; if it does, we never construct a client.
    assert stagehand_web.web_extract("http://169.254.169.254/latest/", "x") is None
    assert stagehand_web.web_extract("http://localhost:8765/admin", "x") is None


def test_unavailable_is_none(monkeypatch):
    monkeypatch.setattr(stagehand_web, "available", lambda: False)
    assert stagehand_web.web_extract("https://example.com", "heading") is None
    assert stagehand_web.web_act("https://example.com", "click") is None
