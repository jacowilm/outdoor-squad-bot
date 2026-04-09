"""
The Outdoor Squad — AI Chatbot Demo
Built by AI Sprints for Nicholas Holland / The Outdoor Squad

Features:
- Answers FAQs about classes, locations, pricing, nutrition
- Lead qualification (asks about fitness goals, experience, availability)
- Directs to free trial signup
- Handles objections
- Upsells nutrition programs
- Captures lead details for follow-up
"""
import os
import json
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI

app = FastAPI(title="Outdoor Squad AI Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load knowledge base
KB_PATH = Path(__file__).parent / "knowledge_base.md"
KNOWLEDGE_BASE = KB_PATH.read_text() if KB_PATH.exists() else ""

# Load leads file
LEADS_FILE = Path(__file__).parent / "leads.json"
if not LEADS_FILE.exists():
    LEADS_FILE.write_text("[]")

# OpenAI client (lazy init to avoid crash if key not set at import time)
_client = None

def get_client():
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        _client = OpenAI(api_key=api_key)
    return _client

SYSTEM_PROMPT = f"""You are the AI assistant for The Outdoor Squad, an outdoor fitness community in Sydney's Inner West. You're friendly, energetic, and supportive — matching the squad's vibe.

Your goals (in priority order):
1. QUALIFY LEADS — Ask about their fitness goals, current activity level, and what they're looking for
2. ANSWER QUESTIONS — Use the knowledge base below to answer accurately
3. BOOK FREE TRIALS — Always guide conversations toward booking a free intro class
4. HANDLE OBJECTIONS — If someone hesitates, address concerns warmly (e.g., "all levels welcome", "no commitment", "try it free first")
5. UPSELL NUTRITION — If someone mentions diet/weight/eating, mention the nutrition program
6. CAPTURE DETAILS — Try to get their name, email, and phone for follow-up

Knowledge Base:
{KNOWLEDGE_BASE}

Conversation rules:
- Be warm, casual, and encouraging — like a friendly coach
- Keep responses concise (2-4 sentences max unless they ask for detail)
- Always end with a question or call-to-action
- If you don't know something specific (like exact pricing or timetable), say "I'd love to get you the exact details — the best way is to book a free intro class where a coach can walk you through everything"
- Never make up information not in the knowledge base
- If someone gives their name/email/phone, acknowledge it warmly
- Use emojis sparingly but naturally (💪, 🏋️, ☀️)
- If someone asks about competitors or other gyms, stay positive about Outdoor Squad without badmouthing others
"""

# In-memory conversation store (per session)
conversations: dict[str, list] = {}


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    message = body.get("message", "").strip()
    session_id = body.get("session_id", "default")

    if not message:
        return JSONResponse({"error": "No message provided"}, status_code=400)

    # Get or create conversation history
    if session_id not in conversations:
        conversations[session_id] = []

    conversations[session_id].append({"role": "user", "content": message})

    # Keep last 20 messages for context
    recent = conversations[session_id][-20:]

    try:
        response = get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + recent,
            max_tokens=300,
            temperature=0.7,
        )
        reply = response.choices[0].message.content

        conversations[session_id].append({"role": "assistant", "content": reply})

        # Check if lead info was shared (basic extraction)
        lead_info = extract_lead_info(message, session_id)
        if lead_info:
            save_lead(lead_info)

        return JSONResponse({
            "reply": reply,
            "session_id": session_id,
        })

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/leads")
async def get_leads():
    """Admin endpoint to view captured leads"""
    leads = json.loads(LEADS_FILE.read_text())
    return JSONResponse(leads)


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
        from fastapi.responses import Response
        return Response(content=js_path.read_text(), media_type="application/javascript")
    return Response(content="console.error('widget.js not found')", media_type="application/javascript")


def extract_lead_info(message: str, session_id: str) -> dict | None:
    """Basic lead info extraction from messages"""
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

    if info:
        info['session_id'] = session_id
        info['timestamp'] = datetime.now().isoformat()
        info['raw_message'] = message
        return info
    return None


def save_lead(lead_info: dict):
    """Save lead to JSON file"""
    leads = json.loads(LEADS_FILE.read_text())
    leads.append(lead_info)
    LEADS_FILE.write_text(json.dumps(leads, indent=2))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
