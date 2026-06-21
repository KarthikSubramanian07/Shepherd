"""
Tests for the provider-agnostic LLM layer (engine/llm.py). No network — only the
pure helpers (provider/model selection, robust JSON-array extraction) are exercised.

    .venv/bin/python tests/test_llm.py     # or: pytest tests/
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from engine import llm


def test_provider_and_model_selection_is_env_driven():
    orig = (config.LLM_PROVIDER, config.GEMINI_MODEL, config.LLM_ANTHROPIC_MODEL)
    try:
        config.LLM_PROVIDER = "gemini"
        config.GEMINI_MODEL = "gemma-4-31b-it"
        assert llm.provider() == "gemini"
        assert llm.model_name() == "gemma-4-31b-it"

        config.LLM_PROVIDER = "ANTHROPIC"   # case-insensitive
        config.LLM_ANTHROPIC_MODEL = "claude-haiku-4-5"
        assert llm.provider() == "anthropic"
        assert llm.model_name() == "claude-haiku-4-5"
    finally:
        config.LLM_PROVIDER, config.GEMINI_MODEL, config.LLM_ANTHROPIC_MODEL = orig


def test_available_reflects_active_provider_key():
    orig = (config.LLM_PROVIDER, config.GEMINI_API_KEY, config.ANTHROPIC_API_KEY)
    try:
        config.LLM_PROVIDER = "gemini"
        config.GEMINI_API_KEY = ""
        config.ANTHROPIC_API_KEY = "present"
        assert llm.available() is False          # gemini selected, no gemini key
        config.GEMINI_API_KEY = "present"
        assert llm.available() is True
    finally:
        config.LLM_PROVIDER, config.GEMINI_API_KEY, config.ANTHROPIC_API_KEY = orig


def test_parse_plain_array():
    assert llm.parse_json_array('[{"a": 1}, {"b": 2}]') == [{"a": 1}, {"b": 2}]


def test_parse_code_fenced_array():
    text = 'Here you go:\n```json\n[{"kind": "open"}]\n```\nDone.'
    assert llm.parse_json_array(text) == [{"kind": "open"}]


def test_parse_returns_first_balanced_array_skipping_junk():
    # Reasoning models leak a malformed array then emit the real one.
    text = '[ {"x": } broken ]\n```json\n[{"ok": true}]\n```'
    assert llm.parse_json_array(text) == [{"ok": True}]


def test_parse_handles_nested_arrays():
    assert llm.parse_json_array('prose [[1, 2], [3]] tail') == [[1, 2], [3]]


def test_parse_raises_without_array():
    for bad in ["", "no json here", "{\"obj\": 1}"]:
        try:
            llm.parse_json_array(bad)
            assert False, f"expected ValueError for {bad!r}"
        except ValueError:
            pass


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
