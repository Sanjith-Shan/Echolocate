"""Provider selection rules — pure unit tests, no network."""

import asyncio
import os

from backend import ai_provider


def _save():
    return {k: os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                                            "CLAUDE_MODEL", "OPENAI_MODEL")}


def _restore(saved):
    for k, v in saved.items():
        if v is None: os.environ.pop(k, None)
        else: os.environ[k] = v


def test_priority_anthropic_beats_openai():
    saved = _save()
    try:
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-fake"
        os.environ["OPENAI_API_KEY"] = "sk-fake"
        assert ai_provider.active_provider() == "anthropic"
        assert ai_provider.active_model().startswith("claude")
    finally:
        _restore(saved)


def test_openai_used_when_only_openai_key_set():
    saved = _save()
    try:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ["OPENAI_API_KEY"] = "sk-fake"
        os.environ["OPENAI_MODEL"] = "gpt-4o-mini"
        assert ai_provider.active_provider() == "openai"
        assert ai_provider.active_model() == "gpt-4o-mini"
    finally:
        _restore(saved)


def test_stub_when_neither_key_set():
    saved = _save()
    try:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)
        assert ai_provider.active_provider() == "stub"
        assert ai_provider.active_model() == "stub"
    finally:
        _restore(saved)


def test_stub_text_complete_returns_message():
    saved = _save()
    try:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)
        text, model = asyncio.run(ai_provider.text_complete(
            system="x", user="hi", max_tokens=10))
        assert model == "stub"
        assert isinstance(text, str) and len(text) > 0
    finally:
        _restore(saved)


def test_stub_vision_returns_deterministic_dict():
    saved = _save()
    try:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)
        parsed, model = asyncio.run(
            ai_provider.vision_analyze(image_b64="abcd", prompt="x"))
        assert model == "stub"
        assert parsed["_stub"] is True
        assert "total_people_visible" in parsed
    finally:
        _restore(saved)
