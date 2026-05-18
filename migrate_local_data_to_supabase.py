import json
import os
from pathlib import Path
from typing import Optional

import httpx


APP_DIR = Path(__file__).parent
LEADS_FILE = APP_DIR / "leads.json"
EVENTS_FILE = APP_DIR / "events.jsonl"
CONVERSATION_LOG_FILE = APP_DIR / "conversation_logs.jsonl"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")

TABLES = {
    "events": "outdoor_squad_events",
    "conversation_logs": "outdoor_squad_conversation_logs",
    "leads": "outdoor_squad_leads",
}


def require_env(name: str, value: str) -> str:
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def headers(prefer: Optional[str] = None) -> dict[str, str]:
    result = {
        "apikey": require_env("SUPABASE_KEY", SUPABASE_KEY),
        "Authorization": f"Bearer {require_env('SUPABASE_KEY', SUPABASE_KEY)}",
        "Content-Type": "application/json",
    }
    if prefer:
        result["Prefer"] = prefer
    return result


def request(method: str, table: str, *, params: Optional[dict] = None, json_body=None, prefer: Optional[str] = None):
    url = f"{require_env('SUPABASE_URL', SUPABASE_URL)}/rest/v1/{table}"
    response = httpx.request(method, url, headers=headers(prefer), params=params, json=json_body, timeout=20.0)
    response.raise_for_status()
    return response.json() if response.text.strip() else None


def read_json_array(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def backfill_leads() -> int:
    leads = read_json_array(LEADS_FILE)
    if not leads:
        return 0
    payload = []
    for lead in leads:
        row = dict(lead)
        row.setdefault("concerns", [])
        payload.append(row)
    request(
        "POST",
        TABLES["leads"],
        json_body=payload,
        prefer="return=minimal",
    )
    return len(payload)


def backfill_events() -> int:
    events = read_jsonl(EVENTS_FILE)
    if not events:
        return 0
    payload = []
    for event in events:
        payload.append({
            "timestamp": event.get("timestamp"),
            "event_type": event.get("event_type", "unknown"),
            "session_id": event.get("session_id", "unknown"),
            "metadata": {
                key: value
                for key, value in event.items()
                if key not in {"timestamp", "event_type", "session_id"}
            },
        })
    request("POST", TABLES["events"], json_body=payload, prefer="return=minimal")
    return len(payload)


def backfill_logs() -> int:
    logs = read_jsonl(CONVERSATION_LOG_FILE)
    if not logs:
        return 0
    request("POST", TABLES["conversation_logs"], json_body=logs, prefer="return=minimal")
    return len(logs)


if __name__ == "__main__":
    summary = {
        "leads": backfill_leads(),
        "events": backfill_events(),
        "conversation_logs": backfill_logs(),
    }
    print(json.dumps(summary))
