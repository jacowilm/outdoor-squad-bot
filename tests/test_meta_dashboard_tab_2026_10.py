"""Messenger and Instagram on the admin dashboard (1 Oct 2026).

cc3f3c6 added the channel and an admin /api/meta/conversations, but the
dashboard never showed it: fb- and ig- threads were listed (and counted) as
website chats, with no way to mute or reply. Now:
- /admin and /api/admin/snapshot carry a "meta" part built by
  meta_dashboard_payload, and a DMs tab renders it with a channel filter.
- The tab reuses the endpoints that already handle fb-/ig- sessions
  (/api/wa/reply, /api/wa/mute, /api/wa/kill with "channel"); no new send path.
- The Website tab leaves out every prefix that has a tab of its own.

The browser behaviour is pinned statically (no browser in this suite), like
test_sms_tab_autorefresh_2026_09.py.
"""
import importlib
import json
import os
import re
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY",
           "OUTDOOR_SQUAD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY",
           "OUTDOOR_SQUAD_OPENAI_API_KEY", "OPENAI_API_KEY",
           "OUTDOOR_SQUAD_GEMINI_API_KEY", "GEMINI_API_KEY",
           "META_APP_SECRET", "META_VERIFY_TOKEN", "META_PAGE_ACCESS_TOKEN"):
    os.environ.pop(_k, None)

import app  # noqa: E402

importlib.reload(app)
app.SUPABASE_URL = ""
app.SUPABASE_KEY = None

client = TestClient(app.app)
AUTH = ("u", "p")
FB = "fb-2343196399046153"
IG = "ig-1443235994382283"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "SUPABASE_URL", "")
    monkeypatch.setattr(app, "SUPABASE_KEY", None)
    for name, filename, empty in [
        ("CONVERSATION_LOG_FILE", "conversation_logs.jsonl", ""),
        ("EVENTS_FILE", "events.jsonl", ""),
        ("LEADS_FILE", "leads.json", "[]"),
    ]:
        path = tmp_path / filename
        path.write_text(empty)
        monkeypatch.setattr(app, name, path)
    monkeypatch.setattr(app, "WA_STATE_FILE", tmp_path / "wa_state.json")
    monkeypatch.setattr(app, "ADMIN_USERNAME", "u")
    monkeypatch.setattr(app, "ADMIN_PASSWORD", "p")
    monkeypatch.setattr(app, "get_admin_password_hash", lambda: None)
    # Nothing in this file may reach Meta.
    monkeypatch.setattr(app, "send_meta_message",
                        lambda *a, **k: pytest.fail("the dashboard must not send"))
    app._wa_setting_cache.clear()
    yield
    app._wa_setting_cache.clear()


def _log(rows):
    with app.CONVERSATION_LOG_FILE.open("a") as fh:
        for sid, role, content, ts in rows:
            fh.write(json.dumps({"session_id": sid, "role": role, "content": content,
                                 "timestamp": ts}) + "\n")


def _seed():
    _log([
        ("widget-abc", "user", "website question", "2026-09-30T09:00:00"),
        (FB, "user", "how much is a trial?", "2026-09-30T10:00:00"),
        (FB, "assistant", "The first week is free.", "2026-09-30T10:00:05"),
        (IG, "user", "do you train in the rain?", "2026-09-30T11:00:00"),
        ("wa-61400000001", "user", "hi", "2026-09-30T08:00:00"),
    ])


# ── Payload ───────────────────────────────────────────────────────────────

def test_payload_lists_only_meta_threads_newest_first_with_their_channel():
    _seed()
    payload = app.meta_dashboard_payload()
    convs = payload["conversations"]
    assert [c["session_id"] for c in convs] == [IG, FB]
    assert {c["session_id"]: c["channel"] for c in convs} == {IG: "instagram", FB: "messenger"}
    fb = convs[1]
    assert fb["message_count"] == 2
    assert fb["last_message"]["content"] == "The first week is free."
    # Both switches default OFF and are reported, never changed, by the payload.
    assert payload["channels_enabled"] == {"messenger": False, "instagram": False}


def test_payload_flags_come_from_one_settings_snapshot(monkeypatch):
    _seed()
    app.set_wa_setting(f"mute:{FB}", str(time.time() + 3600))
    app.set_wa_setting(f"opted_out:{IG}", "1")
    app.set_wa_setting("fb::channel_enabled", "1")
    app._wa_setting_cache.clear()
    calls = []
    real = app.get_wa_setting
    monkeypatch.setattr(app, "get_wa_setting", lambda key, *a: (calls.append(key), real(key, *a))[1])
    convs = {c["session_id"]: c for c in app.meta_dashboard_payload()["conversations"]}
    assert convs[FB]["muted"] is True and convs[FB]["opted_out"] is False
    assert convs[IG]["muted"] is False and convs[IG]["opted_out"] is True
    assert not [k for k in calls if k.startswith(("mute:", "opted_out:"))]


def test_expired_mute_reads_as_unmuted():
    _seed()
    app.set_wa_setting(f"mute:{FB}", str(time.time() - 5))
    convs = {c["session_id"]: c for c in app.meta_dashboard_payload()["conversations"]}
    assert convs[FB]["muted"] is False


def test_meta_lead_without_a_channel_column_is_not_a_website_lead():
    assert app._lead_channel({"session_id": FB}) == "messenger"
    assert app._lead_channel({"session_id": IG}) == "instagram"
    assert app._lead_channel({"session_id": "wa-614"}) == "whatsapp"
    assert app._lead_channel({"session_id": "widget-x"}) == "website"
    assert app._lead_channel({"session_id": FB, "channel": "messenger"}) == "messenger"


# ── Admin endpoints ───────────────────────────────────────────────────────

def test_snapshot_serves_the_meta_part_to_admins_only():
    _seed()
    assert client.get("/api/admin/snapshot?parts=meta").status_code == 401
    r = client.get("/api/admin/snapshot?parts=meta", auth=AUTH)
    assert r.status_code == 200
    assert set(r.json()) == {"meta"}
    assert [c["session_id"] for c in r.json()["meta"]["conversations"]] == [IG, FB]


def test_admin_page_carries_the_dms_tab_and_its_data():
    _seed()
    _log([(FB, "user", "</script><script>alert(1)</script>", "2026-09-30T12:00:00")])
    r = client.get("/admin", auth=AUTH)
    assert r.status_code == 200
    html = r.text
    for marker in ('data-tab="meta"', 'data-panel="meta"', 'id="metaCount"', 'id="metaThreads"',
                   'id="metaReplyForm"', 'id="metaMuteBtn"', 'data-meta-filter="instagram"',
                   'id="metaToggleMessenger"', 'id="metaToggleInstagram"'):
        assert marker in html, marker
    data = json.loads(re.search(r"window\.__OS_ADMIN_DATA__ = (\{.*?\});\n", html, re.S).group(1))
    assert {c["session_id"] for c in data["meta"]["conversations"]} == {FB, IG}
    assert "</script><script>alert(1)" not in html


def test_switches_shown_on_the_page_change_nothing_by_being_rendered():
    _seed()
    client.get("/admin", auth=AUTH)
    client.get("/api/admin/snapshot?parts=meta,overview", auth=AUTH)
    assert app.meta_channel_enabled("messenger") is False
    assert app.meta_channel_enabled("instagram") is False


# ── Static guarantees about the page's script ─────────────────────────────

def _js_between(start, end):
    html = app.ADMIN_HTML
    i = html.index(start)
    return html[i:html.index(end, i)]


def test_website_tab_leaves_out_every_prefix_with_its_own_tab():
    block = re.search(r"const OWN_TAB_PREFIXES = \[(.*?)\];", app.ADMIN_HTML).group(1)
    assert set(re.findall(r"'([a-z]+-)'", block)) == set(app.RESERVED_SESSION_PREFIXES)
    fn = _js_between("function websiteTranscripts()", "function waPhone")
    assert "OWN_TAB_PREFIXES" in fn


def test_dms_tab_uses_only_the_existing_meta_aware_endpoints():
    js = _js_between("// ── Messenger and Instagram (1 Oct 2026)", "// ── Auto-refresh")
    endpoints = set(re.findall(r"fetch\('(/api/[a-z/]+)'", js))
    assert endpoints == {"/api/wa/kill", "/api/wa/mute", "/api/wa/reply"}
    # The switch names its channel, so it can never flip WhatsApp's instead.
    assert "JSON.stringify({ channel: channel, enabled: enabled })" in js
    # Turning a channel on asks first; turning it off does not.
    assert "if (enabled && !window.confirm(" in js


def test_reply_box_is_closed_while_the_channel_is_off_or_the_window_shut():
    js = _js_between("function renderMetaDetail()", "async function metaRefresh")
    assert "const canReply = !!thread.window_open && channelOn;" in js
    assert "document.activeElement === input" in js
    assert "if (!sameThread) { note.textContent = ''" in js


def test_dms_tab_polls_its_parts_and_redraws_on_them():
    block = re.search(r"const TAB_PARTS = \{(.*?)\};", app.ADMIN_HTML, re.S).group(1)
    meta_parts = re.search(r"meta: \[(.*?)\]", block).group(1)
    assert set(re.findall(r"'([a-z]+)'", meta_parts)) == {"meta", "transcripts", "leads"}
    overview = re.search(r"overview: \[(.*?)\]", block).group(1)
    assert "'meta'" in overview
    assert "if (changed.meta || changed.transcripts || changed.leads)" in app.ADMIN_HTML


def test_overview_counts_messenger_and_instagram():
    js = _js_between("function renderOverview()", "// ── Website chat")
    assert "metaData().conversations" in js
    assert "['messenger', 'instagram'].map" in js


def test_no_dashes_in_the_new_copy():
    start = app.ADMIN_HTML.index('<section class="panel" data-panel="meta">')
    panel = app.ADMIN_HTML[start:app.ADMIN_HTML.index("</section>", start)]
    js = _js_between("// ── Messenger and Instagram (1 Oct 2026)", "// ── Auto-refresh")
    for chunk in (panel, js):
        assert "—" not in chunk and "–" not in chunk
