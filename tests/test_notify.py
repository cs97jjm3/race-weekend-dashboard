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
    body = json.dumps({"content": [{"type": "thinking", "thinking": "..."}], "stop_reason": "max_tokens"}).encode()
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda req, timeout: Reply(body))
    with pytest.raises(RuntimeError, match="empty answer"):
        notify.ask_claude("hello")


def test_text_answer_comes_back(monkeypatch):
    body = json.dumps({"content": [{"type": "text", "text": " Leclerc leads. "}], "stop_reason": "end_turn"}).encode()
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda req, timeout: Reply(body))
    assert notify.ask_claude("hello") == "Leclerc leads."
