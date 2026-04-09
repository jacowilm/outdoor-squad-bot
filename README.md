# The Outdoor Squad — AI Chatbot

Custom AI assistant for The Outdoor Squad fitness community (Inner West Sydney).

## Features
- 💬 FAQ handling (classes, locations, pricing, nutrition)
- 🎯 Lead qualification (fitness goals, experience level)
- 📅 Free trial booking guidance
- 💪 Objection handling
- 🥗 Nutrition program upselling
- 📋 Contact detail capture

## Quick Start
```bash
pip install -r requirements.txt
export OPENAI_API_KEY=your_key
uvicorn app:app --reload
```

## Embed Widget
Add to any website:
```html
<script src="https://your-deploy-url/widget.js"></script>
```

## Demo
Visit `http://localhost:8000` for the full demo page.

## API
- `POST /api/chat` — Send a message, get a reply
- `GET /api/leads` — View captured leads

Built by [AI Sprints](https://aisprints.pages.dev)
