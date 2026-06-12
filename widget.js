/**
 * The Outdoor Squad — Embeddable Chat Widget
 * Add this to any website: <script src="YOUR_BOT_URL/widget.js"></script>
 *
 * The widget creates a floating chat bubble in the bottom-right corner.
 */
(function() {
    const API_URL = document.currentScript.src.replace('/widget.js', '/api/chat');
    const EVENT_URL = document.currentScript.src.replace('/widget.js', '/api/event');
    const SESSION_ID = 'widget-' + Math.random().toString(36).substr(2, 9);

    function track(eventType, metadata) {
        try {
            fetch(EVENT_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ event_type: eventType, session_id: SESSION_ID, metadata: metadata || {} }),
                keepalive: true
            }).catch(() => {});
        } catch (e) {}
    }

    const style = document.createElement('style');
    style.textContent = `
        #os-chat-widget {
            position: fixed; right: 20px; bottom: 20px; z-index: 99999;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            color: #0a0a0a;
        }
        #os-chat-bubble {
            position: relative;
            width: 60px; height: 60px;
            border-radius: 50%;
            background: linear-gradient(135deg, #f26522 0%, #e0540f 100%);
            color: white;
            border: 0;
            cursor: pointer;
            box-shadow: 0 10px 28px rgba(242,101,34,.42), 0 4px 12px rgba(10,10,10,.18);
            display: flex; align-items: center; justify-content: center;
            transition: transform .2s ease, box-shadow .2s ease;
        }
        #os-chat-bubble:hover {
            transform: translateY(-2px) scale(1.04);
            box-shadow: 0 14px 32px rgba(242,101,34,.5), 0 6px 14px rgba(10,10,10,.22);
        }
        #os-chat-bubble svg { width: 26px; height: 26px; }
        #os-chat-bubble .os-badge {
            position: absolute; top: 2px; right: 2px;
            width: 12px; height: 12px;
            background: #16a34a;
            border-radius: 50%;
            border: 2px solid #ffffff;
            box-shadow: 0 0 0 0 rgba(22,163,74,.5);
            animation: os-pulse 2.2s ease-out infinite;
        }
        @keyframes os-pulse {
            0%   { box-shadow: 0 0 0 0 rgba(22,163,74,.45); }
            70%  { box-shadow: 0 0 0 10px rgba(22,163,74,0); }
            100% { box-shadow: 0 0 0 0 rgba(22,163,74,0); }
        }

        #os-chat-panel {
            display: none;
            position: absolute; bottom: 76px; right: 0;
            width: min(380px, calc(100vw - 24px));
            height: min(560px, calc(100vh - 110px));
            background: #ffffff;
            border-radius: 16px;
            box-shadow: 0 24px 48px rgba(10,10,10,.22), 0 4px 12px rgba(10,10,10,.08);
            overflow: hidden;
            flex-direction: column;
            transform-origin: bottom right;
            animation: os-pop .22s cubic-bezier(.2,.9,.3,1.1);
        }
        #os-chat-panel.open { display: flex; }
        @keyframes os-pop {
            from { transform: scale(.92) translateY(8px); opacity: 0; }
            to   { transform: scale(1)   translateY(0);  opacity: 1; }
        }

        .os-header {
            background: linear-gradient(135deg, #111 0%, #2a2a2a 100%);
            color: white;
            padding: 16px 18px;
            display: flex; align-items: center; gap: 12px;
            position: relative;
        }
        .os-header::after {
            content: ''; position: absolute; left: 0; right: 0; bottom: 0;
            height: 3px; background: #f26522;
        }
        .os-header-avatar {
            width: 38px; height: 38px;
            border-radius: 50%;
            background: linear-gradient(135deg, #f26522, #e0540f);
            color: #ffffff;
            display: flex; align-items: center; justify-content: center;
            font-weight: 900; font-size: .8rem;
            letter-spacing: .04em;
            box-shadow: inset 0 -2px 0 rgba(0,0,0,.12);
            position: relative;
        }
        .os-header-avatar::after {
            content: ''; position: absolute; bottom: -1px; right: -1px;
            width: 11px; height: 11px;
            background: #16a34a;
            border: 2px solid #111;
            border-radius: 50%;
        }
        .os-header-text { line-height: 1.15; min-width: 0; flex: 1; }
        .os-header-name { font-size: .96rem; font-weight: 700; letter-spacing: -.005em; }
        .os-header-sub { font-size: .72rem; opacity: .68; margin-top: 2px; }
        .os-close {
            background: rgba(255,255,255,.08); border: 0; color: white;
            width: 30px; height: 30px;
            border-radius: 50%;
            cursor: pointer;
            display: flex; align-items: center; justify-content: center;
            transition: background .15s ease;
        }
        .os-close:hover { background: rgba(255,255,255,.18); }
        .os-close svg { width: 14px; height: 14px; }

        .os-messages {
            flex: 1;
            overflow-y: auto;
            padding: 18px 16px 8px;
            display: flex; flex-direction: column; gap: 10px;
            background: #fafaf7;
            scroll-behavior: smooth;
        }
        .os-msg {
            max-width: 86%;
            padding: 10px 14px;
            border-radius: 16px;
            font-size: .9rem;
            line-height: 1.5;
            white-space: pre-wrap;
            overflow-wrap: anywhere;
        }
        .os-msg.bot {
            background: #ffffff;
            color: #0a0a0a;
            border: 1px solid #ececec;
            align-self: flex-start;
            border-bottom-left-radius: 6px;
            box-shadow: 0 1px 2px rgba(10,10,10,.03);
        }
        .os-msg.user {
            background: linear-gradient(135deg, #f26522, #e0540f);
            color: white;
            align-self: flex-end;
            border-bottom-right-radius: 6px;
            box-shadow: 0 2px 8px rgba(242,101,34,.32);
        }
        .os-msg.bot p { margin: 0 0 8px; }
        .os-msg.bot p:last-child { margin-bottom: 0; }
        .os-msg.bot ul {
            margin: 6px 0 8px;
            padding-left: 18px;
        }
        .os-msg.bot ul:last-child { margin-bottom: 0; }
        .os-msg.bot li {
            margin: 3px 0;
            padding-left: 2px;
        }
        .os-msg.bot li::marker { color: #f26522; }
        .os-msg.bot a {
            color: #e0540f;
            font-weight: 600;
            text-decoration: underline;
            text-decoration-thickness: 1.5px;
            text-underline-offset: 2px;
            word-break: break-word;
        }
        .os-msg.bot a:hover { color: #b34109; }
        .os-msg.bot strong { font-weight: 700; }

        .os-quick-replies {
            display: flex; flex-wrap: wrap; gap: 6px;
            padding: 4px 14px 10px;
            background: #fafaf7;
        }
        .os-chip {
            border: 1px solid #e8e8e3;
            background: #ffffff;
            color: #2a2a2a;
            border-radius: 999px;
            padding: 7px 12px;
            font-size: .78rem;
            font-weight: 500;
            font-family: inherit;
            cursor: pointer;
            transition: all .15s ease;
        }
        .os-chip:hover {
            background: #fdf0e7;
            border-color: #f26522;
            color: #e0540f;
            transform: translateY(-1px);
        }

        .os-input-area {
            display: flex; align-items: center;
            padding: 12px;
            gap: 8px;
            border-top: 1px solid #ececec;
            background: #ffffff;
        }
        .os-input-wrap {
            flex: 1;
            display: flex; align-items: center;
            background: #f5f4f1;
            border: 1px solid transparent;
            border-radius: 999px;
            padding: 0 6px 0 16px;
            transition: border-color .15s ease, background .15s ease;
        }
        .os-input-wrap:focus-within {
            background: #ffffff;
            border-color: #f26522;
            box-shadow: 0 0 0 3px rgba(242,101,34,.14);
        }
        .os-input-area input {
            flex: 1;
            padding: 11px 0;
            border: 0;
            background: none;
            font-size: .9rem;
            font-family: inherit;
            color: #0a0a0a;
            outline: none;
            min-width: 0;
        }
        .os-input-area input::placeholder { color: #8a8a8a; }
        .os-send-btn {
            background: linear-gradient(135deg, #f26522, #e0540f);
            color: white;
            border: 0;
            border-radius: 50%;
            width: 36px; height: 36px;
            cursor: pointer;
            display: flex; align-items: center; justify-content: center;
            transition: transform .15s ease, box-shadow .15s ease;
            box-shadow: 0 4px 10px rgba(242,101,34,.32);
        }
        .os-send-btn:hover { transform: scale(1.06); box-shadow: 0 6px 14px rgba(242,101,34,.42); }
        .os-send-btn:active { transform: scale(.96); }
        .os-send-btn svg { width: 16px; height: 16px; }

        .os-footer {
            text-align: center;
            font-size: .66rem;
            color: #8a8a8a;
            padding: 6px 0 9px;
            background: #ffffff;
            letter-spacing: .02em;
        }
        .os-footer strong { color: #2a2a2a; font-weight: 600; }

        .os-typing {
            display: inline-flex; align-items: center; gap: 4px;
            padding: 12px 16px !important;
        }
        .os-typing span {
            display: inline-block; width: 6px; height: 6px;
            background: #c0c0c0; border-radius: 50%;
            animation: os-bounce 1.3s infinite ease-in-out;
        }
        .os-typing span:nth-child(2) { animation-delay: .15s; }
        .os-typing span:nth-child(3) { animation-delay: .3s; }
        @keyframes os-bounce {
            0%, 80%, 100% { transform: scale(.6); opacity: .5; }
            40% { transform: scale(1); opacity: 1; }
        }

        @media (max-width: 640px) {
            #os-chat-widget { right: 14px; bottom: 14px; }
            #os-chat-panel {
                position: fixed;
                right: 12px; left: 12px;
                bottom: 86px;
                width: auto;
                height: calc(100vh - 110px);
                max-height: calc(100vh - 110px);
            }
            .os-msg { max-width: 92%; }
        }
    `;
    document.head.appendChild(style);

    const widget = document.createElement('div');
    widget.id = 'os-chat-widget';
    widget.innerHTML = `
        <div id="os-chat-panel" role="dialog" aria-label="Outdoor Squad chat">
            <div class="os-header">
                <div class="os-header-avatar">OS</div>
                <div class="os-header-text">
                    <div class="os-header-name">Humanoid-Nick</div>
                    <div class="os-header-sub">Outdoor Squad · usually replies instantly</div>
                </div>
                <button class="os-close" type="button" aria-label="Close chat">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
                </button>
            </div>
            <div class="os-messages" id="os-messages">
                <div class="os-msg bot">Hey, welcome in. Ask me about classes, prices, injuries, or whether this would suit you.</div>
            </div>
            <div class="os-quick-replies" id="os-quick-replies">
                <button class="os-chip" data-message="I'm interested but not very fit yet">Not very fit yet</button>
                <button class="os-chip" data-message="I work full-time and need evening sessions">Evening sessions</button>
                <button class="os-chip" data-message="How does the free intro class work?">Free intro</button>
            </div>
            <div class="os-input-area">
                <div class="os-input-wrap">
                    <input type="text" id="os-input" placeholder="Ask Humanoid-Nick anything…" autocomplete="off">
                </div>
                <button id="os-send" class="os-send-btn" type="button" aria-label="Send message">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 5l7 7-7 7"/></svg>
                </button>
            </div>
            <div class="os-footer">Powered by <strong>Humanoid-Nick</strong> · The Outdoor Squad</div>
        </div>
        <button id="os-chat-bubble" type="button" aria-label="Open chat with Humanoid-Nick">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
            <span class="os-badge" aria-hidden="true"></span>
        </button>
    `;
    document.body.appendChild(widget);

    const bubble = document.getElementById('os-chat-bubble');
    const panel = document.getElementById('os-chat-panel');
    const input = document.getElementById('os-input');
    const sendBtn = document.getElementById('os-send');
    const closeBtn = widget.querySelector('.os-close');
    const msgs = document.getElementById('os-messages');
    const quickReplies = document.getElementById('os-quick-replies');

    function openPanel() {
        panel.classList.add('open');
        const badge = bubble.querySelector('.os-badge');
        if (badge) badge.style.display = 'none';
        setTimeout(() => input.focus(), 60);
        track('widget_opened');
    }
    function closePanel() {
        panel.classList.remove('open');
    }

    bubble.onclick = () => {
        if (panel.classList.contains('open')) closePanel(); else openPanel();
    };
    if (closeBtn) closeBtn.onclick = closePanel;

    // Delegated click tracking on links inside bot messages. Fires a
    // link_clicked event with the URL; backend treats trial-provider URLs as
    // a captured lead (no contact required) per Nicholas's 2026-06-03 ask.
    msgs.addEventListener('click', function(ev) {
        const anchor = ev.target.closest('a');
        if (!anchor) return;
        if (!anchor.closest('.os-msg.bot')) return;
        let host = '';
        try { host = new URL(anchor.href).host; } catch (e) {}
        track('link_clicked', { url: anchor.href, host: host });
    });

    function formatBotText(text) {
        return String(text || '')
            .replace(/(Short version:)\s*/gi, '\n\n$1 ')
            .replace(/([A-Za-z][A-Za-z /']+?:)\s*-\s+/g, '$1\n- ')
            .replace(/\s-\s+(?=[A-Z])/g, '\n- ')
            .replace(/\n{3,}/g, '\n\n')
            .trim();
    }

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, function(c) {
            return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]);
        });
    }

    function renderBotHtml(raw) {
        const cleaned = formatBotText(raw);
        const lines = cleaned.split('\n');
        let html = '';
        let inList = false;
        let buf = [];

        function inlineFmt(s) {
            // Escape HTML, then re-introduce markdown links, bare URLs, and **bold**.
            let out = escapeHtml(s);
            // Markdown links [label](url)
            out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, function(_m, label, url) {
                return '<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + label + '</a>';
            });
            // Bare URLs — skip if already inside an anchor (naive: avoid trailing punctuation)
            out = out.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, function(_m, pre, url) {
                const trimmed = url.replace(/[.,;:!?)\]]+$/, '');
                const trail = url.slice(trimmed.length);
                return pre + '<a href="' + trimmed + '" target="_blank" rel="noopener noreferrer">' + trimmed + '</a>' + trail;
            });
            // Bold
            out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
            return out;
        }
        function flushParagraph() {
            if (buf.length) {
                html += '<p>' + buf.join(' ') + '</p>';
                buf = [];
            }
        }
        function flushList() {
            if (inList) { html += '</ul>'; inList = false; }
        }

        for (let i = 0; i < lines.length; i++) {
            const line = lines[i];
            const trimmed = line.trim();
            if (!trimmed) {
                flushParagraph();
                flushList();
                continue;
            }
            if (/^[-•]\s+/.test(trimmed)) {
                flushParagraph();
                if (!inList) { html += '<ul>'; inList = true; }
                html += '<li>' + inlineFmt(trimmed.replace(/^[-•]\s+/, '')) + '</li>';
            } else {
                flushList();
                buf.push(inlineFmt(trimmed));
            }
        }
        flushParagraph();
        flushList();
        return html;
    }

    function addMsg(text, type) {
        const el = document.createElement('div');
        el.className = `os-msg ${type}`;
        if (type === 'bot') {
            el.innerHTML = renderBotHtml(text);
        } else {
            el.textContent = text;
        }
        msgs.appendChild(el);
        msgs.scrollTop = msgs.scrollHeight;
    }

    async function send(presetText) {
        const text = (presetText || input.value).trim();
        if (!text) return;
        track(presetText ? 'quick_reply_clicked' : 'message_sent', { message_length: text.length });
        input.value = '';
        if (quickReplies) quickReplies.style.display = 'none';
        addMsg(text, 'user');

        const typing = document.createElement('div');
        typing.className = 'os-msg bot os-typing';
        typing.innerHTML = '<span></span><span></span><span></span>';
        msgs.appendChild(typing);
        msgs.scrollTop = msgs.scrollHeight;

        try {
            const res = await fetch(API_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text, session_id: SESSION_ID })
            });
            const data = await res.json();
            const delay = typeof data.reply_delay_ms === 'number' ? data.reply_delay_ms : 0;
            setTimeout(() => {
                typing.remove();
                addMsg(data.reply || 'Sorry, something went wrong!', 'bot');
            }, delay);
        } catch (e) {
            typing.remove();
            addMsg('Sorry, something glitched on my side. Give it another go in a sec.', 'bot');
        }
    }

    if (quickReplies) {
        quickReplies.addEventListener('click', (e) => {
            const chip = e.target.closest('.os-chip');
            if (chip) send(chip.dataset.message || chip.textContent);
        });
    }
    sendBtn.onclick = () => send();
    input.onkeypress = (e) => { if (e.key === 'Enter') send(); };
})();
