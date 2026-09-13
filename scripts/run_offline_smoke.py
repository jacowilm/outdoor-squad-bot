#!/usr/bin/env python3
"""Safe offline wrapper around ../run_review_build_smoke.py.

Run from anywhere with: python3 scripts/run_offline_smoke.py

The parent's review (2026-09-13) correctly rejected treating the smoke's
early-exit ("AI backend is not configured" / "Admin auth is not configured")
as proof of a clean run — that gate means the 32+ conversation probes never
actually executed. This wrapper makes the ORIGINAL, UNMODIFIED
run_review_build_smoke.main() runnable and its assertions meaningful, with
every live transport physically unreachable rather than merely unconfigured:

  * ai_configured / admin_configured are satisfied with LOCAL-ONLY placeholder
    values (never real secrets, never printed, never sent anywhere — the
    admin password is only checked by HTTP Basic auth against this same
    in-process TestClient, and the "API key" is never read because the
    functions that would use it are replaced below before any client is
    constructed).
  * generate_anthropic_reply / generate_openai_reply / generate_gemini_reply
    are monkeypatched to raise immediately, so no socket is ever opened to
    any AI provider. generate_ai_reply's existing retry/except logic catches
    that RuntimeError exactly as it would a real outage, and /api/chat's own
    existing OUTDOOR_SQUAD_ENABLE_DEMO_FALLBACK path takes over from there —
    this is the SAME fallback code path production uses during a real AI
    outage, not a new one built for this wrapper.
  * maybe_push_lead_to_momence, notify_lead_summary(_async), send_email_resend
    and send_whatsapp_via_twilio are monkeypatched to no-op/capture, so no
    lead, tag, email, or WhatsApp message ever leaves this process.
  * leads.json / events.jsonl / conversation_logs.jsonl are pointed at a
    tmp directory for the duration of the run (restored/left untouched
    either way — nothing is written into the repo's real data files).
  * socket.getaddrinfo / socket.create_connection are patched to raise for
    the duration of the run — a hard block on DNS resolution and outbound
    TCP connection setup, independent of the function-level patches above,
    so a code path those patches didn't anticipate still can't leave the
    machine. (socket.socket()/socketpair() itself is left alone: asyncio's
    event loop needs it for its internal self-pipe, which is loopback-only
    and never reaches the network.)

Does NOT alter run_review_build_smoke.py's own assertions or CASES — it only
makes the environment around them safe, per the parent's instruction not to
suppress or rewrite the smoke's own failure conditions.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Must be set BEFORE `import app` (module-scope env reads there).
os.environ["OUTDOOR_SQUAD_ENABLE_DEMO_FALLBACK"] = "1"
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = ""
os.environ.setdefault("OUTDOOR_SQUAD_ADMIN_PASSWORD", "local-smoke-only-not-a-real-secret")
# Never read: generate_anthropic_reply is replaced below before any client
# construction, so this value is never used to open a connection or sent
# anywhere. It exists only so configured_ai_providers() reports non-empty.
os.environ.setdefault("OUTDOOR_SQUAD_ANTHROPIC_API_KEY", "sk-ant-local-smoke-placeholder-never-sent")
# Default per-IP rate limit is 30/window; TestClient presents a single IP
# ("testclient") for every one of the smoke's 30+ probes, so without raising
# this the tail probes get silently 429'd (empty body, no "reply" key) and
# the run crashes on a KeyError that looks like a routing bug but isn't
# (documented gotcha: RATE_LIMIT_MAX_PER_WINDOW is captured at import time,
# so this MUST be set before `import app`).
os.environ.setdefault("OUTDOOR_SQUAD_RATE_MAX", "1000")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
import app  # noqa: E402
import socket  # noqa: E402


class _NoNetworkAI(RuntimeError):
    pass


class _SocketBlocked(RuntimeError):
    pass


def _blocked_getaddrinfo(host, *args, **kwargs):
    raise _SocketBlocked(
        f"network disabled for offline smoke: DNS/getaddrinfo attempted for {host!r}. "
        "FastAPI's TestClient talks to the app in-process (ASGI transport), so any "
        "outbound resolution attempt means something tried to reach outside the process."
    )


def _blocked_create_connection(address, *args, **kwargs):
    raise _SocketBlocked(
        f"network disabled for offline smoke: outbound connection attempted to {address!r}."
    )


def _blocked_anthropic(message: str, session_id: str) -> str:
    raise _NoNetworkAI("network disabled for offline smoke: anthropic")


def _blocked_openai(message: str, session_id: str) -> str:
    raise _NoNetworkAI("network disabled for offline smoke: openai")


def _blocked_gemini(message: str, session_id: str) -> str:
    raise _NoNetworkAI("network disabled for offline smoke: gemini")


def _noop_momence(lead_info, session_id, *, source, suppressed=False, trusted=True):
    return None


def _noop_notify_lead_summary(lead_info, *, reason):
    return False


def _noop_notify_lead_summary_async(lead_info, *, reason):
    return None


def _noop_send_email_resend(subject, body, recipients, html=None):
    return False


def _noop_send_whatsapp(to_digits, body):
    return True, "SM-offline-smoke-not-sent"


def main() -> int:
    # Patch the innermost network-calling functions so no socket to an AI
    # provider, Momence, Resend, SMTP, or Twilio can be opened from this run.
    app.generate_anthropic_reply = _blocked_anthropic
    app.generate_openai_reply = _blocked_openai
    app.generate_gemini_reply = _blocked_gemini
    app.maybe_push_lead_to_momence = _noop_momence
    app.notify_lead_summary = _noop_notify_lead_summary
    app.notify_lead_summary_async = _noop_notify_lead_summary_async
    app.send_email_resend = _noop_send_email_resend
    app.send_whatsapp_via_twilio = _noop_send_whatsapp

    # Belt-and-braces network block, on top of the function-level patches
    # above: block DNS resolution and outbound TCP connection setup at the
    # socket-module level, so even a code path the patches above didn't
    # anticipate cannot reach the network. Does NOT touch socket.socket()
    # itself or socket.socketpair() — asyncio's event loop needs those for
    # its internal self-pipe, which never leaves the machine.
    real_getaddrinfo = socket.getaddrinfo
    real_create_connection = socket.create_connection
    socket.getaddrinfo = _blocked_getaddrinfo
    socket.create_connection = _blocked_create_connection
    try:
        with tempfile.TemporaryDirectory(prefix="outdoor-squad-smoke-") as tmp:
            tmp_dir = Path(tmp)
            app.LEADS_FILE = tmp_dir / "leads.json"
            app.EVENTS_FILE = tmp_dir / "events.jsonl"
            app.CONVERSATION_LOG_FILE = tmp_dir / "conversation_logs.jsonl"
            app.LEADS_FILE.write_text("[]")
            app.EVENTS_FILE.write_text("")
            app.CONVERSATION_LOG_FILE.write_text("")

            sys.path.insert(0, str(REPO_ROOT))
            import run_review_build_smoke as smoke
            return smoke.main()
    finally:
        socket.getaddrinfo = real_getaddrinfo
        socket.create_connection = real_create_connection


if __name__ == "__main__":
    raise SystemExit(main())
