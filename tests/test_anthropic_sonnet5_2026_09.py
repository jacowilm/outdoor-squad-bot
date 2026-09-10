"""Robo-Nick's AI tail runs on Claude Sonnet 5 (switched 2026-09-10).

Sonnet 5 rejects a non-default `temperature` with a 400 and runs adaptive
thinking by default (tokens count against max_tokens). The request builder
must therefore send no temperature and disable thinking for Claude 5 models,
while a legacy override (Sonnet 4.6, Haiku 4.5) keeps the tuned 0.82.
"""
import os
import sys
import tempfile
import importlib
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _k in (
    "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY",
    "OUTDOOR_SQUAD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY",
    "OUTDOOR_SQUAD_OPENAI_API_KEY", "OPENAI_API_KEY",
    "OUTDOOR_SQUAD_GEMINI_API_KEY", "GEMINI_API_KEY",
    "OUTDOOR_SQUAD_ANTHROPIC_MODEL",
):
    os.environ.pop(_k, None)

import app  # noqa: E402
importlib.reload(app)
app.SUPABASE_URL = ""
app.SUPABASE_KEY = None

_TMP = Path(tempfile.mkdtemp(prefix="os-sonnet5-"))
app.LEADS_FILE = _TMP / "leads.json"; app.LEADS_FILE.write_text("[]")
app.EVENTS_FILE = _TMP / "events.jsonl"; app.EVENTS_FILE.write_text("")
app.CONVERSATION_LOG_FILE = _TMP / "conversation_logs.jsonl"; app.CONVERSATION_LOG_FILE.write_text("")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    app.conversations.clear()
    monkeypatch.delenv("OUTDOOR_SQUAD_ANTHROPIC_MODEL", raising=False)
    yield
    app.conversations.clear()


def _request(session_id: str, message: str) -> dict:
    app.load_conversation(session_id).append({"role": "user", "content": message})
    return app.build_anthropic_request(message, session_id)


def test_default_model_is_sonnet_5():
    req = _request("s5-default", "Do you train in the rain?")
    assert req["model"] == "claude-sonnet-5"
    assert app.ANTHROPIC_DEFAULT_MODEL == "claude-sonnet-5"


def test_sonnet_5_request_has_no_sampling_params_and_thinking_off():
    req = _request("s5-shape", "Do you train in the rain?")
    for banned in ("temperature", "top_p", "top_k"):
        assert banned not in req, f"{banned} returns 400 on Claude 5 models"
    assert req["thinking"] == {"type": "disabled"}
    assert req["max_tokens"] == 520
    assert req["messages"][-1] == {"role": "user", "content": "Do you train in the rain?"}
    assert req["system"][-1]["cache_control"] == {"type": "ephemeral"}


def test_env_override_to_claude_5_model_also_drops_temperature(monkeypatch):
    monkeypatch.setenv("OUTDOOR_SQUAD_ANTHROPIC_MODEL", "claude-opus-5")
    req = _request("s5-opus", "hi")
    assert req["model"] == "claude-opus-5"
    assert "temperature" not in req
    assert req["thinking"] == {"type": "disabled"}


@pytest.mark.parametrize("legacy", ["claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-6"])
def test_legacy_override_keeps_tuned_temperature_and_no_thinking_key(monkeypatch, legacy):
    monkeypatch.setenv("OUTDOOR_SQUAD_ANTHROPIC_MODEL", legacy)
    req = _request(f"legacy-{legacy}", "hi")
    assert req["model"] == legacy
    assert req["temperature"] == 0.82
    assert "thinking" not in req


def test_sampling_params_helper_is_fail_safe_for_unknown_future_ids():
    # An id we have never seen must NOT get a temperature: omitting it is
    # accepted by every model, sending 0.82 is a 400 on anything post-4.6.
    assert "temperature" not in app.anthropic_sampling_params("claude-sonnet-6")
    assert "temperature" not in app.anthropic_sampling_params("claude-opus-4-8")
