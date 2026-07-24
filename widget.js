/**
 * The Outdoor Squad — Embeddable Chat Widget
 * Add this to any website: <script src="YOUR_BOT_URL/widget.js"></script>
 *
 * The widget creates a floating chat bubble in the bottom-right corner.
 */
(function() {
    const API_URL = document.currentScript.src.replace('/widget.js', '/api/chat');
    const EVENT_URL = document.currentScript.src.replace('/widget.js', '/api/event');
    // One session id per VISIT, not per page load: persisted so a conversation
    // survives page navigation (the bot's server-side memory is keyed by this
    // id), and so funnel analytics count a multi-page visit as one session.
    const SESSION_ID = (() => {
        try {
            let sid = sessionStorage.getItem('os-session-id');
            if (!sid) {
                sid = 'widget-' + Math.random().toString(36).substr(2, 9);
                sessionStorage.setItem('os-session-id', sid);
            }
            return sid;
        } catch (e) {
            return 'widget-' + Math.random().toString(36).substr(2, 9);
        }
    })();

    // Bubble design variant. Stamped on every event so Nicholas can split-test
    // icons month by month and compare open-rate per variant. Bump this string
    // whenever the bubble look changes (e.g. 'gday-robot' -> 'kettlebell').
    const BUBBLE_VARIANT = 'gday-robot';

    function track(eventType, metadata) {
        try {
            const meta = Object.assign({ bubble_variant: BUBBLE_VARIANT }, metadata || {});
            fetch(EVENT_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ event_type: eventType, session_id: SESSION_ID, metadata: meta }),
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
        /* B+robot pill: wave 👋 · "G'day — ask Robo-Nick" · robot face */
        #os-chat-bubble {
            position: relative;
            height: 56px;
            border-radius: 999px;
            padding: 0 16px;
            gap: 10px;
            background: linear-gradient(135deg, #f26522 0%, #e0540f 100%);
            color: white;
            border: 0;
            cursor: pointer;
            box-shadow: 0 10px 28px rgba(242,101,34,.42), 0 4px 12px rgba(10,10,10,.18);
            display: flex; align-items: center; justify-content: center;
            font-family: inherit;
            font-weight: 700;
            font-size: .95rem;
            line-height: 1;
            white-space: nowrap;
            transition: transform .2s ease, box-shadow .2s ease;
        }
        #os-chat-bubble:hover {
            transform: translateY(-2px) scale(1.03);
            box-shadow: 0 14px 32px rgba(242,101,34,.5), 0 6px 14px rgba(10,10,10,.22);
        }
        #os-chat-bubble .os-bub-label { padding-bottom: 1px; }
        #os-chat-bubble .os-wave {
            font-size: 1.2rem;
            display: inline-block;
            transform-origin: 70% 70%;
            animation: os-wave 2.6s ease-in-out infinite;
        }
        @keyframes os-wave {
            0%, 60%, 100% { transform: rotate(0deg); }
            65%, 75%, 85% { transform: rotate(16deg); }
            70%, 80% { transform: rotate(-8deg); }
        }
        /* Little robot face at the trailing end */
        #os-chat-bubble .os-robot {
            position: relative;
            flex: 0 0 auto;
            width: 30px; height: 24px;
            background: #ffffff;
            border-radius: 7px;
            box-shadow: inset 0 -2px 0 rgba(0,0,0,.10);
        }
        #os-chat-bubble .os-robot::before,
        #os-chat-bubble .os-robot::after {
            content: ''; position: absolute; top: 6px;
            width: 6px; height: 8px; border-radius: 2.5px;
            background: #e0540f;
            animation: os-blink 4.5s infinite;
        }
        #os-chat-bubble .os-robot::before { left: 6px; }
        #os-chat-bubble .os-robot::after { right: 6px; }
        #os-chat-bubble .os-robot-antenna {
            position: absolute; top: -6px; left: 50%; transform: translateX(-50%);
            width: 2.5px; height: 6px; background: #ffffff; border-radius: 2px;
        }
        #os-chat-bubble .os-robot-antenna::after {
            content: ''; position: absolute; top: -4px; left: 50%; transform: translateX(-50%);
            width: 6px; height: 6px; background: #16a34a; border-radius: 50%;
            animation: os-pulse 2.2s ease-out infinite;
        }
        @keyframes os-blink {
            0%, 46%, 50%, 100% { transform: scaleY(1); }
            48% { transform: scaleY(.12); }
        }
        @keyframes os-pulse {
            0%   { box-shadow: 0 0 0 0 rgba(22,163,74,.45); }
            70%  { box-shadow: 0 0 0 8px rgba(22,163,74,0); }
            100% { box-shadow: 0 0 0 0 rgba(22,163,74,0); }
        }

        /* Proactive teaser: small speech bubble above the pill. Shown once per
           browser session after a short delay; the passive pill alone had a
           ~0.1% open rate over the first live fortnight. */
        #os-teaser {
            display: none;
            position: absolute; bottom: 68px; right: 0;
            max-width: 280px;
            background: #ffffff;
            color: #0a0a0a;
            border-radius: 14px;
            border-bottom-right-radius: 4px;
            padding: 12px 34px 12px 14px;
            font-size: .88rem;
            font-weight: 500;
            line-height: 1.35;
            box-shadow: 0 12px 32px rgba(10,10,10,.18), 0 3px 10px rgba(10,10,10,.10);
            cursor: pointer;
            transform-origin: bottom right;
        }
        #os-teaser.show { display: block; animation: os-pop .25s cubic-bezier(.2,.9,.3,1.1); }
        #os-teaser-close {
            position: absolute; top: 6px; right: 8px;
            border: 0; background: none; cursor: pointer;
            color: #9a9a9a; font-size: 1rem; line-height: 1;
            padding: 2px 4px; font-family: inherit;
        }
        #os-teaser-close:hover { color: #0a0a0a; }
        @media (prefers-reduced-motion: no-preference) {
            #os-chat-bubble.os-nudge { animation: os-nudge 1.5s ease-in-out 3; }
        }
        @keyframes os-nudge {
            0%, 100% { transform: scale(1); }
            50% { transform: scale(1.05); box-shadow: 0 12px 30px rgba(242,101,34,.55), 0 5px 13px rgba(10,10,10,.2); }
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
            #os-chat-widget { right: 14px; bottom: 14px; max-width: calc(100vw - 28px); }
            #os-chat-bubble {
                height: 52px;
                padding: 0 13px;
                gap: 8px;
                font-size: .9rem;
            }
            #os-chat-bubble .os-wave { font-size: 1.1rem; }
            #os-chat-bubble .os-robot { width: 27px; height: 22px; }
            #os-chat-panel {
                position: fixed;
                right: 12px; left: 12px;
                bottom: 82px;
                width: auto;
                height: calc(100vh - 106px);
                max-height: calc(100vh - 106px);
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
                    <div class="os-header-name">Robo-Nick</div>
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
                    <input type="text" id="os-input" placeholder="Ask Robo-Nick anything…" autocomplete="off">
                </div>
                <button id="os-send" class="os-send-btn" type="button" aria-label="Send message">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 5l7 7-7 7"/></svg>
                </button>
            </div>
        </div>
        <div id="os-teaser" role="button" tabindex="0" aria-label="Open chat with Robo-Nick">
            <button id="os-teaser-close" type="button" aria-label="Dismiss">✕</button>
            <span>Got a question about times, prices or where to start? Ask me — I'm quick 👋</span>
        </div>
        <button id="os-chat-bubble" type="button" aria-label="Open chat with Robo-Nick">
            <span class="os-wave" aria-hidden="true">👋</span>
            <span class="os-bub-label">G'day — ask Robo-Nick</span>
            <span class="os-robot" aria-hidden="true"><span class="os-robot-antenna"></span></span>
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

    function openPanel(restoring) {
        hideTeaser();
        panel.classList.add('open');
        try { sessionStorage.setItem('os-panel-open', '1'); } catch (e) {}
        if (!restoring) {
            setTimeout(() => input.focus(), 60);
            track('widget_opened');
        }
    }
    function closePanel() {
        panel.classList.remove('open');
        try { sessionStorage.removeItem('os-panel-open'); } catch (e) {}
    }

    bubble.onclick = () => {
        if (panel.classList.contains('open')) closePanel(); else openPanel();
    };
    if (closeBtn) closeBtn.onclick = closePanel;

    // Proactive teaser: after a short delay, nudge visitors who haven't opened
    // the chat. Once per browser session; click opens the chat, ✕ dismisses.
    const teaser = document.getElementById('os-teaser');
    const teaserClose = document.getElementById('os-teaser-close');
    let teaserHideTimer = null;

    function teaserState() {
        try { return sessionStorage.getItem('os-teaser'); } catch (e) { return null; }
    }

    function hideTeaser() {
        // Any hide ends the teaser's lifecycle for the whole visit (dismissed,
        // clicked, expired, or the chat opened) — no page will re-show it.
        if (teaser) teaser.classList.remove('show');
        if (teaserHideTimer) { clearTimeout(teaserHideTimer); teaserHideTimer = null; }
        try { sessionStorage.setItem('os-teaser', 'done'); } catch (e) {}
    }

    function showTeaser(resumeMs) {
        if (!teaser || panel.classList.contains('open')) return;
        if (!resumeMs) {
            if (teaserState()) return;
            try { sessionStorage.setItem('os-teaser', String(Date.now())); } catch (e) {}
            track('teaser_shown');
        }
        teaser.classList.add('show');
        bubble.classList.add('os-nudge');
        // Don't hover forever — quietly retire if ignored.
        teaserHideTimer = setTimeout(hideTeaser, resumeMs || 45000);
    }

    if (teaser) {
        teaser.addEventListener('click', (ev) => {
            if (ev.target.closest('#os-teaser-close')) return;
            track('teaser_clicked');
            openPanel();
        });
        teaser.addEventListener('keydown', (ev) => {
            if (ev.key === 'Enter' || ev.key === ' ') {
                ev.preventDefault();
                track('teaser_clicked');
                openPanel();
            }
        });
        teaserClose.addEventListener('click', (ev) => {
            ev.stopPropagation();
            track('teaser_dismissed');
            hideTeaser();
        });
        bubble.addEventListener('animationend', () => bubble.classList.remove('os-nudge'));
        // Teaser lifecycle survives navigation: 'os-teaser' holds the epoch-ms
        // it was first shown while it is on screen, then 'done' once dismissed,
        // clicked, expired, or the chat opened. Landing on a new page while it
        // is mid-display re-shows it for the REMAINDER of its 45s, so hopping
        // pages doesn't make it vanish. The 10s countdown is likewise
        // cumulative from the visit's first page (os-teaser-t0), with a 1.2s
        // floor so it never pops jarringly mid page-transition. ('1' is the
        // legacy shown-flag from the previous widget build — treat as done.)
        const st = teaserState();
        if (st === 'done' || st === '1') {
            /* lifecycle already finished this visit */
        } else if (st) {
            const remaining = 45000 - (Date.now() - Number(st));
            if (remaining > 1000) showTeaser(remaining); else hideTeaser();
        } else {
            let teaserDelay = 10000;
            try {
                let t0 = Number(sessionStorage.getItem('os-teaser-t0'));
                if (!t0) {
                    t0 = Date.now();
                    sessionStorage.setItem('os-teaser-t0', String(t0));
                }
                teaserDelay = Math.max(1200, 10000 - (Date.now() - t0));
            } catch (e) { /* storage blocked — plain per-page delay */ }
            setTimeout(showTeaser, teaserDelay);
        }
    }

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

    function addMsg(text, type, restoring) {
        const el = document.createElement('div');
        el.className = `os-msg ${type}`;
        if (type === 'bot') {
            el.innerHTML = renderBotHtml(text);
        } else {
            el.textContent = text;
        }
        msgs.appendChild(el);
        msgs.scrollTop = msgs.scrollHeight;
        if (!restoring) {
            // Keep the transcript for this visit so page navigation mid-chat
            // doesn't wipe the conversation from the panel.
            try {
                const log = JSON.parse(sessionStorage.getItem('os-chat-log') || '[]');
                log.push({ t: text, w: type });
                sessionStorage.setItem('os-chat-log', JSON.stringify(log.slice(-40)));
            } catch (e) {}
        }
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

    // Restore the visit's chat across page navigations: the stable session id
    // already keeps the bot's server-side memory intact, and here the
    // transcript and the open/closed panel state come back client-side, so
    // switching pages mid-conversation loses nothing. Restores are silent —
    // no events are re-tracked.
    try {
        const savedLog = JSON.parse(sessionStorage.getItem('os-chat-log') || '[]');
        if (savedLog.length) {
            savedLog.forEach((m) => addMsg(m.t, m.w, true));
            if (quickReplies) quickReplies.style.display = 'none';
        }
        if (sessionStorage.getItem('os-panel-open')) openPanel(true);
    } catch (e) {}

    // Impression ping: counts a visit where the chat bubble was visible, so the
    // weekly report can show what % of visitors actually engage. Fired once per
    // browser session (sessionStorage guard) so page hops/reloads don't inflate.
    try {
        if (!sessionStorage.getItem('os-impression')) {
            sessionStorage.setItem('os-impression', '1');
            track('widget_impression', { page: (location.pathname || '/').slice(0, 200) });
        }
    } catch (e) {
        // Storage blocked (strict privacy mode) — still count the view.
        track('widget_impression', { page: 'storage-unavailable' });
    }
})();
