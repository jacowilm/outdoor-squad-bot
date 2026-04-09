/**
 * The Outdoor Squad — Embeddable Chat Widget
 * Add this to any website: <script src="YOUR_BOT_URL/widget.js"></script>
 * 
 * The widget creates a floating chat bubble in the bottom-right corner.
 */
(function() {
    const API_URL = document.currentScript.src.replace('/widget.js', '/api/chat');
    const SESSION_ID = 'widget-' + Math.random().toString(36).substr(2, 9);

    // Inject styles
    const style = document.createElement('style');
    style.textContent = `
        #os-chat-widget { position: fixed; bottom: 20px; right: 20px; z-index: 99999; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
        #os-chat-bubble { width: 60px; height: 60px; border-radius: 50%; background: linear-gradient(135deg, #2d5016, #4a7c23); color: white; border: none; cursor: pointer; font-size: 1.5rem; box-shadow: 0 4px 16px rgba(0,0,0,0.2); transition: transform 0.2s; display: flex; align-items: center; justify-content: center; }
        #os-chat-bubble:hover { transform: scale(1.1); }
        #os-chat-bubble .badge { position: absolute; top: -2px; right: -2px; width: 16px; height: 16px; background: #e74c3c; border-radius: 50%; border: 2px solid white; }
        #os-chat-panel { display: none; position: absolute; bottom: 70px; right: 0; width: 380px; max-height: 520px; background: white; border-radius: 16px; box-shadow: 0 8px 32px rgba(0,0,0,0.15); overflow: hidden; flex-direction: column; }
        #os-chat-panel.open { display: flex; }
        .os-header { background: linear-gradient(135deg, #2d5016, #4a7c23); color: white; padding: 14px 18px; display: flex; align-items: center; gap: 10px; }
        .os-header-avatar { width: 36px; height: 36px; border-radius: 50%; background: rgba(255,255,255,0.2); display: flex; align-items: center; justify-content: center; }
        .os-header h4 { margin: 0; font-size: 0.95rem; }
        .os-header span { font-size: 0.75rem; opacity: 0.8; }
        .os-close { background: none; border: none; color: white; font-size: 1.2rem; cursor: pointer; margin-left: auto; }
        .os-messages { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; max-height: 350px; }
        .os-msg { max-width: 85%; padding: 10px 14px; border-radius: 14px; font-size: 0.9rem; line-height: 1.4; }
        .os-msg.bot { background: #f0f0f0; align-self: flex-start; border-bottom-left-radius: 4px; }
        .os-msg.user { background: linear-gradient(135deg, #2d5016, #4a7c23); color: white; align-self: flex-end; border-bottom-right-radius: 4px; }
        .os-input-area { display: flex; padding: 12px; gap: 8px; border-top: 1px solid #eee; }
        .os-input-area input { flex: 1; padding: 10px 14px; border: 1px solid #ddd; border-radius: 20px; font-size: 0.9rem; outline: none; }
        .os-input-area input:focus { border-color: #4a7c23; }
        .os-input-area button { background: linear-gradient(135deg, #2d5016, #4a7c23); color: white; border: none; border-radius: 50%; width: 38px; height: 38px; cursor: pointer; font-size: 1rem; }
        .os-typing span { display: inline-block; width: 6px; height: 6px; background: #999; border-radius: 50%; animation: os-bounce 1.4s infinite both; margin-right: 3px; }
        .os-typing span:nth-child(2) { animation-delay: 0.16s; }
        .os-typing span:nth-child(3) { animation-delay: 0.32s; }
        @keyframes os-bounce { 0%, 80%, 100% { transform: scale(0); } 40% { transform: scale(1); } }
    `;
    document.head.appendChild(style);

    // Create widget HTML
    const widget = document.createElement('div');
    widget.id = 'os-chat-widget';
    widget.innerHTML = `
        <div id="os-chat-panel">
            <div class="os-header">
                <div class="os-header-avatar">💪</div>
                <div>
                    <h4>Squad Assistant</h4>
                    <span>Online — replies instantly</span>
                </div>
                <button class="os-close" onclick="document.getElementById('os-chat-panel').classList.remove('open')">&times;</button>
            </div>
            <div class="os-messages" id="os-messages">
                <div class="os-msg bot">Hey! 👋 Welcome to The Outdoor Squad! Got questions about our classes, locations, or getting started? I'm here to help!</div>
            </div>
            <div class="os-input-area">
                <input type="text" id="os-input" placeholder="Ask me anything...">
                <button id="os-send">➤</button>
            </div>
        </div>
        <button id="os-chat-bubble">💬<div class="badge"></div></button>
    `;
    document.body.appendChild(widget);

    // Event listeners
    const bubble = document.getElementById('os-chat-bubble');
    const panel = document.getElementById('os-chat-panel');
    const input = document.getElementById('os-input');
    const sendBtn = document.getElementById('os-send');
    const msgs = document.getElementById('os-messages');

    bubble.onclick = () => {
        panel.classList.toggle('open');
        if (panel.classList.contains('open')) {
            bubble.querySelector('.badge').style.display = 'none';
            input.focus();
        }
    };

    function addMsg(text, type) {
        const el = document.createElement('div');
        el.className = `os-msg ${type}`;
        el.textContent = text;
        msgs.appendChild(el);
        msgs.scrollTop = msgs.scrollHeight;
    }

    async function send() {
        const text = input.value.trim();
        if (!text) return;
        input.value = '';
        addMsg(text, 'user');

        // Typing indicator
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
            typing.remove();
            addMsg(data.reply || 'Sorry, something went wrong!', 'bot');
        } catch(e) {
            typing.remove();
            addMsg('Sorry, having trouble connecting. Try again!', 'bot');
        }
    }

    sendBtn.onclick = send;
    input.onkeypress = (e) => { if (e.key === 'Enter') send(); };
})();
