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
import random
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

MIN_REPLY_DELAY_MS = 900
MAX_REPLY_DELAY_MS = 2600

SYSTEM_PROMPT = f"""You are the chat assistant for The Outdoor Squad, an outdoor fitness community in Sydney's Inner West.

You should feel like a real, thoughtful coach or front-desk human, not a scripted FAQ bot.

Core behaviour:
- First react to what the person actually said.
- Then answer what you can clearly answer.
- Then ask the single most natural next question, only if it helps.
- Do not dump a pre-made pitch unless it genuinely fits the moment.
- Do not sound like a flowchart, sales script, or support macro.

Your goals, in order:
1. Understand the person and the context of their message
2. Help naturally using the knowledge base
3. Move promising conversations toward a free intro class
4. Qualify intent without making the chat feel like an interrogation
5. Capture contact details only when the moment is right
6. Mention nutrition or PT only when relevant to what they said

Knowledge Base:
{KNOWLEDGE_BASE}

Conversation rules:
- Be warm, casual, observant, and human
- Keep replies short, usually 2 to 5 short lines, not giant blocks
- Make replies easy to scan on a phone
- Prefer this structure when it fits: quick reaction, direct answer, then one simple next step or question
- Use line breaks naturally so each idea has room
- If you list options, keep them short and clean instead of writing a dense paragraph
- Vary sentence structure, avoid repeating the same openings or closings
- Do not always end with a CTA, sometimes a simple helpful answer is better
- Ask at most one question at a time unless the user clearly wants to move fast
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
- Avoid sounding too polished; a slightly natural spoken tone is better than perfect marketing copy
- If the user is joking, uncertain, drunk, flirty, embarrassed, forgetful, or changing topic, stay steady and reply like a real person would

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
            temperature=0.9,
            presence_penalty=0.3,
            frequency_penalty=0.2,
        )
        reply = response.choices[0].message.content

        conversations[session_id].append({"role": "assistant", "content": reply})

        # Check if lead info was shared (basic extraction)
        lead_info = extract_lead_info(message, session_id)
        if lead_info:
            save_lead(lead_info)

        reply_delay_ms = random.randint(MIN_REPLY_DELAY_MS, MAX_REPLY_DELAY_MS)

        return JSONResponse({
            "reply": reply,
            "session_id": session_id,
            "reply_delay_ms": reply_delay_ms,
        })

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/booking")
async def booking(request: Request):
    """Handle discovery call booking requests"""
    body = await request.json()
    name = body.get("name", "Unknown")
    email = body.get("email", "")
    business = body.get("business", "")
    phone = body.get("phone", "")
    date = body.get("date", "")
    time = body.get("time", "")
    notes = body.get("notes", "")

    # Save booking
    bookings_file = Path(__file__).parent / "bookings.json"
    if not bookings_file.exists():
        bookings_file.write_text("[]")
    bookings = json.loads(bookings_file.read_text())
    booking_data = {
        "name": name, "email": email, "business": business,
        "phone": phone, "date": date, "time": time,
        "notes": notes, "created_at": datetime.now().isoformat()
    }
    bookings.append(booking_data)
    bookings_file.write_text(json.dumps(bookings, indent=2))

    # Send notification email to ourselves
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(
            f"New Discovery Call Booking!\n\n"
            f"Name: {name}\nEmail: {email}\nBusiness: {business}\n"
            f"Phone: {phone}\nDate: {date}\nTime: {time} AEST\n"
            f"Notes: {notes}\n\n— AI Sprints Booking System"
        )
        msg["Subject"] = f"New Booking: {name} ({business or 'No business'})"
        msg["From"] = "bookings@aisprints.com.au"
        msg["To"] = "jacowilmjr@agentmail.to"
        # Best effort — don't fail the booking if email fails
    except Exception:
        pass

    return JSONResponse({"ok": True, "message": "Booking received"})


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
