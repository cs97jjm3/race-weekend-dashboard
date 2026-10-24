"""Asking Claude: a blank answer must count as a failure, not an empty message on screen."""
import io
import json

import pytest

import notify


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_blank_answer_is_reported(monkeypatch):
    monkeypatch.setattr(notify.config, "ANTHROPIC_API_KEY", "sk-ant-test")
    body = json.dumps({"content": [{"type": "thinking", "thinking": "..."}], "stop_reason": "max_tokens"}).encode()
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda req, timeout: Reply(body))
    with pytest.raises(RuntimeError, match="empty answer"):
        notify.ask_claude("hello")


def test_text_answer_comes_back(monkeypatch):
    monkeypatch.setattr(notify.config, "ANTHROPIC_API_KEY", "sk-ant-test")
    body = json.dumps({"content": [{"type": "text", "text": " Leclerc leads. "}], "stop_reason": "end_turn"}).encode()
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda req, timeout: Reply(body))
    assert notify.ask_claude("hello") == "Leclerc leads."


def test_gemini_answer_and_its_hidden_thinking(monkeypatch):
    monkeypatch.setattr(notify.config, "AI_PROVIDER", "gemini", raising=False)
    monkeypatch.setattr(notify.config, "GEMINI_API_KEY", "AIza-test", raising=False)
    seen = {}

    def fake_post(url, body, headers, timeout):
        seen.update(url=url, headers=headers)
        return {"candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True},
                                                       {"text": "Verstappen is closing."}]}, "finishReason": "STOP"}]}
    monkeypatch.setattr(notify, "_post", fake_post)
    assert notify.ask_claude("hi") == "Verstappen is closing."
    assert ":generateContent" in seen["url"] and seen["headers"]["x-goog-api-key"] == "AIza-test"
    assert notify.ai_name() == "Gemini" and notify.ai_ready()


def test_openai_style_server_needs_no_key(monkeypatch):
    monkeypatch.setattr(notify.config, "AI_PROVIDER", "openai", raising=False)
    monkeypatch.setattr(notify.config, "OPENAI_BASE_URL", "http://localhost:3001/v1", raising=False)
    monkeypatch.setattr(notify.config, "OPENAI_API_KEY", "", raising=False)
    monkeypatch.setattr(notify, "_post", lambda url, body, headers, timeout: {
        "choices": [{"message": {"content": "Hamilton pits."}, "finish_reason": "stop"}]})
    assert notify.ai_ready()
    assert notify.ask_claude("hi") == "Hamilton pits."


def test_claude_is_the_default(monkeypatch):
    monkeypatch.delattr(notify.config, "AI_PROVIDER", raising=False)
    assert notify.provider() == "claude" and notify.ai_name() == "Claude"


def test_back_up_ai_steps_in_when_the_first_fails(monkeypatch):
    monkeypatch.setattr(notify.config, "AI_PROVIDER", "gemini", raising=False)
    monkeypatch.setattr(notify.config, "GEMINI_API_KEY", "AQ.test", raising=False)
    monkeypatch.setattr(notify.config, "AI_FALLBACK", "claude", raising=False)
    monkeypatch.setattr(notify.config, "ANTHROPIC_API_KEY", "sk-ant-test", raising=False)

    def fake_post(url, body, headers, timeout):
        if "googleapis" in url:
            raise notify.Busy("HTTP 503 high demand")
        return {"content": [{"type": "text", "text": "Leclerc on pole."}], "stop_reason": "end_turn"}
    monkeypatch.setattr(notify, "_post", fake_post)
    assert notify.ask_claude("hi") == "Leclerc on pole."
    assert notify.used_backup() and notify.last_used[0] == "claude"


def test_back_up_only_used_when_it_has_a_key(monkeypatch):
    monkeypatch.setattr(notify.config, "AI_PROVIDER", "gemini", raising=False)
    monkeypatch.setattr(notify.config, "GEMINI_API_KEY", "AQ.test", raising=False)
    monkeypatch.setattr(notify.config, "AI_FALLBACK", "claude", raising=False)
    monkeypatch.setattr(notify.config, "ANTHROPIC_API_KEY", "", raising=False)
    assert notify.fallback() is None
    monkeypatch.setattr(notify, "_post", lambda *a, **k: (_ for _ in ()).throw(notify.Busy("HTTP 503")))
    with pytest.raises(notify.Busy):
        notify.ask_claude("hi")
