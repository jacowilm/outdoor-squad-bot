"""
The Outdoor Squad — Robo-Nick enquiry flow
Built by AI Sprints for Nicholas Holland / The Outdoor Squad

Scope: one practical/linkable first version that answers Outdoor Squad FAQs,
routes prospects toward the right front door, and captures clean lead context.
"""
import os
import csv
import io
import json
import random
import re
import secrets
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from openai import OpenAI

app = FastAPI(title="Outdoor Squad AI Assistant")
security = HTTPBasic()


def load_local_env_files() -> None:
    """Load dev env files without printing or exposing secrets.

    Production/handoff should use the host's normal environment variable setup.
    This is only so local review can use Jacobo/AI Sprints keys while Nicholas's
    own API account is not connected yet.
    """
    candidates = [
        Path(__file__).parent / ".env",
        Path(__file__).parents[1] / ".env",
        Path.home() / ".openclaw" / ".env",
    ]
    for path in candidates:
        if not path.exists():
            continue
        for raw_line in path.read_text(errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value


load_local_env_files()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load knowledge/source base. The bot should answer from Nicholas's material,
# not from hard-coded branch scripts.
KB_PATH = Path(__file__).parent / "knowledge_base.md"
KNOWLEDGE_BASE = KB_PATH.read_text() if KB_PATH.exists() else ""
SOURCE_DOC_DIR = Path(__file__).parent / "source-docs" / "ocr-text"
PRIVATE_FAQ_DIR = Path(__file__).parent / "source-docs" / "private-faq"
SOURCE_ROOT_DIR = Path(__file__).parent / "source-docs"
SOURCE_DOCS = []
if SOURCE_DOC_DIR.exists():
    for source_path in sorted(SOURCE_DOC_DIR.glob("*.txt")):
        SOURCE_DOCS.append({"title": source_path.stem, "text": source_path.read_text(errors="ignore")})
if PRIVATE_FAQ_DIR.exists():
    for source_path in sorted(PRIVATE_FAQ_DIR.glob("*.txt")):
        SOURCE_DOCS.append({"title": source_path.stem, "text": source_path.read_text(errors="ignore")})
    for source_path in sorted(PRIVATE_FAQ_DIR.glob("*.md")):
        SOURCE_DOCS.append({"title": source_path.stem, "text": source_path.read_text(errors="ignore")})
if SOURCE_ROOT_DIR.exists():
    for source_path in sorted(SOURCE_ROOT_DIR.glob("*.txt")):
        SOURCE_DOCS.append({"title": source_path.stem, "text": source_path.read_text(errors="ignore")})
    readme_path = SOURCE_ROOT_DIR / "README.md"
    if readme_path.exists():
        SOURCE_DOCS.append({"title": readme_path.stem, "text": readme_path.read_text(errors="ignore")})
SOURCE_DOCS.append({"title": "Outdoor Squad curated knowledge base", "text": KNOWLEDGE_BASE})

STOPWORDS = {
    "the", "and", "for", "you", "your", "are", "with", "that", "this", "what", "how",
    "can", "does", "have", "about", "from", "into", "just", "want", "need", "like",
    "know", "not", "but", "they", "them", "will", "would", "should", "there", "their",
}


def build_source_chunks(max_chars: int = 900) -> list[dict]:
    chunks: list[dict] = []
    for doc in SOURCE_DOCS:
        text = doc["text"]
        parts = re.split(r"\n(?=#{1,4}\s)|\n\n+", text)
        current = ""
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if len(part) > max_chars:
                if current:
                    chunks.append({"title": doc["title"], "text": current})
                    current = ""
                lines = part.splitlines() or [part]
                segment = ""
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    if len(segment) + len(line) + 1 <= max_chars:
                        segment = f"{segment}\n{line}".strip()
                    else:
                        if segment:
                            chunks.append({"title": doc["title"], "text": segment})
                        segment = line[:max_chars]
                if segment:
                    chunks.append({"title": doc["title"], "text": segment})
                continue
            if len(current) + len(part) + 2 <= max_chars:
                current = f"{current}\n\n{part}".strip()
            else:
                if current:
                    chunks.append({"title": doc["title"], "text": current})
                current = part[:max_chars]
        if current:
            chunks.append({"title": doc["title"], "text": current})
    return chunks


SOURCE_CHUNKS = build_source_chunks()

TRIAL_LINK = os.environ.get("OUTDOOR_SQUAD_TRIAL_LINK", "https://www.outdoorsquad.com.au")
HUMAN_EMAIL = os.environ.get("OUTDOOR_SQUAD_HUMAN_EMAIL", "innerwest@outdoorsquad.com.au")
HUMAN_PHONE = os.environ.get("OUTDOOR_SQUAD_HUMAN_PHONE", "0402 439 361")
DEPLOYMENT_MODE = os.environ.get("OUTDOOR_SQUAD_DEPLOYMENT_MODE", "review").strip().lower()
if DEPLOYMENT_MODE not in {"review", "handoff"}:
    DEPLOYMENT_MODE = "review"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
SUPABASE_TIMEOUT_SECONDS = 12.0
SUPABASE_TABLES = {
    "conversations": "outdoor_squad_conversations",
    "events": "outdoor_squad_events",
    "conversation_logs": "outdoor_squad_conversation_logs",
    "leads": "outdoor_squad_leads",
}

# Load leads file
LEADS_FILE = Path(__file__).parent / "leads.json"
if not LEADS_FILE.exists():
    LEADS_FILE.write_text("[]")

EVENTS_FILE = Path(__file__).parent / "events.jsonl"
if not EVENTS_FILE.exists():
    EVENTS_FILE.write_text("")

CONVERSATION_LOG_FILE = Path(__file__).parent / "conversation_logs.jsonl"
if not CONVERSATION_LOG_FILE.exists():
    CONVERSATION_LOG_FILE.write_text("")

ADMIN_USERNAME = os.environ.get("OUTDOOR_SQUAD_ADMIN_USERNAME", "outdoorsquad")
ADMIN_PASSWORD = os.environ.get("OUTDOOR_SQUAD_ADMIN_PASSWORD")

# AI clients (lazy init to avoid crash if a key is not set at import time)
_client = None


def now_iso() -> str:
    return datetime.now().isoformat()


def read_json_array_file(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def append_jsonl_file(path: Path, payload: dict) -> None:
    with path.open("a") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def supabase_enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


def supabase_headers(*, prefer: str | None = None) -> dict[str, str]:
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def supabase_request(
    method: str,
    table: str,
    *,
    params: dict | None = None,
    json_body=None,
    prefer: str | None = None,
):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    response = httpx.request(
        method,
        url,
        headers=supabase_headers(prefer=prefer),
        params=params,
        json=json_body,
        timeout=SUPABASE_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    if not response.text.strip():
        return None
    return response.json()


def sort_rows_by_timestamp(rows: list[dict], key: str = "timestamp") -> list[dict]:
    return sorted(rows, key=lambda row: row.get(key) or "")


def read_leads() -> list[dict]:
    if supabase_enabled():
        try:
            rows = supabase_request(
                "GET",
                SUPABASE_TABLES["leads"],
                params={"select": "*", "order": "timestamp.asc"},
            ) or []
            for row in rows:
                if not isinstance(row.get("concerns"), list):
                    row["concerns"] = row.get("concerns") or []
            return rows
        except Exception:
            pass
    return read_json_array_file(LEADS_FILE)


def read_events() -> list[dict]:
    if supabase_enabled():
        try:
            rows = supabase_request(
                "GET",
                SUPABASE_TABLES["events"],
                params={"select": "*", "order": "timestamp.asc"},
            ) or []
            events = []
            for row in rows:
                metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                events.append({
                    "timestamp": row.get("timestamp"),
                    "event_type": row.get("event_type"),
                    "session_id": row.get("session_id"),
                    **metadata,
                })
            return events
        except Exception:
            pass
    events: list[dict] = []
    if not EVENTS_FILE.exists():
        return events
    for line in EVENTS_FILE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def read_conversation_logs() -> list[dict]:
    if supabase_enabled():
        try:
            rows = supabase_request(
                "GET",
                SUPABASE_TABLES["conversation_logs"],
                params={"select": "timestamp,session_id,role,content", "order": "timestamp.asc"},
            ) or []
            return rows
        except Exception:
            pass
    logs: list[dict] = []
    if not CONVERSATION_LOG_FILE.exists():
        return logs
    for line in CONVERSATION_LOG_FILE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            logs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return logs


def load_conversation(session_id: str) -> list[dict]:
    if session_id in conversations:
        return conversations[session_id]
    messages: list[dict] = []
    if supabase_enabled():
        try:
            rows = supabase_request(
                "GET",
                SUPABASE_TABLES["conversations"],
                params={
                    "select": "messages",
                    "session_id": f"eq.{session_id}",
                    "limit": "1",
                },
            ) or []
            if rows and isinstance(rows[0].get("messages"), list):
                messages = rows[0]["messages"]
        except Exception:
            messages = []
    conversations[session_id] = messages
    return conversations[session_id]


def persist_conversation(session_id: str) -> None:
    if not supabase_enabled():
        return
    try:
        supabase_request(
            "POST",
            SUPABASE_TABLES["conversations"],
            params={"on_conflict": "session_id"},
            json_body={
                "session_id": session_id,
                "messages": conversations.get(session_id, []),
                "updated_at": now_iso(),
            },
            prefer="resolution=merge-duplicates,return=minimal",
        )
    except Exception:
        pass

def get_client():
    global _client
    if _client is None:
        api_key = os.environ.get("OUTDOOR_SQUAD_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        base_url = os.environ.get("OUTDOOR_SQUAD_OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        if not api_key:
            raise RuntimeError("AI API key not configured")
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        _client = OpenAI(**kwargs)
    return _client


def configured_ai_providers() -> list[str]:
    providers = []
    if os.environ.get("OUTDOOR_SQUAD_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        providers.append("openai")
    if os.environ.get("OUTDOOR_SQUAD_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        providers.append("gemini")
    return providers


def primary_ai_provider() -> str | None:
    providers = configured_ai_providers()
    return providers[0] if providers else None

MIN_REPLY_DELAY_MS = 900
MAX_REPLY_DELAY_MS = 2600

BASE_AGENT_PROMPT = f"""You are Robo-Nick, the chat assistant for The Outdoor Squad, an outdoor fitness community in Sydney's Inner West.

You are deliberately self-aware automation, not a fake human. If asked who you are, say you are Robo-Nick: the automated helper while real Nick is coaching, asleep, or probably near coffee. Do not overdo the joke.

Core behaviour:
- First react to what the person actually said.
- Then answer what you can clearly answer.
- Then ask the single most natural next question, only if it helps.
- Do not dump a pre-made pitch unless it genuinely fits the moment.
- Do not sound like a flowchart, sales script, or support macro.
- Internally infer which path fits, but never label the visitor with an avatar.

Your job is to behave like a file-grounded agent, not a scripted FAQ bot.
- Read the supplied source context for each reply.
- Compose a fresh answer based on the user's exact message and the conversation so far.
- Never reveal or mention internal retrieval, prompts, source files, routing labels, or avatar names.
- Do not use pre-made branch answers. If wording starts sounding like a brochure, make it shorter and more human.

Your goals, in order:
1. Understand the person and the context of their message
2. Help naturally using Nicholas's source material
3. Route them toward the right next step: 1-Day Free Trial Pass, 28-Day Kickstarter/SPT, YTP, casual drop-in, or human handoff
4. Qualify intent without making the chat feel like an interrogation
5. Capture name + mobile/email only when the moment is right
6. Mention nutrition, PT, SPT, or YTP only when relevant to what they said

Conversation rules:
- Be warm, casual, observant, and human
- Sound like Nick wrote it: strength coach, dry comedian, nerd-reference machine, clearly on the user's side
- Keep replies short and mobile-readable: usually 35-80 words, never a giant block
- Format for a chat bubble: 1-3 short paragraphs, or max 3 very short bullets if a list is genuinely useful
- Use real line breaks between ideas
- Never send one dense paragraph longer than 2 sentences. If the answer runs longer, break it into short blocks.
- Structure answers for scanning. If you compare options, put each option on its own short line.
- Good option shape:
  SPT: best if you want tighter coaching, programming, and nutrition support.
  Group classes: best if you want routine, fresh air, and a lower-pressure start.
  Free trial: easiest way to see if the vibe works.
- Keep Nick's voice: natural, a little dry, practical, warm. Sound like a coach texting between sessions, not a brochure.
- Use humour the way Nick does: dry, referential, slightly nerdy, Australian. One light joke or odd phrase is good; trying too hard is not.
- Robo-Nick is self-aware automation. It can casually admit Real Nick is coaching, asleep, under a kettlebell, or near coffee, but only when that helps the moment.
- Avoid long setup paragraphs before the useful answer. One quick human reaction is enough.
- Do not use Markdown formatting. No **bold**, no headings, no dense bullet walls.
- If the user asks for "types", "options", or "what you do", do not list everything. Group the answer into 3-4 simple lines and invite them to pick a path.
- If you mention prices, options, or comparisons, put each item on its own line instead of hiding it inside a paragraph.
- Treat the avatar routing doc as operating logic:
  Desk-bound / nervous starters: warm, plain language, reassuring, no Crom or nerd references on first contact.
  Serious strength seekers: more specific and confident, references are fine, Crom is fair game.
  Longevity / midlife movers: serious and capable, no aesthetic language, no patronising tone.
  SPT prospects: position SPT / Kickstarter clearly and confidently.
  YTP parents: warm, parent-respectful, safety-forward.
- For workout/class type questions, answer with training styles first, not product names: strength, conditioning/HiiT/run, bootcamp/group sessions, plus kids/YTP only if relevant. Do not describe YTP as a generic adult long-term plan; it is the youth program.
- Do not sign messages with "Robo-Nick". The widget already shows who is speaking.
- Do not paste links/phone/email unless the user is ready to book, asks for contact details, or shares contact details.
- Make replies easy to scan on a phone
- Prefer this structure when it fits: quick reaction, direct answer, then one simple next step or question
- Use line breaks naturally so each idea has room
- If you list options, each option should be one short line with a simple dash, no bold labels, and usually no more than 10 words before the explanation ends. Prefer 3 options; 4 is the absolute max.
- Vary sentence structure, avoid repeating the same openings or closings
- Do not always end with a CTA, sometimes a simple helpful answer is better
- Ask at most one question at a time unless the user clearly wants to move fast
- If the user says "idk", "not sure", or gives a vague/low-effort answer, do not say generic assistant phrases like "I'm here to help with whatever you need". Narrow the path for them in a casual way: ask whether this is for them, their kid, prices, or trying a first class.
- If they sound hesitant, reassure them naturally without over-selling
- If they sound motivated, match that energy
- If they mention goals, injuries, schedule, confidence, weight loss, strength, routine, nerves, embarrassment, or inconsistency, respond directly to that before pitching anything
- If they ask something odd, playful, skeptical, or slightly off-track, answer it like a calm human and then gently steer back if appropriate
- If someone gives a curve ball, do not ignore it and do not snap back into a script immediately
- If they mention a physical limitation or injury, be encouraging without making medical claims
- If you do not know an exact detail like pricing or timetable, be honest and guide them to the free intro class for specifics
- Never invent facts outside the knowledge base
- Never mention being an AI unless directly asked
- Use emojis occasionally and lightly, around 1 small emoji in some replies, not every reply
- Emojis should feel conversational and friendly, like 👍 💪 🙂 🙌, not cheesy or overdone
- Avoid canned phrases like 'I'd love to help', 'great question', or 'book now' unless they genuinely fit
- Also avoid generic chatbot filler like 'I'm here to help', 'how can I assist', or 'what do you need help with today'. Sound like Nick's useful front-desk helper, not SaaS support.
- Use the brand references as seasoning, not wallpaper. Crom, Conan, Tolkien, Princess Bride, RPG/dungeon jokes, and Inner West specifics are all fair game when they fit naturally.
- Never force a joke into a sensitive, medical, or hesitant moment. Warmth and clarity beat cleverness.
- Avoid sounding too polished; a slightly natural spoken tone is better than perfect marketing copy
- If the user is joking, uncertain, drunk, flirty, embarrassed, forgetful, or changing topic, stay steady and reply like a real person would
- If contact details are shared, acknowledge them and say the team can follow up; do not pretend an external booking/CRM action already happened.
- Use this trial/contact destination when needed: {TRIAL_LINK}; human contact: {HUMAN_EMAIL} / {HUMAN_PHONE}

Style examples:
- If someone says they are nervous or unfit, respond like: 'Totally fair. A lot of people start in that exact spot, and the sessions can be adjusted to your level.'
- If someone asks a practical question, answer it first instead of forcing qualification.
- If someone says something weird like 'Does it involve nudity?', lightly acknowledge it and answer without sounding offended or robotic.
- If someone says they are missing a limb or have a serious limitation, respond supportively and focus on adaptation, not hype.
- If someone is clearly interested, guide them toward the free intro class in a low-pressure way.
- Good formatting example:
  Totally fair, and you definitely wouldn't be the only one feeling that way 🙂

  Most people start before they feel "ready", and sessions can be adjusted to your level.

  If you want, I can also explain how the free intro works.
"""


def keyword_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9']{3,}", text.lower())
        if token not in STOPWORDS
    }


def relevant_source_context(message: str, session_id: str, limit: int = 5) -> str:
    """Small local retrieval layer over Nicholas's docs/curated KB."""
    history = load_conversation(session_id)[-8:]
    query = "\n".join([m.get("content", "") for m in history if m.get("role") == "user"] + [message])
    tokens = keyword_tokens(query)
    if not tokens:
        tokens = {"outdoor", "squad", "trial", "fitness", "start"}

    scored: list[tuple[int, dict]] = []
    for chunk in SOURCE_CHUNKS:
        chunk_tokens = keyword_tokens(chunk["text"])
        score = len(tokens & chunk_tokens)
        # Bias key evergreen context into every answer without making the prompt huge.
        title = chunk["title"].lower()
        if "bot-faq" in title and score:
            score += 3
        if "brand voice" in title and score:
            score += 2
        if "offer" in title and any(t in tokens for t in {"price", "cost", "trial", "spt", "kickstarter", "membership", "pt"}):
            score += 4
        if "avatar" in title and any(t in tokens for t in {"nervous", "kid", "daughter", "son", "strength", "weight", "routine", "busy"}):
            score += 3
        if score:
            scored.append((score, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)
    selected = scored[:limit] or [(0, SOURCE_CHUNKS[0])] if SOURCE_CHUNKS else []
    for required in ("brand voice guide", "bot avatar routing"):
        if any(required in chunk["title"].lower() for _, chunk in selected):
            continue
        for chunk in SOURCE_CHUNKS:
            if required in chunk["title"].lower():
                selected.append((0, chunk))
                break
    return "\n\n---\n\n".join(
        f"Source: {chunk['title']}\n{chunk['text'][:1400]}" for _, chunk in selected
    )


def build_agent_messages(message: str, session_id: str) -> list[dict]:
    context = relevant_source_context(message, session_id)
    source_prompt = f"""Relevant Outdoor Squad source context for this reply:
{context}

Now answer the user's latest message naturally as Robo-Nick. Use the source context, the conversation history, and the user's tone. If the source context does not contain an exact answer, say so briefly and route to a free trial or human follow-up instead of inventing."""
    recent = load_conversation(session_id)[-16:]
    return [
        {"role": "system", "content": BASE_AGENT_PROMPT},
        {"role": "system", "content": source_prompt},
    ] + recent


def build_openai_request_params(message: str, session_id: str) -> dict:
    model = os.environ.get("OUTDOOR_SQUAD_OPENAI_MODEL", "gpt-5-mini")
    params = {
        "model": model,
        "messages": build_agent_messages(message, session_id),
    }
    if model.startswith("gpt-5"):
        # GPT-5 chat models use max_completion_tokens and only support the
        # default sampling settings in the Chat Completions API.
        params["max_completion_tokens"] = 1200
    else:
        params.update({
            "max_tokens": 360,
            "temperature": 0.82,
            "presence_penalty": 0.45,
            "frequency_penalty": 0.35,
        })
    return params


def generate_openai_reply(message: str, session_id: str) -> str:
    response = get_client().chat.completions.create(
        **build_openai_request_params(message, session_id)
    )
    reply = clean_agent_reply(response.choices[0].message.content)
    if not reply:
        raise RuntimeError("OpenAI returned an empty cleaned reply")
    return reply


def build_gemini_payload(message: str, session_id: str) -> dict:
    messages = build_agent_messages(message, session_id)
    system_text = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
    contents = []
    for item in messages:
        role = item.get("role")
        if role == "system":
            continue
        contents.append({
            "role": "model" if role == "assistant" else "user",
            "parts": [{"text": item.get("content", "")}],
        })

    return {
        "systemInstruction": {"parts": [{"text": system_text}]},
        "contents": contents,
        "generationConfig": {
            "temperature": 0.82,
            "maxOutputTokens": 360,
        },
    }


def generate_gemini_reply(message: str, session_id: str) -> str:
    api_key = os.environ.get("OUTDOOR_SQUAD_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Gemini API key not configured")

    model = os.environ.get("OUTDOOR_SQUAD_GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    body = json.dumps(build_gemini_payload(message, session_id)).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:180]
        raise RuntimeError(f"Gemini backend error {exc.code}: {detail}") from exc

    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "\n".join(part.get("text", "") for part in parts if part.get("text"))
    if not text.strip():
        raise RuntimeError("Gemini returned an empty reply")
    reply = clean_agent_reply(text)
    if not reply:
        raise RuntimeError("Gemini returned an empty cleaned reply")
    return reply


def generate_ai_reply(message: str, session_id: str) -> tuple[str, str]:
    errors = []
    for provider in configured_ai_providers():
        for attempt in range(2):
            try:
                if provider == "openai":
                    return generate_openai_reply(message, session_id), "openai"
                if provider == "gemini":
                    return generate_gemini_reply(message, session_id), "gemini"
            except Exception as exc:
                errors.append(f"{provider} attempt {attempt + 1}: {str(exc)[:120]}")
                if attempt == 0:
                    time.sleep(0.8)
    raise RuntimeError("; ".join(errors) or "AI API key not configured")


def clean_agent_reply(reply: str | None) -> str:
    """Keep chat output readable inside a small website bubble."""
    text = (reply or "").strip()
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    text = text.replace("**", "")
    text = re.sub(r"^(great|good) question[!.,]?\s*", "", text, flags=re.IGNORECASE)
    text = text.replace("•", "\n- ")
    text = re.sub(r"^[*-]\s*", "- ", text, flags=re.MULTILINE)
    text = re.sub(
        r"\s+(Training styles:|Pricing highlights:|Options:|Quick summary:|SPT:|Group classes:|Free trial:|Free meal plan:)",
        r"\n\n\1",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?<!\n)(Which option|What kind of injury|What(?:'|’)s the main thing|What are you mainly looking for)",
        r"\n\n\1",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return format_reply_for_chat(text)


def split_long_paragraph(paragraph: str) -> list[str]:
    sentence_like = re.split(r"(?<=[.!?])\s+", paragraph.strip())
    sentence_like = [part.strip() for part in sentence_like if part.strip()]
    if len(sentence_like) <= 1:
        return [paragraph.strip()]

    chunks: list[str] = []
    current: list[str] = []
    for sentence in sentence_like:
        current.append(sentence)
        joined = " ".join(current)
        if len(joined) >= 120 or len(current) >= 2:
            chunks.append(joined)
            current = []
    if current:
        chunks.append(" ".join(current))
    return chunks


def format_reply_for_chat(text: str) -> str:
    option_labels = [
        "SPT:",
        "Group classes:",
        "Free trial:",
        "Free meal plan:",
        "Training styles:",
        "Pricing highlights:",
        "Options:",
        "Quick breakdown:",
        "Quick version:",
    ]
    label_pattern = "|".join(re.escape(label) for label in option_labels)
    text = re.sub(r"\s+(?=(" + label_pattern + r"))", "\n\n", text)
    text = re.sub(r"(Short version:)\s*", r"\n\n\1 ", text, flags=re.IGNORECASE)
    text = re.sub(r"([A-Za-z][A-Za-z /']+?:)\s*-\s+", r"\1\n- ", text)

    blocks: list[str] = []
    for raw_block in re.split(r"\n{2,}", text):
        block = raw_block.strip()
        if not block:
            continue
        if block.count(" - ") >= 2:
            block = re.sub(r"\s-\s+", "\n- ", block)
        if "\n" in block:
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            blocks.extend(lines)
            continue
        if block.startswith("- "):
            blocks.append(block)
            continue
        if len(block) > 140:
            blocks.extend(split_long_paragraph(block))
            continue
        blocks.append(block)

    cleaned_blocks: list[str] = []
    for block in blocks:
        if block.startswith("- "):
            cleaned_blocks.append(block)
            continue
        cleaned_blocks.append(block.strip())

    return "\n\n".join(block for block in cleaned_blocks if block).strip()


def recent_assistant_message(session_id: str) -> str:
    for item in reversed(load_conversation(session_id)):
        if item.get("role") == "assistant":
            return item.get("content", "")
    return ""


def contextual_short_reply(message: str, session_id: str) -> str | None:
    clean = normalise_chat_text(message)
    previous = recent_assistant_message(session_id).lower()
    if clean in {"no", "nope", "nah", "none"}:
        if any(
            phrase in previous
            for phrase in [
                "what kind of injury are you working around",
                "injuries or limitations",
                "limitations the coach should know",
                "old injury",
                "recent injury",
                "long-term niggle",
                "working around",
                "injury",
                "limitation",
                "knee",
            ]
        ):
            return (
                "Sweet — no injuries or limitations to flag.\n\n"
                "That keeps it straightforward for the coach.\n\n"
                "Are you leaning more towards a free trial, regular group training, or something more coached like SPT?"
            )
    if clean in {"yes", "yep", "yeah", "sure"}:
        if "want me to explain what usually happens in a first session" in previous:
            return (
                "Usually it’s pretty simple.\n\n"
                "You turn up, meet the coach, get a feel for the session, and they adjust things to your level rather than expecting you to keep up with everyone straight away.\n\n"
                "If you want, I can also point you to the best location to start with."
            )
    return None


def should_use_outage_fallback(message: str) -> bool:
    text = message.lower()
    keyword_groups = [
        ["free intro", "trial", "free class", "intro class"],
        ["price", "cost", "how much", "membership", "casual", "drop-in", "drop in"],
        ["spt", "semi-private", "semi private", "personal training", "kickstarter", "pt"],
        ["kid", "kids", "child", "son", "daughter", "teen", "young", "ytp"],
        ["unfit", "beginner", "nervous", "embarrassed"],
        ["injury", "injured", "limitation", "bad knee", "back pain", "shoulder"],
        ["food", "nutrition", "meal", "diet", "weight loss"],
        ["where", "camperdown", "redfern", "parking", "public transport"],
    ]
    return any(any(word in text for word in group) for group in keyword_groups)

# In-memory conversation store (per session)
conversations: dict[str, list] = {}


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """Protect owner-only leads, metrics, and conversation review surfaces."""
    if not ADMIN_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin password is not configured on this deployment.",
        )
    username_ok = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    password_ok = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    message = body.get("message", "").strip()
    session_id = body.get("session_id", "default")

    if not message:
        return JSONResponse({"error": "No message provided"}, status_code=400)

    # Get or create conversation history
    history = load_conversation(session_id)
    is_new_conversation = len(history) == 0
    if is_new_conversation:
        log_event("conversation_started", session_id=session_id)

    history.append({"role": "user", "content": message})
    persist_conversation(session_id)
    log_chat_message(session_id, "user", message)
    log_event(
        "message_received",
        session_id=session_id,
        route=classify_route(message.lower()),
        message_length=len(message),
    )

    if should_use_local_tone_handler(message, session_id):
        reply = demo_fallback_reply(message, session_id=session_id)
        history.append({"role": "assistant", "content": reply})
        persist_conversation(session_id)
        log_chat_message(session_id, "assistant", reply)

        lead_info = extract_lead_info(message, session_id)
        if lead_info:
            save_lead(lead_info)
            log_event("lead_captured", **lead_info)

        log_event("local_tone_handler_used", session_id=session_id)
        log_bot_reply(session_id, reply, fallback=False)
        return JSONResponse({
            "reply": reply,
            "session_id": session_id,
            "reply_delay_ms": random.randint(MIN_REPLY_DELAY_MS, MAX_REPLY_DELAY_MS),
    })

    try:
        reply, ai_provider = generate_ai_reply(message, session_id)

        history.append({"role": "assistant", "content": reply})
        persist_conversation(session_id)
        log_chat_message(session_id, "assistant", reply)

        # Check if lead info was shared (basic extraction)
        lead_info = extract_lead_info(message, session_id)
        if lead_info:
            save_lead(lead_info)
            log_event("lead_captured", **lead_info)

        reply_delay_ms = random.randint(MIN_REPLY_DELAY_MS, MAX_REPLY_DELAY_MS)
        log_bot_reply(session_id, reply, fallback=False)

        return JSONResponse({
            "reply": reply,
            "session_id": session_id,
            "reply_delay_ms": reply_delay_ms,
            "ai_provider": ai_provider,
        })

    except Exception as exc:
        # The client-ready product is the agent path above. Canned FAQ branches
        # are not the product. Only enable the old deterministic demo fallback
        # deliberately for offline development.
        if os.environ.get("OUTDOOR_SQUAD_ENABLE_DEMO_FALLBACK") == "1" or should_use_outage_fallback(message):
            reply = demo_fallback_reply(message, session_id=session_id)
        else:
            reply = (
                "I’m having trouble reaching the AI backend for a moment. Please try again in a few seconds."
            )
        history.append({"role": "assistant", "content": reply})
        persist_conversation(session_id)
        log_chat_message(session_id, "assistant", reply)

        lead_info = extract_lead_info(message, session_id)
        if lead_info:
            save_lead(lead_info)
            log_event("lead_captured", **lead_info)

        using_demo_fallback = (
            os.environ.get("OUTDOOR_SQUAD_ENABLE_DEMO_FALLBACK") == "1"
            or should_use_outage_fallback(message)
        )
        log_bot_reply(session_id, reply, fallback=using_demo_fallback)

        payload = {
            "reply": reply,
            "session_id": session_id,
            "reply_delay_ms": random.randint(MIN_REPLY_DELAY_MS, MAX_REPLY_DELAY_MS),
            "fallback": using_demo_fallback,
        }
        if os.environ.get("OUTDOOR_SQUAD_DEBUG_ERRORS") == "1":
            payload["backend_error"] = str(exc)[:160]
        return JSONResponse(payload)


@app.post("/api/booking")
async def booking(request: Request):
    """Handle sample flow requests from the public AI Sprints form."""
    body = await request.json()
    name = body.get("name", "Unknown")
    email = body.get("email", "")
    business = body.get("business", "")
    phone = body.get("phone", "")
    role = body.get("role", "")
    notes = body.get("notes", "")

    bookings_file = Path(__file__).parent / "bookings.json"
    if not bookings_file.exists():
        bookings_file.write_text("[]")
    bookings = json.loads(bookings_file.read_text())
    booking_data = {
        "type": "sample_flow_request",
        "name": name,
        "email": email,
        "business": business,
        "phone": phone,
        "role": role,
        "notes": notes,
        "created_at": datetime.now().isoformat(),
    }
    bookings.append(booking_data)
    bookings_file.write_text(json.dumps(bookings, indent=2))
    log_event("sample_flow_request", session_id=body.get("session_id", "public-form"), **booking_data)

    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(
            f"New sample flow request!\n\n"
            f"Name: {name}\n"
            f"Email: {email}\n"
            f"Business: {business}\n"
            f"Phone: {phone}\n"
            f"Role: {role}\n"
            f"Notes: {notes}\n\n"
            f"— AI Sprints sample-first form"
        )
        msg["Subject"] = f"New sample request: {name} ({business or 'No business'})"
        msg["From"] = "bookings@aisprints.com.au"
        msg["To"] = "jacowilmjr@agentmail.to"
        # Best effort — don't fail the request if email setup is unavailable
    except Exception:
        pass

    return JSONResponse({"ok": True, "message": "Sample request received"})


@app.get("/api/leads")
async def get_leads(_: str = Depends(require_admin)):
    """Admin endpoint to view captured leads"""
    return JSONResponse(read_leads())


@app.get("/api/leads.csv")
async def export_leads_csv(_: str = Depends(require_admin)):
    """Admin CSV export for captured leads."""
    leads = read_leads()
    columns = [
        "timestamp",
        "name",
        "email",
        "phone",
        "route",
        "location_preference",
        "time_preference",
        "concerns",
        "handoff_summary",
        "raw_message",
        "session_id",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for lead in leads:
        row = dict(lead)
        if isinstance(row.get("concerns"), list):
            row["concerns"] = "; ".join(row["concerns"])
        writer.writerow(row)
    return Response(
        output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="outdoor-squad-leads.csv"'},
    )


@app.post("/api/event")
async def track_event(request: Request):
    """Lightweight widget analytics for Nicholas/Lyn's weekly review."""
    body = await request.json()
    event_type = str(body.get("event_type", "widget_event"))[:80]
    session_id = str(body.get("session_id", "unknown"))[:120]
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    log_event(event_type, session_id=session_id, **metadata)
    return JSONResponse({"ok": True})


def build_metrics_payload() -> dict:
    events = read_events()
    conversations_started = {e.get("session_id") for e in events if e.get("event_type") == "conversation_started"}
    sessions_with_messages = {e.get("session_id") for e in events if e.get("event_type") == "message_received"}
    sessions_with_completion = {
        e.get("session_id")
        for e in events
        if e.get("event_type") in {
            "lead_captured",
            "booking_link_shown",
            "human_handoff_suggested",
        }
    }
    leads = read_leads()
    route_counts: dict[str, int] = {}
    outcome_counts: dict[str, int] = {
        "lead_captured": 0,
        "booking_link_shown": 0,
        "human_handoff_suggested": 0,
        "local_tone_handler_used": 0,
        "fallback_reply_used": 0,
    }
    for event in events:
        route = event.get("route")
        if route:
            route_counts[route] = route_counts.get(route, 0) + 1
        if event.get("event_type") in outcome_counts:
            outcome_counts[event["event_type"]] += 1

    return {
        "conversations_started": len(conversations_started),
        "conversations_with_user_messages": len(sessions_with_messages),
        "completion_rate": safe_rate(len(sessions_with_completion), len(conversations_started)),
        "dropoff_rate": safe_rate(len(conversations_started - sessions_with_completion), len(conversations_started)),
        "leads_captured": len(leads),
        "route_counts": route_counts,
        "outcomes": outcome_counts,
        "last_event_at": events[-1]["timestamp"] if events else None,
        "note": "Supabase-backed owner analytics; local files remain fallback only.",
    }


@app.get("/api/metrics")
async def get_metrics(_: str = Depends(require_admin)):
    """Simple success metrics for the paid first version."""
    return JSONResponse(build_metrics_payload())


@app.get("/api/conversation-logs")
async def get_conversation_logs(limit: int = 200, _: str = Depends(require_admin)):
    """Owner-only redacted transcript review for 30/60/90 day quality checks."""
    logs = read_conversation_logs()
    return JSONResponse(logs[-max(1, min(limit, 1000)):])


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(_: str = Depends(require_admin)):
    """Small protected owner dashboard for Square-era operations."""
    admin_data = {
        "metrics": build_metrics_payload(),
        "leads": read_leads(),
        "logs": read_conversation_logs()[-120:],
    }
    return HTMLResponse(ADMIN_HTML.replace("__ADMIN_DATA__", json.dumps(admin_data)))


@app.get("/api/health")
async def health():
    """Deployment health check without exposing secret values."""
    api_key_sources = []
    if os.environ.get("OUTDOOR_SQUAD_OPENAI_API_KEY"):
        api_key_sources.append("OUTDOOR_SQUAD_OPENAI_API_KEY")
    elif os.environ.get("OPENAI_API_KEY"):
        api_key_sources.append("OPENAI_API_KEY")
    if os.environ.get("OUTDOOR_SQUAD_GEMINI_API_KEY"):
        api_key_sources.append("OUTDOOR_SQUAD_GEMINI_API_KEY")
    elif os.environ.get("GEMINI_API_KEY"):
        api_key_sources.append("GEMINI_API_KEY")
    providers = configured_ai_providers()
    review_hosted = DEPLOYMENT_MODE == "review"
    handoff_ready = (
        DEPLOYMENT_MODE == "handoff"
        and bool(providers)
        and bool(ADMIN_PASSWORD)
        and TRIAL_LINK != "https://www.outdoorsquad.com.au"
        and any(source.startswith("OUTDOOR_SQUAD_") for source in api_key_sources)
    )

    return JSONResponse({
        "ok": True,
        "deployment_mode": DEPLOYMENT_MODE,
        "review_hosted_by_ai_sprints": review_hosted,
        "handoff_ready": handoff_ready,
        "storage_backend": "supabase" if supabase_enabled() else "local_files",
        "supabase_configured": supabase_enabled(),
        "ai_configured": bool(providers),
        "ai_provider": providers[0] if providers else None,
        "ai_providers": providers,
        "api_key_source": api_key_sources[0] if api_key_sources else None,
        "api_key_sources": api_key_sources,
        "model": os.environ.get("OUTDOOR_SQUAD_OPENAI_MODEL", "gpt-5-mini"),
        "gemini_model": os.environ.get("OUTDOOR_SQUAD_GEMINI_MODEL", "gemini-2.5-flash"),
        "admin_configured": bool(ADMIN_PASSWORD),
        "trial_link_configured": TRIAL_LINK != "https://www.outdoorsquad.com.au",
        "human_email": HUMAN_EMAIL,
        "human_phone": HUMAN_PHONE,
        "source_chunks": len(SOURCE_CHUNKS),
    })


@app.get("/", response_class=HTMLResponse)
async def serve_demo():
    """Serve the demo chat widget page"""
    html_path = Path(__file__).parent / "demo.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<h1>Outdoor Squad Bot Demo</h1><p>demo.html not found</p>")


@app.get("/widget.js")
async def serve_widget():
    """Serve the embeddable widget JS"""
    js_path = Path(__file__).parent / "widget.js"
    if js_path.exists():
        return Response(content=js_path.read_text(), media_type="application/javascript")
    return Response(content="console.error('widget.js not found')", media_type="application/javascript")


@app.get("/widget-preview", response_class=HTMLResponse)
async def widget_preview():
    """Serve a simple page that mounts the embeddable widget."""
    html_path = Path(__file__).parent / "widget_preview.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<h1>Widget preview not found</h1>", status_code=404)


def should_use_local_tone_handler(message: str, session_id: str) -> bool:
    """Catch moments that need stateful tone more than generic AI/Q&A."""
    text = normalise_chat_text(message)
    if contextual_short_reply(message, session_id):
        return True
    if has_contact_details(message):
        return True
    if is_vague_message(text):
        return True
    if is_obvious_boundary_joke(text):
        return True
    if any(word in text for word in ["nutrition", "meal", "diet", "weight loss", "lose weight"]):
        return True

    # If the user repeats the same short message, answer the behaviour rather
    # than pretending it is a fresh FAQ.
    user_messages = [m["content"] for m in load_conversation(session_id) if m.get("role") == "user"]
    short_repeats = [normalise_chat_text(m) for m in user_messages if len(normalise_chat_text(m)) <= 18]
    return len(short_repeats) >= 2 and short_repeats[-1] == short_repeats[-2]


def has_contact_details(message: str) -> bool:
    return bool(
        re.search(r'(?:04\d{2}[\s-]?\d{3}[\s-]?\d{3}|\+?61\s?4\d{2}[\s-]?\d{3}[\s-]?\d{3})', message)
        or re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', message)
    )


def normalise_chat_text(message: str) -> str:
    return re.sub(r"\s+", " ", message.lower().strip().strip(".!?…"))


def is_vague_message(text: str) -> bool:
    vague = {
        "idk", "i dont know", "i don't know", "dunno", "not sure", "unsure",
        "maybe", "no idea", "hmm", "uh", "umm", "whatever", "?", "help",
    }
    short_but_meaningful = {"spt", "pt", "no", "nope", "nah", "yes", "yep"}
    return text in vague or (len(text) <= 3 and text not in short_but_meaningful)


def is_obvious_boundary_joke(text: str) -> bool:
    return any(word in text for word in ["nudity", "nude", "naked", "army yelling", "yelling", "drill sergeant"])


def demo_fallback_reply(message: str, session_id: str = "default") -> str:
    """Deterministic replies for the core Outdoor Squad demo paths."""
    text = message.lower()
    clean = normalise_chat_text(message)

    contextual_reply = contextual_short_reply(message, session_id)
    if contextual_reply:
        return contextual_reply

    if is_vague_message(clean):
        vague_count = sum(
            1
            for m in load_conversation(session_id)
            if m.get("role") == "user" and is_vague_message(normalise_chat_text(m.get("content", "")))
        )
        if vague_count <= 1:
            return (
                "Fair. Easiest place to start: is this for you, your kid, or are you just checking prices?"
            )
        if vague_count == 2:
            return (
                "Still in the fog. No drama — pick the least-wrong one: trial class, personal training, kids training, or prices?"
            )
        return (
            "All good, I’ll stop throwing the brochure at you. If you want the shortest path: book a free trial, or send your name + mobile and real Nick/Lyn can point you the right way."
        )

    if is_obvious_boundary_joke(clean):
        return (
            "Haha, no — clothes stay on and there’s no army yelling.\n\n"
            "The sessions are coached, but the vibe is supportive rather than shouty. You’ll work hard, just without the weird bootcamp theatre.\n\n"
            "Are you asking because group training sounds intimidating, or just checking the danger level?"
        )

    if re.search(r'(?:04\d{2}[\s-]?\d{3}[\s-]?\d{3}|\+?61\s?4\d{2}[\s-]?\d{3}[\s-]?\d{3})', message) or re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', message):
        name = extract_contact_name(message, session_id=session_id)
        intro = f"Perfect — I’ve got those contact details, {name.split()[0]}." if name else "Perfect — I’ve got those contact details."
        follow_up = (
            "The team can use that to follow up about the best free intro, SPT, or coach-call option for you."
            if name
            else "If you haven’t already, send your first name too so Nick or Lyn know who they’re replying to."
        )
        return (
            f"{intro}\n\n"
            f"{follow_up}\n\n"
            "Before they reach out, what’s the main thing you want help with: fitness, weight loss, routine, or confidence getting started?"
        )

    if any(word in text for word in ["where", "camperdown", "redfern", "parking", "bus", "public transport", "meet"]):
        if "redfern" in text:
            return (
                "Redfern sessions meet around Redfern Park — usually near the Park Café at the Sports Oval end, or undercover behind the café if the weather is being dramatic.\n\n"
                "There’s parking on Chalmers St and underground at Woolworths, and Redfern Station is about 700m away.\n\n"
                "Are you looking at Redfern specifically, or comparing it with Camperdown?"
            )
        if "camperdown" in text:
            return (
                "Camperdown sessions meet at Camperdown Tennis, around The Barracks / Camperdown Oval.\n\n"
                "Parking is usually around Australia St and Mallet St, and buses on Parramatta Rd stop very close by. Newtown Station is about 900m away.\n\n"
                "Are you thinking mornings, evenings, or Saturday?"
            )
        return (
            "There are two main training spots: Camperdown and Redfern.\n\n"
            "Camperdown meets at Camperdown Tennis near the oval. Redfern meets around Redfern Park near the Park Café end. Both have nearby parking and public transport options.\n\n"
                "Which one is closer for you?"
        )

    if any(word in text for word in ["spt", "semi-private", "semi private", "personal training", "pt", "program", "programming", "partner", "mate", "friend and i", "kickstarter"]):
        return (
            "That sounds more like the SPT / 28-Day Kickstarter path than a basic group-class trial.\n\n"
            "SPT is small-group personal training: max 4 people, proper programming, assessment, nutrition support, and group classes included. The 28-Day Kickstarter is the trial version at $397.\n\n"
            "Are you looking for more coaching attention, or mostly a stronger routine?"
        )

    if (
        any(word in text for word in ["training styles", "different training", "conditioning", "hiit", "bootcamp", "strength"])
        and any(word in text for word in ["price", "prices", "cost", "membership", "how much"])
    ):
        return (
            "Quick breakdown:\n\n"
            "Training styles:\n"
            "- Strength-focused sessions for getting stronger and moving better.\n"
            "- Conditioning / HIIT / running work for fitness and endurance.\n"
            "- Group-style sessions if you want a social, coached class.\n"
            "- SPT if you want the most personalised option.\n\n"
            "Pricing highlights:\n"
            "- 1-Day Free Trial Pass: free.\n"
            "- Casual drop-in: $37.\n"
            "- Squad Ascent membership: $51/week for unlimited group classes.\n"
            "- SPT starts from $125/week depending on setup.\n\n"
            "If you want, I can narrow it down based on whether you care more about strength, weight loss, or routine."
        )

    if any(word in text for word in ["price", "cost", "how much", "$", "membership", "cancel", "pause", "contract"]):
        return (
            "Quick version: the main group membership is Squad Ascent at $51/wk for unlimited group classes.\n\n"
            "There’s also a free 1-Day Trial Pass, $37 casual drop-ins, and SPT from $125/wk if you want more personalised coaching.\n\n"
            "Most people start with the free trial so they can see what actually fits before choosing anything."
        )

    if any(word in text for word in ["unfit", "not fit", "not very fit", "beginner", "nervous", "embarrassed"]):
        return (
            "Totally fair — a lot of people start before they feel ready 🙂\n\n"
            "The sessions can be adjusted to your level, so you don't need to turn up already fit.\n\n"
            "Are you mainly looking to build fitness, lose weight, or get back into a routine?"
        )

    if any(word in text for word in ["evening", "full-time", "full time", "after work", "schedule", "availability"]):
        return (
            "That makes sense — most people need something that fits around work.\n\n"
            "The best next step would be a free intro so the team can point you to the right session options.\n\n"
            "Which area are you closest to in the Inner West?"
        )

    if any(word in text for word in ["busy", "inconsistent", "quit", "routine", "motivation", "two weeks"]):
        return (
            "Honestly, that’s a really common pattern — people don’t usually need more willpower, they need something easy to keep showing up to.\n\n"
            "The group structure and coaching can help with consistency because you’re not figuring it all out alone.\n\n"
            "Would evenings, mornings, or weekends be easiest for you to stick with?"
        )

    if any(word in text for word in ["free intro", "trial", "free class", "intro class", "how does"]):
        return (
            "The free intro is the low-pressure way to see if the Squad feels right.\n\n"
            "You can ask questions, get a feel for the coaching style, and work out which sessions suit you.\n\n"
            f"You can start here: {TRIAL_LINK}\n\n"
            "Or send your name and mobile and the team can follow up."
        )

    if any(word in text for word in ["kid", "kids", "child", "son", "daughter", "teen", "young", "ytp"]):
        return (
            "Yep — that’s Young'N'Strong, the youth training programme for kids aged 10–17.\n\n"
            "It’s coached by qualified, WWCC-checked trainers and focuses on safe strength, movement skills, confidence, and a bit of fun — not tiny bootcamp sergeants yelling at children.\n\n"
            "How old is your kid?"
        )

    if any(word in text for word in ["food", "nutrition", "meal", "diet", "weight loss"]):
        return (
            "Yep — if weight loss is the goal, training and food need to stop fighting each other. Annoying, but true.\n\n"
            "SPT: best if you want tighter coaching, programming, nutrition support, and progress tracking.\n"
            "Group classes: best if you want routine, fresh air, and a lower-pressure start.\n"
            "Free meal plan: handy if food is the bit that keeps wobbling.\n\n"
            "Which bit feels like the bigger blocker right now: training consistency or food?"
        )

    if any(word in text for word in ["injury", "injured", "limitation", "bad knee", "back pain", "shoulder"]):
        return (
            "Good thing to flag. An old injury doesn’t automatically make it a bad fit, but it’s worth handling properly.\n\n"
            "The sensible move is to tell the coach what’s going on so they can suggest adjustments and avoid anything silly.\n\n"
            "What kind of injury are you working around?"
        )

    if any(word in text for word in ["group", "not sure", "awkward", "intimidating"]):
        return (
            "Completely understandable. Group training can sound intimidating before you've tried it.\n\n"
            "Outdoor Squad is meant to feel supportive, not hardcore-for-the-sake-of-it. The free intro is a good no-pressure test.\n\n"
            "Want me to explain what usually happens in a first session?"
        )

    return (
        "I can help with that. Outdoor Squad is an outdoor fitness community around Sydney's Inner West, with coaching that can adapt to different fitness levels.\n\n"
        "The usual best next step is a free intro so the team can point you to the right option.\n\n"
        "What are you mainly looking for — fitness, weight loss, routine, or something else?"
    )


def extract_lead_info(message: str, session_id: str) -> dict | None:
    """Capture a follow-up lead only after usable contact details are shared."""
    import re
    info = {}

    # Email
    email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', message)
    if email_match:
        info['email'] = email_match.group()

    # Phone (Australian)
    phone_match = re.search(r'(?:04\d{2}[\s-]?\d{3}[\s-]?\d{3}|\+?61\s?4\d{2}[\s-]?\d{3}[\s-]?\d{3})', message)
    if phone_match:
        info['phone'] = phone_match.group()

    if not info:
        return None

    name = extract_contact_name(message, session_id=session_id)
    if name:
        info['name'] = name

    info['session_id'] = session_id
    info['timestamp'] = datetime.now().isoformat()
    info['raw_message'] = message
    info.update(build_lead_summary(session_id, message))
    return info


def extract_contact_name(message: str, session_id: str = "default") -> str | None:
    """Best-effort name extraction when a visitor drops contact details."""
    history = "\n".join(
        m.get("content", "")
        for m in load_conversation(session_id)
        if m.get("role") == "user"
    )
    explicit_source = "\n".join(part for part in [history, message] if part).strip()
    contact_stripped = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', ' ', explicit_source)
    contact_stripped = re.sub(r'(?:04\d{2}[\s-]?\d{3}[\s-]?\d{3}|\+?61\s?4\d{2}[\s-]?\d{3}[\s-]?\d{3})', ' ', contact_stripped)
    explicit_name = re.search(
        r"\b(?:my name is|name is|this is|call me)\s+([A-Za-z][A-Za-z'-]{1,})(?:\s+([A-Za-z][A-Za-z'-]{1,}))?",
        contact_stripped,
        flags=re.IGNORECASE,
    )
    if explicit_name:
        captured = [part for part in explicit_name.groups() if part]
        return " ".join(captured).title()

    fallback_source = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', ' ', message)
    fallback_source = re.sub(r'(?:04\d{2}[\s-]?\d{3}[\s-]?\d{3}|\+?61\s?4\d{2}[\s-]?\d{3}[\s-]?\d{3})', ' ', fallback_source)
    fallback_source = re.sub(r'\b(name is|my name is|this is|call me)\b', ' ', fallback_source, flags=re.IGNORECASE)
    words = re.findall(r"[A-Za-z][A-Za-z'-]{1,}", fallback_source)
    stop = {
        "and", "email", "phone", "mobile", "number", "thanks", "thank", "cheers",
        "camperdown", "redfern", "trial", "class", "fitness", "please", "my", "is",
        "keen", "interested", "looking", "free", "after", "work", "evening", "evenings",
        "morning", "mornings", "afternoon", "afternoons", "suit", "suits", "me",
        "best", "main", "goal", "goals", "help", "with",
    }
    name_words = [w for w in words if w.lower() not in stop]
    if not name_words:
        return None
    return " ".join(name_words[:2]).title()


def build_lead_summary(session_id: str, latest_message: str = "") -> dict:
    """Create a simple handoff summary for Nick/Lyn from the chat so far."""
    messages = load_conversation(session_id)
    user_texts = [m["content"] for m in messages if m.get("role") == "user"]
    joined = "\n".join(user_texts + ([latest_message] if latest_message else []))
    lower = joined.lower()

    route = classify_route(lower)
    location = "Camperdown" if "camperdown" in lower else "Redfern" if "redfern" in lower else "unknown"
    time_pref = "evening" if any(x in lower for x in ["evening", "after work", " pm", "6:30"] ) else "morning" if any(x in lower for x in ["morning", "6am", "6:00", "9:30"]) or re.search(r"\b\d{1,2}\s?am\b", lower) else "unknown"

    concerns = []
    for label, words in {
        "beginner/nervous": ["unfit", "not fit", "beginner", "nervous", "embarrassed", "starting"],
        "injury/limitation": ["injury", "injured", "knee", "back", "shoulder", "pain", "pregnant", "postnatal"],
        "schedule": ["busy", "schedule", "full-time", "full time", "after work", "availability"],
        "price": ["price", "cost", "how much", "membership", "$"],
        "child/youth": ["kid", "child", "son", "daughter", "teen", "ytp"],
    }.items():
        if any(word in lower for word in words):
            concerns.append(label)

    return {
        "route": route,
        "location_preference": location,
        "time_preference": time_pref,
        "concerns": concerns,
        "handoff_summary": f"Route: {route}; location: {location}; time: {time_pref}; concerns: {', '.join(concerns) or 'none captured'}",
    }


def classify_route(text: str) -> str:
    if any(word in text for word in ["kid", "kids", "child", "son", "daughter", "teen", "ytp", "young'n'strong"]):
        return "YTP / parent enquiry"
    if any(word in text for word in ["spt", "semi-private", "semi private", "personal training", "pt", "program", "programming", "partner", "kickstarter", "hyrox", "powerlifting", "crossfit"]):
        return "SPT / 28-Day Kickstarter"
    if any(word in text for word in ["casual", "drop-in", "drop in", "visiting"]):
        return "casual drop-in"
    if any(word in text for word in ["human", "nick", "call me", "talk to someone", "medical", "rehab", "pregnant", "postnatal"]):
        return "human handoff"
    return "1-Day Free Trial Pass"


def log_bot_reply(session_id: str, reply: str, fallback: bool = False):
    """Record outcomes that matter for Nicholas/Lyn's revenue review."""
    lower = reply.lower()
    if fallback:
        log_event("fallback_reply_used", session_id=session_id)
    if TRIAL_LINK.lower() in lower:
        log_event("booking_link_shown", session_id=session_id)
    if (
        HUMAN_EMAIL.lower() in lower
        or HUMAN_PHONE in reply
        or "team can follow up" in lower
        or "team can use that to follow up" in lower
        or "nick or lyn" in lower
        or "saved your details" in lower
    ):
        log_event("human_handoff_suggested", session_id=session_id)
    log_event("bot_reply_sent", session_id=session_id, reply_length=len(reply))


def log_event(event_type: str, session_id: str = "unknown", **metadata):
    """Append one JSONL analytics event without storing full chat transcripts."""
    safe_metadata = {
        key: value
        for key, value in metadata.items()
        if key not in {"raw_message"} and value not in (None, "")
    }
    event = {
        "timestamp": now_iso(),
        "event_type": event_type,
        "session_id": session_id,
        **safe_metadata,
    }
    if supabase_enabled():
        try:
            supabase_request(
                "POST",
                SUPABASE_TABLES["events"],
                json_body={
                    "timestamp": event["timestamp"],
                    "event_type": event_type,
                    "session_id": session_id,
                    "metadata": safe_metadata,
                },
                prefer="return=minimal",
            )
            return
        except Exception:
            pass
    append_jsonl_file(EVENTS_FILE, event)


def safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 3)


def redact_contact(text: str) -> str:
    text = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', '[email]', text)
    text = re.sub(r'(?:04\d{2}[\s-]?\d{3}[\s-]?\d{3}|\+?61\s?4\d{2}[\s-]?\d{3}[\s-]?\d{3})', '[phone]', text)
    return text


def log_chat_message(session_id: str, role: str, content: str):
    """Store redacted conversation text for protected quality review."""
    event = {
        "timestamp": now_iso(),
        "session_id": session_id,
        "role": role,
        "content": redact_contact(content)[:1600],
    }
    if supabase_enabled():
        try:
            supabase_request(
                "POST",
                SUPABASE_TABLES["conversation_logs"],
                json_body=event,
                prefer="return=minimal",
            )
            return
        except Exception:
            pass
    append_jsonl_file(CONVERSATION_LOG_FILE, event)


def save_lead(lead_info: dict):
    """Save lead to Supabase when configured, otherwise local JSON."""
    normalized = dict(lead_info)
    normalized.setdefault("concerns", [])
    if supabase_enabled():
        try:
            supabase_request(
                "POST",
                SUPABASE_TABLES["leads"],
                json_body=normalized,
                prefer="return=minimal",
            )
            return
        except Exception:
            pass
    leads = read_json_array_file(LEADS_FILE)
    leads.append(normalized)
    LEADS_FILE.write_text(json.dumps(leads, indent=2))


ADMIN_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Outdoor Squad Bot Admin</title>
  <style>
    :root { --black: #000000; --charcoal: #39383d; --orange: #f26522; --orange-dark: #ea510a; --light: #e0e0e0; --paper: #ffffff; --ink: #000000; --muted: #4f4f4f; --border: #d8d8d8; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: linear-gradient(180deg, rgba(242,101,34,.10), rgba(246,246,246,.82) 42%), #f6f6f6; color: var(--ink); }
    header { background: radial-gradient(circle at top left, rgba(242,101,34,.42), transparent 34%), linear-gradient(135deg, var(--black), var(--charcoal)); color: white; padding: 20px 24px; border-bottom: 5px solid var(--orange); }
    header h1 { font-size: 1.25rem; margin: 0 0 4px; letter-spacing: 0; }
    header p { margin: 0; opacity: .86; font-size: .9rem; }
    header a { color: white; text-underline-offset: 3px; }
    main { max-width: 1120px; margin: 0 auto; padding: 18px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; }
    .card { background: var(--paper); border: 1px solid var(--border); border-radius: 8px; padding: 14px; box-shadow: 0 6px 20px rgba(0,0,0,.08); }
    .metric { color: var(--orange-dark); font-size: 1.8rem; font-weight: 800; margin-top: 8px; }
    h2 { color: var(--black); font-size: 1rem; margin: 24px 0 10px; }
    pre { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--paper); border: 1px solid var(--border); border-radius: 8px; padding: 12px; max-height: 420px; overflow: auto; line-height: 1.45; }
    table { width: 100%; border-collapse: collapse; background: var(--paper); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
    th, td { text-align: left; padding: 10px; border-bottom: 1px solid #eeeeee; vertical-align: top; font-size: .9rem; }
    th { background: #f3f3f3; color: var(--black); border-top: 3px solid var(--orange); }
    td:first-child, td:nth-child(2) { white-space: nowrap; }
    .muted { color: var(--muted); font-size: .86rem; }
    .toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 18px 0 4px; flex-wrap: wrap; }
    .toolbar-meta { color: var(--muted); font-size: .84rem; }
    .toolbar-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .section-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 24px 0 10px; }
    .section-head h2 { margin: 0; }
    .button { display: inline-block; background: linear-gradient(135deg, var(--orange-dark), var(--orange)); color: white; border: 0; border-radius: 999px; padding: 8px 13px; font-size: .86rem; font-weight: 700; text-decoration: none; white-space: nowrap; }
    .button:hover { filter: brightness(.96); }
  </style>
</head>
<body>
  <header>
    <h1>Outdoor Squad Bot Admin</h1>
    <p>Protected owner view for leads, success metrics, and conversation review.</p>
  </header>
  <main>
    <div class="toolbar">
      <div class="toolbar-meta" id="lastUpdated">Loading latest data…</div>
      <div class="toolbar-actions">
        <a class="button" href="/admin">Refresh Data</a>
      </div>
    </div>
    <section class="grid" id="metrics"></section>
    <div class="section-head">
      <h2>Captured Leads</h2>
      <a class="button" href="/api/leads.csv">Export CSV</a>
    </div>
    <div id="leads" class="muted">Loading...</div>
    <h2>Recent Redacted Conversation Log</h2>
    <pre id="logs">Loading...</pre>
  </main>
  <script>
    window.__OS_ADMIN_DATA__ = __ADMIN_DATA__;
    function metric(label, value) {
      return '<div class="card"><div class="muted">' + label + '</div><div class="metric">' + value + '</div></div>';
    }
    function pct(value) {
      return Math.round((Number(value) || 0) * 100) + '%';
    }
    function esc(value) {
      return String(value || '').replace(/[&<>"']/g, function(char) {
        return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]);
      });
    }
    function boot() {
      const data = window.__OS_ADMIN_DATA__ || {};
      const metrics = data.metrics || { outcomes: {} };
      const leads = data.leads || [];
      const logs = data.logs || [];
      const lastUpdated = metrics.last_event_at || new Date().toISOString();
      document.getElementById('lastUpdated').textContent = 'Last updated: ' + lastUpdated + ' — auto-refreshes every 15s';
      document.getElementById('metrics').innerHTML = [
        metric('Conversations started', metrics.conversations_started),
        metric('Completion rate', pct(metrics.completion_rate)),
        metric('Drop-off rate', pct(metrics.dropoff_rate)),
        metric('Leads captured', metrics.leads_captured),
        metric('Handoffs suggested', metrics.outcomes.human_handoff_suggested),
        metric('Booking links shown', metrics.outcomes.booking_link_shown)
      ].join('');
      document.getElementById('leads').innerHTML = leads.length ? (
        '<table><thead><tr><th>Time</th><th>Contact</th><th>Route</th><th>Context</th></tr></thead><tbody>' +
        leads.slice().reverse().map(function(lead) {
          return '<tr><td>' + esc(lead.timestamp) + '</td><td>' + esc(lead.email || lead.phone) + '</td><td>' + esc(lead.route) + '</td><td>' + esc(lead.handoff_summary) + '</td></tr>';
        }).join('') +
        '</tbody></table>'
      ) : 'No leads captured yet.';
      document.getElementById('logs').textContent = logs.map(function(row) {
        return row.timestamp + ' [' + row.session_id + '] ' + row.role + ': ' + row.content;
      }).join('\\n\\n') || 'No conversation logs yet.';
      window.setTimeout(function() {
        window.location.reload();
      }, 15000);
    }
    boot();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
