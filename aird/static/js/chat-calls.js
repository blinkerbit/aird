/**
 * LiveKit voice/video for chat (media via external SFU; signaling on /ws/chat).
 */
let ctx = null;
let room = null;
let livekit = null;
let activeMode = 'audio';
let incoming = null;
let ringTimer = null;
let ringAudioCtx = null;
let busyTimer = null;
let waitingForAnswer = false;
let activeCallConvId = '';
let callId = '';
let phase = 'idle';
let wsBound = false;
let headerBound = false;
let callLock = false;
let remoteDropTimer = null;
let callMinimized = false;
let videoFit = 'fit';
let meterCtx = null;
let meters = { local: null, remote: null };
let meterRaf = 0;
let speakerCtx = null;
let audioRoute = 'headset';
let routeGen = 0;
let routeTimers = [];
let routeToastTimer = 0;

function meName() {
  return ctx?.me || globalThis.__AIRD_CHAT_USER__ || '';
}

function ensureCallStyles() {
  const existing = document.getElementById('chatCallStyles');
  if (existing?.dataset.version === '15') return;
  existing?.remove();
  const style = document.createElement('style');
  style.id = 'chatCallStyles';
  style.dataset.version = '15';
  style.textContent = `
    .chat-call-btn.btn{min-height:2rem;height:2rem;width:2rem;padding:0}
    .chat-call-btn svg{width:16px;height:16px}
    .chat-call-incoming{position:fixed;inset:0;z-index:2147483000;display:flex !important;align-items:center;justify-content:center;padding:calc(1rem + env(safe-area-inset-top,0)) calc(1rem + env(safe-area-inset-right,0)) calc(1rem + env(safe-area-inset-bottom,0)) calc(1rem + env(safe-area-inset-left,0));background:rgba(0,0,0,.55)}
    .chat-call-pagebar{position:fixed;top:0;left:0;right:0;z-index:2147483001;display:flex !important;align-items:center;justify-content:space-between;gap:.75rem;padding:calc(.65rem + env(safe-area-inset-top,0)) calc(.8rem + env(safe-area-inset-right,0)) .65rem calc(.8rem + env(safe-area-inset-left,0));background:#111827;color:#fff;box-shadow:0 8px 24px rgba(0,0,0,.28)}
    .chat-call-pagebar p{margin:0;font-weight:700;font-size:.95rem;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .chat-call-pagebar div{display:flex;gap:.45rem;flex-shrink:0;align-items:flex-end}
    .chat-call-overlay.hidden,.chat-call-incoming.hidden,.chat-call-pagebar.hidden,.chat-call-minimized.hidden,.chat-call-device-menu.hidden,.chat-call-inchat.hidden{display:none !important}
    .chat-call-inchat{display:flex !important;align-items:center;justify-content:space-between;gap:.75rem;margin:.65rem .75rem 0;padding:.9rem 1rem;border-radius:.9rem;background:#111827;color:#fff;position:relative;z-index:6}
    .chat-call-inchat p{margin:0;font-weight:700}
    .chat-call-inchat div{display:flex;gap:.45rem;flex-shrink:0;align-items:flex-end}
    .chat-call-modal-card{width:min(22rem,92vw);padding:1.15rem;border-radius:1.1rem;background:var(--color-base-100,#fff);text-align:center;box-shadow:0 18px 48px rgba(0,0,0,.32)}
    .chat-call-modal-actions{display:flex;flex-wrap:nowrap;justify-content:center;align-items:flex-end;gap:1rem;margin-top:.95rem}
    .chat-call-overlay{position:fixed;inset:0;z-index:90;display:flex !important;flex-direction:column !important;align-items:stretch !important;justify-content:flex-start !important;width:100% !important;height:100% !important;background:#0c0c10 !important;color:#fff}
    .chat-call-top{display:flex;align-items:center;justify-content:space-between;gap:.75rem;width:100%;padding:.7rem .8rem;padding-top:calc(.7rem + env(safe-area-inset-top,0));position:relative;z-index:4}
    .chat-call-status{margin:0;font-size:.85rem;font-weight:600;opacity:.8}
    .chat-call-top-actions{display:flex;gap:.4rem}
    .chat-call-iconbtn{width:2.2rem;height:2.2rem;border:0;border-radius:999px;background:rgba(255,255,255,.14);color:#fff;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;font-size:1.15rem;line-height:1}
    .chat-call-iconbtn svg{width:1.05rem;height:1.05rem}
    .chat-call-device-menu{position:absolute;top:calc(3.2rem + env(safe-area-inset-top,0));right:.7rem;z-index:5;box-sizing:border-box;width:min(18rem,calc(100vw - 1.4rem));max-height:calc(100% - 3.2rem - env(safe-area-inset-top,0) - 6.5rem - env(safe-area-inset-bottom,0));overflow:auto;padding:.75rem;border-radius:.85rem;background:#1b1b22;box-shadow:0 16px 40px rgba(0,0,0,.45);display:grid;grid-template-columns:minmax(0,1fr);align-content:start;gap:.55rem}
    .chat-call-device-menu label{display:grid;gap:.25rem;font-size:.75rem;font-weight:650;text-align:left}
    .chat-call-device-menu select{width:100%;padding:.4rem .45rem;border-radius:.45rem;border:1px solid rgba(255,255,255,.16);background:#111;color:#fff;font:inherit}
    .chat-call-stage{position:relative;flex:1;width:100%;min-height:0}
    .chat-call-tile{position:absolute;overflow:hidden;background:#17171d}
    .chat-call-tile--remote{inset:0}
    .chat-call-tile--local{right:.75rem;bottom:.75rem;width:min(34vw,13.5rem);aspect-ratio:3/4;border-radius:.85rem;box-shadow:0 10px 28px rgba(0,0,0,.45);z-index:2;border:1px solid rgba(255,255,255,.12);touch-action:none;cursor:grab;user-select:none}
    .chat-call-pipmin{position:absolute;top:.4rem;right:.4rem;z-index:4;width:1.75rem;height:1.75rem;padding:0;border:0;border-radius:999px;background:rgba(0,0,0,.55);color:#fff;display:inline-flex;align-items:center;justify-content:center;cursor:pointer}
    .chat-call-pipmin svg{width:1rem;height:1rem}
    .chat-call-pipmin .icon-show{display:none}
    .chat-call-tile--local.is-pip-collapsed{width:1.75rem;height:1.75rem;aspect-ratio:auto;border-radius:999px;background:transparent;border:0;box-shadow:none;overflow:visible}
    .chat-call-tile--local.is-pip-collapsed video,.chat-call-tile--local.is-pip-collapsed .chat-call-face,.chat-call-tile--local.is-pip-collapsed .chat-call-caption{display:none !important}
    .chat-call-tile--local.is-pip-collapsed .chat-call-pipmin{top:0;right:0}
    .chat-call-tile--local.is-pip-collapsed .chat-call-pipmin .icon-hide{display:none}
    .chat-call-tile--local.is-pip-collapsed .chat-call-pipmin .icon-show{display:block}
    .chat-call-tile video,.chat-call-video{position:absolute;inset:0;z-index:0;width:100% !important;height:100% !important;max-height:none !important;border-radius:0 !important;object-fit:cover;background:#000;display:block}
    .chat-call-stage.is-fit .chat-call-tile--remote video{object-fit:contain !important;width:var(--call-vw,auto) !important;height:var(--call-vh,auto) !important;max-width:100% !important;max-height:100% !important;inset:auto !important;left:50% !important;top:50% !important;transform:translate(-50%,-50%) !important}
    /* Self-view like a mirror; remote stays true-sided so text/gestures match the other person. */
    .chat-call-tile--local video,.chat-call-video--mirror{-webkit-transform:scaleX(-1) !important;transform:scaleX(-1) !important}
    .chat-call-face{position:absolute;inset:0;z-index:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.55rem;padding:.6rem;text-align:center}
    .chat-call-face-name{font-weight:650;font-size:1.55rem;letter-spacing:-.02em}
    .chat-call-tile--local .chat-call-face-name{font-size:.95rem}
    .chat-call-tile.has-video .chat-call-face{display:none}
    .chat-call-caption{display:none;position:absolute;left:.55rem;bottom:.5rem;z-index:1;align-items:center;gap:.4rem;max-width:calc(100% - 1.1rem);padding:.2rem .45rem;border-radius:999px;background:rgba(0,0,0,.38);font-size:.78rem;font-weight:600}
    .chat-call-tile.has-video .chat-call-caption{display:flex}
    .chat-call-caption span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .chat-call-tile.is-speaking{box-shadow:inset 0 0 0 3px rgba(134,239,172,.95)}
    .chat-call-tile--local.is-speaking{box-shadow:0 0 0 3px #86efac, 0 10px 28px rgba(0,0,0,.45)}
    .chat-call-wave{display:inline-flex;align-items:flex-end;gap:.18rem;height:.7rem;opacity:0}
    .chat-call-wave i{width:.28rem;height:.28rem;border-radius:50%;background:#fff;display:block}
    .chat-call-tile.is-speaking:not(.is-muted) .chat-call-wave{opacity:1}
    .chat-call-tile.is-speaking:not(.is-muted) .chat-call-wave i{animation:chat-call-wave .9s ease-in-out infinite}
    .chat-call-tile.is-speaking:not(.is-muted) .chat-call-wave i:nth-child(2){animation-delay:.15s}
    .chat-call-tile.is-speaking:not(.is-muted) .chat-call-wave i:nth-child(3){animation-delay:.3s}
    @keyframes chat-call-wave{0%,100%{transform:translateY(0)}50%{transform:translateY(-.38rem)}}
    .chat-call-mute{display:none;width:1.05rem;height:1.05rem;flex-shrink:0;color:#fff}
    .chat-call-mute svg{width:100%;height:100%}
    .chat-call-tile.is-muted .chat-call-mute{display:inline-flex}
    .chat-call-face .chat-call-mute{width:1.35rem;height:1.35rem}
    .chat-call-ctrl{appearance:none;box-sizing:border-box;width:3.25rem;min-width:3.25rem;margin:0;padding:0;border:0;background:transparent;color:inherit;cursor:pointer;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;gap:.28rem;font:inherit;line-height:1;flex:0 0 auto}
    .chat-call-ctrl.hidden{display:none !important}
    .chat-call-ctrl-icon{width:2.75rem;height:2.75rem;border-radius:999px;display:inline-flex;align-items:center;justify-content:center;background:rgba(255,255,255,.16);color:#fff;flex:0 0 auto}
    .chat-call-ctrl-icon svg{width:1.2rem;height:1.2rem;display:block}
    .chat-call-ctrl-label{display:block;max-width:100%;font-size:.62rem;font-weight:700;letter-spacing:.01em;line-height:1.15;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .chat-call-ctrl[aria-pressed="true"] .chat-call-ctrl-icon{background:var(--color-primary);color:#fff}
    .chat-call-ctrl--answer .chat-call-ctrl-icon{background:var(--color-primary);color:#fff}
    .chat-call-ctrl--danger .chat-call-ctrl-icon,.chat-call-ctrl--danger[aria-pressed="true"] .chat-call-ctrl-icon{background:var(--color-error);color:#fff}
    .chat-call-route-toast{position:absolute;left:50%;top:42%;z-index:8;transform:translate(-50%,-40%);padding:.5rem .95rem;border-radius:.75rem;background:rgba(0,0,0,.78);color:#fff;font-size:1.05rem;font-weight:700;letter-spacing:.01em;pointer-events:none;opacity:0;transition:opacity .2s ease}
    .chat-call-route-toast.is-on{opacity:1}
    .chat-call-controls{display:flex;flex-wrap:nowrap;align-items:flex-end;justify-content:center;gap:.4rem;width:100%;min-width:0;box-sizing:border-box;position:relative;z-index:6;padding:.55rem .7rem calc(.7rem + env(safe-area-inset-bottom,0));padding-left:calc(.7rem + env(safe-area-inset-left,0));padding-right:calc(.7rem + env(safe-area-inset-right,0))}
    .chat-call-controls-scroll{display:flex;flex:0 1 auto;flex-wrap:nowrap;align-items:flex-end;gap:.35rem;min-width:0;max-width:100%;overflow-x:auto;overflow-y:hidden;scrollbar-width:none;-webkit-overflow-scrolling:touch;overscroll-behavior-x:contain}
    .chat-call-controls-scroll::-webkit-scrollbar{display:none}
    .chat-call-controls > .chat-call-ctrl--danger{flex:0 0 auto}
    .chat-call-minimized{position:fixed;z-index:91;left:50%;transform:translateX(-50%);bottom:calc(.75rem + env(safe-area-inset-bottom,0));display:flex;align-items:center;gap:.5rem;padding:.45rem calc(.65rem + env(safe-area-inset-right,0)) .45rem calc(.65rem + env(safe-area-inset-left,0));border-radius:999px;background:var(--color-primary,#2563eb);color:#fff;box-shadow:0 8px 28px rgba(0,0,0,.28);min-width:min(92vw,20rem);max-width:calc(100vw - env(safe-area-inset-left,0) - env(safe-area-inset-right,0) - 1rem);cursor:pointer}
    .chat-call-minimized-pulse{width:.55rem;height:.55rem;border-radius:50%;background:#4ade80;flex-shrink:0}
    .chat-call-minimized.is-live .chat-call-minimized-pulse{animation:chat-call-pulse 1s ease infinite}
    @keyframes chat-call-pulse{50%{opacity:.35}}
    .chat-call-minimized-label{flex:1;font-weight:700;font-size:.85rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:left}
    .chat-call-minimized-actions{display:flex;gap:.35rem;flex-shrink:0;align-items:flex-end}
  `;
  document.head.appendChild(style);
}

function muteIcon() {
  return `<span class="chat-call-mute" aria-label="Microphone off"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><line x1="2" y1="2" x2="22" y2="22"/><path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V5a3 3 0 0 0-5.94-.6"/><path d="M17 16.95A7 7 0 0 1 5 12v-2m14 0v2c0 .28-.02.56-.05.83"/><line x1="12" y1="19" x2="12" y2="22"/><line x1="8" y1="22" x2="16" y2="22"/></svg></span>`;
}

const CALL_MINIMIZE_ICON = '<svg class="icon-hide" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M5 12h14"/></svg><svg class="icon-show" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 14v6h6M20 10V4h-6M14 10l6-6M10 14l-6 6"/></svg>';
const SPEAKER_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>';
const HEADSET_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></svg>';
const MIC_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" y2="22"/></svg>';
const MIC_OFF_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="2" y1="2" x2="22" y2="22"/><path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V5a3 3 0 0 0-5.94-.6"/><path d="M17 16.95A7 7 0 0 1 5 12v-2m14 0v2c0 .28-.02.56-.05.83"/><line x1="12" x2="12" y1="19" y2="22"/></svg>';
const FIT_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 4v5H4M15 4v5h5M20 15h-5v5M4 15h5v5"/></svg>';
const FILL_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/></svg>';
const VIDEO_MODE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m16 13 5 3V8l-5 3"/><rect x="2" y="6" width="14" height="12" rx="2"/></svg>';
const PHONE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>';
const HANGUP_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.68 13.31a16 16 0 0 0 3.41 2.6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7 2 2 0 0 1 1.72 2v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.42 19.42 0 0 1-3.33-2.67m-2.67-3.34a19.79 19.79 0 0 1-3.07-8.63A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91"/><line x1="22" x2="2" y1="2" y2="22"/></svg>';
const CAM_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/></svg>';
const CAM_OFF_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/><line x1="2" y1="2" x2="22" y2="22"/></svg>';
const OPEN_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>';

function callCtrlButton(id, label, icon, className, attrs) {
  const idAttr = id ? ` id="${id}"` : '';
  const cls = className ? `chat-call-ctrl ${className}` : 'chat-call-ctrl';
  const extra = attrs ? ` ${attrs}` : '';
  return `<button type="button"${idAttr} class="${cls}"${extra}><span class="chat-call-ctrl-icon" aria-hidden="true">${icon}</span><span class="chat-call-ctrl-label">${label}</span></button>`;
}

function paintCallCtrl(btn, label, icon) {
  if (!btn) return;
  const text = btn.querySelector('.chat-call-ctrl-label');
  const glyph = btn.querySelector('.chat-call-ctrl-icon');
  if (text) text.textContent = label;
  else btn.textContent = label;
  if (icon && glyph) glyph.innerHTML = icon;
}

function answerCtrlButtons() {
  return `${callCtrlButton('', 'Decline', HANGUP_ICON, 'chat-call-ctrl--danger', 'data-call-reject aria-label="Decline"')}${callCtrlButton('', 'Answer', PHONE_ICON, 'chat-call-ctrl--answer', 'data-call-accept aria-label="Answer"')}`;
}

function tileMarkup(which, name) {
  const face = which === 'local' ? 'chatCallLocalFace' : 'chatCallRemoteFace';
  const label = which === 'local' ? 'chatCallLocalName' : 'chatCallRemoteName';
  const mute = muteIcon();
  const pip = which === 'local'
    ? `<button type="button" class="chat-call-pipmin" aria-label="Minimize your video">${CALL_MINIMIZE_ICON}</button>`
    : '';
  return `
    <div class="chat-call-face">
      <span class="chat-call-face-name" id="${face}">${name}</span>
      ${mute}
      <span class="chat-call-wave" aria-hidden="true"><i></i><i></i><i></i></span>
    </div>
    <div class="chat-call-caption">
      <span id="${label}">${name}</span>
      ${mute}
      <span class="chat-call-wave" aria-hidden="true"><i></i><i></i><i></i></span>
    </div>
    ${pip}`;
}

function wireSelfPreview(overlay) {
  const stage = overlay.querySelector('.chat-call-stage');
  const tile = overlay.querySelector('#chatCallLocalTile');
  if (!stage || !tile) return;
  let drag = null;
  const place = (left, top) => {
    const bounds = stage.getBoundingClientRect();
    const maxL = Math.max(0, bounds.width - tile.offsetWidth);
    const maxT = Math.max(0, bounds.height - tile.offsetHeight);
    tile.style.left = Math.min(maxL, Math.max(0, left)) + 'px';
    tile.style.top = Math.min(maxT, Math.max(0, top)) + 'px';
    tile.style.right = 'auto';
    tile.style.bottom = 'auto';
  };
  tile.addEventListener('pointerdown', (event) => {
    if (event.target.closest('button')) return;
    const rect = tile.getBoundingClientRect();
    const bounds = stage.getBoundingClientRect();
    drag = {
      id: event.pointerId,
      dx: event.clientX - rect.left,
      dy: event.clientY - rect.top,
      ox: bounds.left,
      oy: bounds.top,
    };
    tile.setPointerCapture(event.pointerId);
  });
  tile.addEventListener('pointermove', (event) => {
    if (!drag || drag.id !== event.pointerId) return;
    place(event.clientX - drag.ox - drag.dx, event.clientY - drag.oy - drag.dy);
  });
  const endDrag = (event) => {
    if (!drag || drag.id !== event.pointerId) return;
    drag = null;
  };
  tile.addEventListener('pointerup', endDrag);
  tile.addEventListener('pointercancel', endDrag);
}

function ensureOverlay() {
  const existing = document.getElementById('chatCallOverlay');
  if (existing && existing.dataset.version === '12') return existing;
  existing?.remove();
  const el = document.createElement('div');
  el.id = 'chatCallOverlay';
  el.className = 'chat-call-overlay hidden';
  el.innerHTML = `
    <header class="chat-call-top">
      <p id="chatCallStatus" class="chat-call-status">Connecting…</p>
      <div class="chat-call-top-actions">
        <button type="button" id="chatCallDevicesBtn" class="chat-call-iconbtn" aria-label="Microphone, speaker, and camera">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/></svg>
        </button>
        <button type="button" id="chatCallMinimizeBtn" class="chat-call-iconbtn" aria-label="Minimize"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M5 12h14"/></svg></button>
      </div>
    </header>
    <div id="chatCallDeviceMenu" class="chat-call-device-menu hidden">
      <label>Microphone
        <select id="chatCallMicSelect" aria-label="Microphone"></select>
      </label>
      <label>Speaker
        <select id="chatCallSpeakerSelect" aria-label="Speaker"></select>
      </label>
      <label>Camera
        <select id="chatCallCamSelect" aria-label="Camera"></select>
      </label>
    </div>
    <div class="chat-call-stage">
      <div class="chat-call-remote-grid" id="chatCallRemoteGrid">
        <div class="chat-call-tile chat-call-tile--remote" id="chatCallRemoteTile">${tileMarkup('remote', 'Other')}</div>
      </div>
      <div class="chat-call-tile chat-call-tile--local" id="chatCallLocalTile">${tileMarkup('local', 'You')}</div>
    </div>
    <div id="chatCallRouteToast" class="chat-call-route-toast" role="status"></div>
    <div class="chat-call-controls">
      <div class="chat-call-controls-scroll">
        ${callCtrlButton('chatCallMuteBtn', 'Mute', MIC_ICON, '', 'aria-label="Mute"')}
        ${callCtrlButton('chatCallRouteBtn', 'Headset', HEADSET_ICON, 'chat-call-route', 'aria-pressed="false" aria-label="Headset"')}
        ${callCtrlButton('chatCallFitBtn', 'Fit', FIT_ICON, '', 'aria-pressed="true" aria-label="Fit"')}
        ${callCtrlButton('chatCallModeBtn', 'Video', VIDEO_MODE_ICON, '', 'aria-label="Video"')}
        ${callCtrlButton('chatCallCamBtn', 'Camera', CAM_ICON, 'hidden', 'aria-label="Camera"')}
        ${callCtrlButton('chatCallHearBtn', 'Sound', SPEAKER_ICON, 'hidden', 'aria-label="Speaker"')}
      </div>
      ${callCtrlButton('chatCallHangupBtn', 'End', HANGUP_ICON, 'chat-call-ctrl--danger', 'aria-label="End call"')}
    </div>`;
  el.dataset.version = '12';
  document.body.appendChild(el);
  el.querySelector('#chatCallHangupBtn')?.addEventListener('click', () => { void hangUp(); });
  wireSelfPreview(el);
  el.querySelector('#chatCallMinimizeBtn')?.addEventListener('click', () => {
    closeDeviceMenu();
    if (window.AndroidHost && typeof window.AndroidHost.enterPipMode === 'function') {
      window.AndroidHost.enterPipMode();
      return;
    }
    setCallMinimized(true);
  });
  el.querySelector('.chat-call-pipmin')?.addEventListener('click', (event) => {
    event.stopPropagation();
    const tile = document.getElementById('chatCallLocalTile');
    if (!tile) return;
    const collapsed = tile.classList.toggle('is-pip-collapsed');
    event.currentTarget.setAttribute('aria-label', collapsed ? 'Show your video' : 'Minimize your video');
  });
  el.querySelector('#chatCallDevicesBtn')?.addEventListener('click', (event) => {
    event.stopPropagation();
    toggleDeviceMenu();
  });
  el.querySelector('#chatCallDeviceMenu')?.addEventListener('click', (event) => { event.stopPropagation(); });
  el.addEventListener('pointerdown', () => {
    if (!isPhone()) return;
    void enableCallSound();
  });
  el.addEventListener('click', (event) => {
    if (event.target.closest('#chatCallDevicesBtn, #chatCallDeviceMenu')) return;
    closeDeviceMenu();
  });
  el.querySelector('#chatCallRouteBtn')?.addEventListener('click', () => {
    void switchAudioRoute(audioRoute === 'speaker' ? 'headset' : 'speaker');
  });
  el.querySelector('#chatCallHearBtn')?.addEventListener('click', () => { void enableCallSound(); });
  el.querySelector('#chatCallModeBtn')?.addEventListener('click', () => {
    void setCallMode(activeMode === 'video' ? 'audio' : 'video');
  });
  el.querySelector('#chatCallCamBtn')?.addEventListener('click', () => { void toggleCamera(); });
  el.querySelector('#chatCallFitBtn')?.addEventListener('click', () => {
    applyVideoFit(videoFit === 'fit' ? 'fill' : 'fit');
  });
  applyVideoFit(readVideoFit());
  watchFitLayout(el);
  el.querySelector('#chatCallMicSelect')?.addEventListener('change', () => { void applySelectedMic(); });
  el.querySelector('#chatCallSpeakerSelect')?.addEventListener('change', () => { void applySelectedSpeaker(); });
  el.querySelector('#chatCallCamSelect')?.addEventListener('change', () => { void applySelectedCamera(); });
  return el;
}

function closeDeviceMenu() {
  document.getElementById('chatCallDeviceMenu')?.classList.add('hidden');
}

function toggleDeviceMenu() {
  const menu = document.getElementById('chatCallDeviceMenu');
  if (!menu) return;
  const open = menu.classList.contains('hidden');
  menu.classList.toggle('hidden', !open);
  if (open) void refreshDeviceSelectors();
}

function ensureMinimizedBar() {
  let el = document.getElementById('chatCallMinimized');
  if (el?.dataset.version === '1') return el;
  const hidden = !el || el.classList.contains('hidden');
  el?.remove();
  el = document.createElement('div');
  el.id = 'chatCallMinimized';
  el.dataset.version = '1';
  el.className = hidden ? 'chat-call-minimized hidden' : 'chat-call-minimized';
  el.setAttribute('role', 'button');
  el.setAttribute('tabindex', '0');
  el.setAttribute('aria-label', 'Return to call');
  el.innerHTML = `
    <span class="chat-call-minimized-pulse" aria-hidden="true"></span>
    <span id="chatCallMinimizedLabel" class="chat-call-minimized-label">On a call</span>
    <span class="chat-call-minimized-actions">
      ${callCtrlButton('chatCallMiniExpand', 'Open', OPEN_ICON, '', 'aria-label="Open call"')}
      ${callCtrlButton('chatCallMiniEnd', 'End', HANGUP_ICON, 'chat-call-ctrl--danger', 'aria-label="End call"')}
    </span>`;
  document.body.appendChild(el);
  el.addEventListener('click', (event) => {
    if (event.target.closest('#chatCallMiniEnd')) return;
    setCallMinimized(false);
  });
  el.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      setCallMinimized(false);
    }
  });
  el.querySelector('#chatCallMiniExpand')?.addEventListener('click', (event) => {
    event.stopPropagation();
    setCallMinimized(false);
  });
  el.querySelector('#chatCallMiniEnd')?.addEventListener('click', (event) => {
    event.stopPropagation();
    void hangUp();
  });
  return el;
}

function updateMinimizedBar() {
  const bar = document.getElementById('chatCallMinimized');
  const label = document.getElementById('chatCallMinimizedLabel');
  if (!bar || !label) return;
  const status = document.getElementById('chatCallStatus')?.textContent?.trim();
  const peer = otherLabel();
  const kind = activeMode === 'video' ? 'Video' : 'Voice';
  label.textContent = status && status !== 'Connecting…' ? `${kind} · ${peer}` : `${kind} call · ${peer}`;
  const live = Boolean(
    document.getElementById('chatCallLocalTile')?.classList.contains('is-speaking')
    || document.getElementById('chatCallRemoteTile')?.classList.contains('is-speaking'),
  );
  bar.classList.toggle('is-live', live);
}

function setCallMinimized(minimized) {
  const overlay = ensureOverlay();
  const mini = ensureMinimizedBar();
  if (!room && minimized) {
    callMinimized = false;
    mini.classList.add('hidden');
    overlay.classList.remove('hidden');
    return;
  }
  callMinimized = Boolean(minimized && room);
  if (callMinimized) {
    overlay.classList.add('hidden');
    mini.classList.remove('hidden');
    updateMinimizedBar();
  } else {
    mini.classList.add('hidden');
    overlay.classList.remove('hidden');
  }
}

function bindIncomingClick(root) {
  root.querySelector('[data-call-accept]')?.addEventListener('click', () => {
    const permission = beginMediaPermission(incoming?.mode === 'video' ? 'video' : 'audio');
    unlockCallAudio();
    acceptIncoming(permission).catch((err) => reportCallError(err));
  });
  root.querySelector('[data-call-reject]')?.addEventListener('click', () => { void declineIncoming(); });
}

function ensureIncomingBar() {
  let el = document.getElementById('chatCallIncoming');
  if (el?.dataset.version === '4') return el;
  el?.remove();
  el = document.createElement('div');
  el.id = 'chatCallIncoming';
  el.dataset.version = '4';
  el.className = 'chat-call-incoming hidden';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  el.innerHTML = `
    <div class="chat-call-modal-card">
      <div class="chat-call-modal-icon" aria-hidden="true">📞</div>
      <p id="chatCallIncomingText" class="chat-call-incoming-text"></p>
      <div class="chat-call-modal-actions">
        ${answerCtrlButtons()}
      </div>
    </div>`;
  document.body.appendChild(el);
  bindIncomingClick(el);
  return el;
}

function ensureChatPopup() {
  let el = document.getElementById('chatCallInChat');
  if (el?.dataset.version === '2') return el;
  el?.remove();
  el = document.createElement('div');
  el.id = 'chatCallInChat';
  el.dataset.version = '2';
  el.className = 'chat-call-inchat hidden';
  el.innerHTML = `
    <p id="chatCallInChatText"></p>
    <div>
      ${answerCtrlButtons()}
    </div>`;
  const thread = document.querySelector('.chat-thread-panel');
  if (thread) thread.insertBefore(el, thread.firstChild);
  else document.body.appendChild(el);
  bindIncomingClick(el);
  return el;
}

function ensurePageBar() {
  let el = document.getElementById('chatCallPageBar');
  if (el?.dataset.version === '2') return el;
  el?.remove();
  el = document.createElement('div');
  el.id = 'chatCallPageBar';
  el.dataset.version = '2';
  el.className = 'chat-call-pagebar hidden';
  el.innerHTML = `
    <p id="chatCallPageBarText"></p>
    <div>
      ${answerCtrlButtons()}
    </div>`;
  document.body.appendChild(el);
  bindIncomingClick(el);
  return el;
}

function primeAudio() {
  try {
    if (!ringAudioCtx) ringAudioCtx = new AudioContext();
    if (ringAudioCtx.state === 'suspended') void ringAudioCtx.resume();
  } catch { /* autoplay */ }
}

function isPhone() {
  return /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
}

function ensureSpeakerElement() {
  let el = document.getElementById('chatCallSpeakerSink');
  if (el) return el;
  el = document.createElement('audio');
  el.id = 'chatCallSpeakerSink';
  el.className = 'chat-call-remote-audio';
  el.autoplay = true;
  el.playsInline = true;
  el.setAttribute('playsinline', '');
  el.volume = 1;
  el.style.cssText = 'position:fixed;left:0;bottom:0;width:8px;height:8px;opacity:0.02;pointer-events:none';
  document.body.appendChild(el);
  return el;
}

function routeFromLabel(label) {
  const text = String(label || '').toLowerCase();
  if (/speaker|loud/.test(text)) return 'speaker';
  if (/headset|headphone|earpiece|ear piece|bluetooth|airpod|buds|handset|\bphone\b/.test(text)) return 'headset';
  return '';
}

async function sinkForRoute(speaker) {
  if (!navigator.mediaDevices?.enumerateDevices) return '';
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const match = devices.find((device) => (
      device.kind === 'audiooutput'
      && device.deviceId
      && routeFromLabel(device.label) === (speaker ? 'speaker' : 'headset')
    ));
    return match?.deviceId || '';
  } catch {
    return '';
  }
}

async function switchAudioRoute(route) {
  applyAudioRoute(route, { toast: true });
  const id = await sinkForRoute(route === 'speaker');
  if (!id) return;
  savePreferredDevice('audiooutput', id);
  const sel = document.getElementById('chatCallSpeakerSelect');
  if (sel && [...sel.options].some((opt) => opt.value === id)) sel.value = id;
  if (room) await room.switchActiveDevice('audiooutput', id).catch(() => {});
  await applySpeakerToAttachedAudio(id);
  pushHardwareRoute(route === 'speaker');
}

function clearRouteTimers() {
  routeTimers.forEach((timer) => clearTimeout(timer));
  routeTimers = [];
}

function paintRouteButton() {
  const btn = document.getElementById('chatCallRouteBtn');
  if (!btn) return;
  const speaker = audioRoute === 'speaker';
  btn.setAttribute('aria-pressed', speaker ? 'true' : 'false');
  btn.setAttribute('aria-label', speaker ? 'Speaker' : 'Headset');
  paintCallCtrl(btn, speaker ? 'Speaker' : 'Headset', speaker ? SPEAKER_ICON : HEADSET_ICON);
}

function showRouteToast(text) {
  const host = document.getElementById('chatCallOverlay') || document.body;
  let el = document.getElementById('chatCallRouteToast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'chatCallRouteToast';
    el.className = 'chat-call-route-toast';
    el.setAttribute('role', 'status');
    host.appendChild(el);
  } else if (el.parentElement !== host) {
    host.appendChild(el);
  }
  el.textContent = text;
  el.classList.remove('is-on');
  void el.offsetWidth;
  el.classList.add('is-on');
  clearTimeout(routeToastTimer);
  routeToastTimer = setTimeout(() => el.classList.remove('is-on'), 1500);
}

function pushHardwareRoute(speaker) {
  try {
    if (navigator.audioSession) navigator.audioSession.type = speaker ? 'playback' : 'play-and-record';
  } catch { /* unsupported */ }
  try {
    globalThis.AndroidHost?.setSpeakerphone?.(speaker);
  } catch { /* host missing */ }
}

function applyAudioRoute(route, opts = {}) {
  audioRoute = route === 'speaker' ? 'speaker' : 'headset';
  const generation = ++routeGen;
  const speaker = audioRoute === 'speaker';
  const run = () => {
    if (generation !== routeGen) return;
    pushHardwareRoute(speaker);
  };
  run();
  clearRouteTimers();
  routeTimers.push(setTimeout(run, 350));
  routeTimers.push(setTimeout(run, 1100));
  paintRouteButton();
  if (opts.toast) showRouteToast(speaker ? 'Speaker' : 'Headset');
}

function preferLoudspeaker() {
  applyAudioRoute(audioRoute);
}

function unlockCallAudio() {
  primeAudio();
  preferLoudspeaker();
  const el = ensureSpeakerElement();
  el.muted = false;
  el.volume = 1;
  if (!el.srcObject) {
    el.src = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=';
    void el.play().catch(() => {});
  }
  try {
    if (!speakerCtx) speakerCtx = new AudioContext();
    if (speakerCtx.state === 'suspended') void speakerCtx.resume();
  } catch { /* autoplay */ }
}

async function enableCallSound() {
  unlockCallAudio();
  const sink = document.getElementById('chatCallSpeakerSink');
  if (sink) {
    sink.muted = false;
    sink.volume = 1;
    void sink.play().catch(() => {});
  }
  try {
    await room?.startAudio();
  } catch { /* needs another tap */ }
  syncHearButton();
}

function syncHearButton() {
  const btn = document.getElementById('chatCallHearBtn');
  if (!btn) return;
  const blocked = isPhone() && (!room || room.canPlaybackAudio === false);
  btn.classList.toggle('hidden', !blocked);
}

function playBeep(freq, when, duration, volume) {
  if (!ringAudioCtx) return;
  const osc = ringAudioCtx.createOscillator();
  const gain = ringAudioCtx.createGain();
  osc.type = 'sine';
  osc.frequency.value = freq;
  gain.gain.setValueAtTime(volume, when);
  gain.gain.exponentialRampToValueAtTime(0.001, when + duration);
  osc.connect(gain);
  gain.connect(ringAudioCtx.destination);
  osc.start(when);
  osc.stop(when + duration);
}

function startRing(kind) {
  stopRing();
  primeAudio();
  const ringback = kind === 'ringback';
  try {
    if (!ringback) navigator.vibrate?.([280, 120, 280, 120, 280]);
  } catch { /* unsupported */ }
  document.getElementById('chatCallIncoming')?.classList.toggle('chat-call-incoming--ringing', !ringback);
  const burst = () => {
    if (!ringAudioCtx) return;
    const t = ringAudioCtx.currentTime;
    if (ringback) {
      playBeep(440, t, 0.35, 0.22);
      playBeep(480, t, 0.35, 0.16);
      playBeep(440, t + 0.45, 0.35, 0.22);
      playBeep(480, t + 0.45, 0.35, 0.16);
    } else {
      playBeep(520, t, 0.18, 0.2);
    }
  };
  burst();
  ringTimer = setInterval(burst, ringback ? 2800 : 850);
}

function stopRing() {
  clearInterval(ringTimer);
  ringTimer = null;
  document.getElementById('chatCallIncoming')?.classList.remove('chat-call-incoming--ringing');
}

function clearBusyTimer() {
  clearTimeout(busyTimer);
  busyTimer = null;
  waitingForAnswer = false;
}

function clearRemoteDropTimer() {
  clearTimeout(remoteDropTimer);
  remoteDropTimer = null;
}

function scheduleRemoteDropCheck() {
  clearRemoteDropTimer();
  remoteDropTimer = setTimeout(() => {
    remoteDropTimer = null;
    const live = room;
    if (waitingForAnswer || !live?.remoteParticipants) return;
    if (live.remoteParticipants.size === 0) void endLocal('Call ended', false);
  }, 3000);
}

function notifyAndroidCall(active, mode) {
  try {
    const host = globalThis.AndroidHost;
    if (host && typeof host.setInVideoCall === 'function') host.setInVideoCall(Boolean(active));
  } catch { /* host missing */ }
  if (active) {
    applyAudioRoute(mode === 'video' ? 'speaker' : 'headset');
    return;
  }
  clearRouteTimers();
  routeGen += 1;
  pushHardwareRoute(false);
  try {
    if (navigator.audioSession) navigator.audioSession.type = 'auto';
  } catch { /* unsupported */ }
}

function markAnswered() {
  if (phase !== 'outgoing' && !waitingForAnswer) return;
  phase = 'live';
  clearBusyTimer();
  stopRing();
  setStatus(activeMode === 'video' ? 'Video call' : 'Voice call');
  paintSpeaking();
}

function signalCall(type, convId, id, extra) {
  if (!convId) return;
  const ending = id || callId || '';
  ctx.sendWs?.({ type, conversation_id: convId, call_id: ending, ...(extra || {}) });
  try { globalThis.AndroidHost?.signalCall?.(type, convId, ending); } catch { /* ignore */ }
}

function syncCallModeUi() {
  const video = activeMode === 'video';
  document.getElementById('chatCallCamBtn')?.classList.toggle('hidden', !video);
  const modeBtn = document.getElementById('chatCallModeBtn');
  if (modeBtn) {
    const label = video ? 'Voice' : 'Video';
    paintCallCtrl(modeBtn, label, video ? PHONE_ICON : VIDEO_MODE_ICON);
    modeBtn.setAttribute('aria-label', label);
  }
  const camBtn = document.getElementById('chatCallCamBtn');
  if (camBtn && video) {
    const on = Boolean(room?.localParticipant?.isCameraEnabled);
    paintCallCtrl(camBtn, 'Camera', on ? CAM_ICON : CAM_OFF_ICON);
    camBtn.setAttribute('aria-label', on ? 'Camera off' : 'Camera on');
  }
  if (!waitingForAnswer) setStatus(video ? 'Video call' : 'Voice call');
  try { globalThis.AndroidHost?.setInVideoCall?.(video); } catch { /* host missing */ }
}

function clearLocalVideo() {
  const tile = document.getElementById('chatCallLocalTile');
  tile?.querySelectorAll('video').forEach((node) => node.remove());
  tile?.classList.remove('has-video');
}

let modeSwitching = false;

async function setCallMode(mode) {
  const live = room;
  if (!live?.localParticipant || modeSwitching) return;
  if (phase !== 'live' && phase !== 'outgoing' && phase !== 'joining') return;
  const next = mode === 'video' ? 'video' : 'audio';
  if (next === activeMode) return;
  modeSwitching = true;
  try {
    if (next === 'video') {
      await ensureMediaPermission('video');
      const mobile = globalThis.matchMedia('(max-width: 1023px)').matches;
      const { VideoPresets } = await loadLiveKit();
      const preset = mobile ? VideoPresets.h540 : VideoPresets.h720;
      const cameraId = document.getElementById('chatCallCamSelect')?.value || preferredDeviceId('videoinput');
      const capture = {
        resolution: preset.resolution,
        ...(cameraId ? { deviceId: cameraId } : {}),
      };
      try {
        await live.localParticipant.setCameraEnabled(true, capture);
      } catch {
        await live.localParticipant.setCameraEnabled(true, { resolution: preset.resolution });
      }
      live.localParticipant.videoTrackPublications.forEach((pub) => {
        if (pub.track) attachVideoTrack(pub.track, live.localParticipant);
      });
      if (audioRoute !== 'speaker') applyAudioRoute('speaker', { toast: true });
    } else {
      await live.localParticipant.setCameraEnabled(false);
      clearLocalVideo();
    }
  } catch (err) {
    setStatus(String(err?.message || 'Camera unavailable'));
    return;
  } finally {
    modeSwitching = false;
  }
  activeMode = next;
  syncCallModeUi();
  if (activeCallConvId) signalCall('call_mode', activeCallConvId, callId, { mode: next });
}

function applyRemoteCallMode(mode) {
  const next = mode === 'video' ? 'video' : 'audio';
  if (next === activeMode) {
    syncCallModeUi();
    return;
  }
  activeMode = next;
  if (next === 'audio') {
    const live = room;
    if (live?.localParticipant?.isCameraEnabled) {
      void live.localParticipant.setCameraEnabled(false);
    }
    clearLocalVideo();
    const remote = document.getElementById('chatCallRemoteTile');
    remote?.querySelectorAll('video').forEach((node) => node.remove());
    remote?.classList.remove('has-video');
  } else if (audioRoute !== 'speaker') {
    applyAudioRoute('speaker', { toast: true });
  }
  syncCallModeUi();
}

function sameCall(data) {
  const conv = String(data?.conversation_id || '');
  if (!conv) return false;
  if (activeCallConvId && conv !== activeCallConvId && incoming?.conversation_id !== conv) return false;
  if (data.call_id && callId && data.call_id !== callId) return false;
  return phase !== 'idle' || Boolean(incoming);
}

function endsThisCall(data) {
  if (data?.call_id && callId && data.call_id !== callId) return false;
  return sameCall(data) || endsActiveCall(data);
}

async function dropAsBusy() {
  if (phase !== 'outgoing') return;
  await endLocal('No answer', true);
}

function setVoiceVizActive() {}

function identityBase(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  return raw.split(':')[0].trim();
}

function threadPeerName() {
  const title = document.querySelector('.chat-thread-title');
  return (title?.textContent || '').replace('🔒', '').trim();
}

function otherLabel() {
  const me = identityBase(meName());
  const titled = threadPeerName();
  const titledOk = titled && identityBase(titled) !== me ? titled : '';
  if (room) {
    for (const remote of room.remoteParticipants.values()) {
      const raw = String(remote?.name || '').trim() || String(remote?.identity || '').trim();
      const base = identityBase(raw);
      if (!base || base === me) continue;
      return base;
    }
  }
  if (incoming?.from) {
    const from = String(incoming.from).trim();
    if (from && identityBase(from) !== me) return from;
  }
  return titledOk || 'Other';
}

function paintPeopleNames() {
  const peer = otherLabel();
  const localFace = document.getElementById('chatCallLocalFace');
  const localName = document.getElementById('chatCallLocalName');
  const remoteFace = document.getElementById('chatCallRemoteFace');
  const remoteName = document.getElementById('chatCallRemoteName');
  if (localFace) localFace.textContent = 'You';
  if (localName) localName.textContent = 'You';
  if (remoteFace) remoteFace.textContent = peer;
  if (remoteName) remoteName.textContent = peer;
}

function participantMuted(participant) {
  if (!participant) return false;
  if (participant.isMicrophoneEnabled === false) return true;
  let muted = false;
  participant.audioTrackPublications?.forEach((pub) => {
    if (pub.isMuted) muted = true;
  });
  return muted;
}

function paintSpeaking() {
  const live = room;
  const local = live?.localParticipant;
  const speaking = (participant) => Boolean(
    participant && !participantMuted(participant) && (participant.isSpeaking || (participant.audioLevel || 0) > 0.04),
  );
  document.getElementById('chatCallLocalTile')?.classList.toggle('is-speaking', speaking(local));
  document.getElementById('chatCallLocalTile')?.classList.toggle('is-muted', participantMuted(local));
  const remotes = live ? [...live.remoteParticipants.values()] : [];
  remotes.forEach((participant) => {
    const tile = remoteTileFor(participant);
    tile?.classList.toggle('is-speaking', speaking(participant));
    tile?.classList.toggle('is-muted', participantMuted(participant));
  });
}

function paintLevel() {}

function stopMeters() {
  cancelAnimationFrame(meterRaf);
  meterRaf = 0;
  meters = { local: null, remote: null };
  if (meterCtx) {
    void meterCtx.close().catch(() => {});
    meterCtx = null;
  }
  paintSpeaking();
}

function paintMeters() {
  paintPeopleNames();
  paintSpeaking();
  if (callMinimized) updateMinimizedBar();
  meterRaf = requestAnimationFrame(paintMeters);
}

function watchAudio(track, which) {
  if (!track || track.kind !== 'audio') return;
  try {
    if (!meterCtx) meterCtx = new AudioContext();
    const stream = new MediaStream([track.mediaStreamTrack]);
    const source = meterCtx.createMediaStreamSource(stream);
    const analyser = meterCtx.createAnalyser();
    analyser.fftSize = 64;
    source.connect(analyser);
    meters[which] = analyser;
    if (!meterRaf) meterRaf = requestAnimationFrame(paintMeters);
    if (meterCtx.state === 'suspended') void meterCtx.resume();
  } catch { /* meter is optional */ }
}

function watchLocalMic() {
  const live = room;
  if (!live?.localParticipant) return;
  live.localParticipant.audioTrackPublications.forEach((pub) => {
    if (pub.track) watchAudio(pub.track, 'local');
  });
}

function deviceStorageKey(kind) {
  if (kind === 'audioinput') return 'aird-call-mic';
  if (kind === 'videoinput') return 'aird-call-camera';
  return 'aird-call-speaker';
}

function preferredDeviceId(kind) {
  try {
    return localStorage.getItem(deviceStorageKey(kind)) || '';
  } catch {
    return '';
  }
}

function savePreferredDevice(kind, deviceId) {
  try {
    if (deviceId) localStorage.setItem(deviceStorageKey(kind), deviceId);
  } catch { /* ignore */ }
}

async function applySpeakerToAttachedAudio(deviceId) {
  const id = deviceId || preferredDeviceId('audiooutput');
  if (!id) return;
  document.querySelectorAll('audio').forEach((el) => {
    if (typeof el.setSinkId !== 'function') return;
    void el.setSinkId(id).catch(() => {});
  });
}

function attachRemoteAudio(track) {
  if (!track || track.kind !== 'audio') return;
  preferLoudspeaker();
  const el = ensureSpeakerElement();
  el.removeAttribute('src');
  el.src = '';
  track.attach(el);
  el.volume = 1;
  el.muted = false;
  void el.play().catch(() => { syncHearButton(); });
  void room?.startAudio().then(() => syncHearButton()).catch(() => { syncHearButton(); });
}

function clearRemoteAudio() {
  const sink = document.getElementById('chatCallSpeakerSink');
  if (sink) {
    sink.pause();
    sink.srcObject = null;
    sink.removeAttribute('src');
  }
  document.querySelectorAll('audio.chat-call-remote-audio').forEach((el) => {
    if (el.id === 'chatCallSpeakerSink') return;
    el.pause();
    el.remove();
  });
}

async function refreshDeviceSelectors() {
  const micSel = document.getElementById('chatCallMicSelect');
  const spkSel = document.getElementById('chatCallSpeakerSelect');
  const camSel = document.getElementById('chatCallCamSelect');
  if (!micSel || !navigator.mediaDevices?.enumerateDevices) return;
  const devices = await navigator.mediaDevices.enumerateDevices();
  const emptyLabel = {
    audioinput: 'No microphone found',
    audiooutput: 'System speaker',
    videoinput: 'No camera found',
  };
  const fallback = {
    audioinput: 'Microphone',
    audiooutput: 'Speaker',
    videoinput: 'Camera',
  };
  const fill = (sel, kind, stored) => {
    if (!sel) return;
    const list = devices.filter((d) => d.kind === kind && d.deviceId);
    const prev = sel.value || stored;
    sel.replaceChildren();
    if (!list.length) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = emptyLabel[kind];
      sel.appendChild(opt);
      sel.disabled = true;
      return;
    }
    sel.disabled = false;
    list.forEach((d, i) => {
      const opt = document.createElement('option');
      opt.value = d.deviceId;
      opt.textContent = d.label || `${fallback[kind]} ${i + 1}`;
      sel.appendChild(opt);
    });
    const pick = list.some((d) => d.deviceId === prev) ? prev : list[0].deviceId;
    sel.value = pick;
  };
  fill(micSel, 'audioinput', preferredDeviceId('audioinput'));
  fill(spkSel, 'audiooutput', preferredDeviceId('audiooutput'));
  fill(camSel, 'videoinput', preferredDeviceId('videoinput'));
}

async function applySelectedMic() {
  const sel = document.getElementById('chatCallMicSelect');
  const id = sel?.value;
  if (!id || !room) return;
  savePreferredDevice('audioinput', id);
  await room.switchActiveDevice('audioinput', id);
}

async function applySelectedSpeaker() {
  const sel = document.getElementById('chatCallSpeakerSelect');
  const id = sel?.value;
  const label = sel?.selectedOptions?.[0]?.textContent || '';
  const route = routeFromLabel(label) || (audioRoute === 'speaker' ? 'headset' : 'speaker');
  if (id && room) {
    savePreferredDevice('audiooutput', id);
    await room.switchActiveDevice('audiooutput', id).catch(() => {});
    await applySpeakerToAttachedAudio(id);
  }
  applyAudioRoute(route, { toast: true });
}

async function applySelectedCamera() {
  const sel = document.getElementById('chatCallCamSelect');
  const id = sel?.value;
  if (!id || !room) return;
  savePreferredDevice('videoinput', id);
  await room.switchActiveDevice('videoinput', id).catch(() => {});
}

async function applySavedAudioDevices() {
  const live = room;
  if (!live) return;
  await refreshDeviceSelectors();
  const micSel = document.getElementById('chatCallMicSelect');
  const spkSel = document.getElementById('chatCallSpeakerSelect');
  const camSel = document.getElementById('chatCallCamSelect');
  if (micSel?.value) {
    savePreferredDevice('audioinput', micSel.value);
    await live.switchActiveDevice('audioinput', micSel.value).catch(() => {});
  }
  if (spkSel?.value && !isPhone()) {
    const label = spkSel.selectedOptions?.[0]?.textContent || '';
    const guessed = routeFromLabel(label);
    if (guessed) audioRoute = guessed;
    savePreferredDevice('audiooutput', spkSel.value);
    await live.switchActiveDevice('audiooutput', spkSel.value).catch(() => {});
    await applySpeakerToAttachedAudio(spkSel.value);
  }
  applyAudioRoute(audioRoute);
  if (activeMode === 'video' && camSel?.value) {
    savePreferredDevice('videoinput', camSel.value);
    await live.switchActiveDevice('videoinput', camSel.value).catch(() => {});
  }
}

function attachExistingRemoteAudio(liveRoom) {
  liveRoom?.remoteParticipants?.forEach((participant) => {
    participant.audioTrackPublications.forEach((pub) => {
      if (pub.track) attachRemoteAudio(pub.track);
    });
  });
}

function reportCallError(err) {
  const msg = String(err?.message || '');
  if (!msg || /disconnect|localParticipant|remoteParticipants|abort|cancel/i.test(msg)) return;
  showOverlay(true);
  setStatus(msg);
}

async function ensureMediaPermission(mode) {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('This browser cannot use the microphone');
  }
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: true,
    video: mode === 'video',
  });
  stream.getTracks().forEach((track) => track.stop());
}

function beginMediaPermission(mode) {
  return ensureMediaPermission(mode);
}

async function loadLiveKit() {
  if (livekit) return livekit;
  livekit = await import('./vendor/livekit-client.bundle.js');
  return livekit;
}

function setStatus(text) {
  const el = document.querySelector('#chatCallStatus');
  if (el) el.textContent = text;
  if (callMinimized) updateMinimizedBar();
}

function showOverlay(show) {
  const overlay = ensureOverlay();
  const mini = ensureMinimizedBar();
  if (!show) {
    callMinimized = false;
    closeDeviceMenu();
    overlay.classList.add('hidden');
    mini.classList.add('hidden');
    return;
  }
  if (callMinimized && room) {
    overlay.classList.add('hidden');
    mini.classList.remove('hidden');
    updateMinimizedBar();
    return;
  }
  callMinimized = false;
  mini.classList.add('hidden');
  overlay.classList.remove('hidden');
  syncHearButton();
  paintRouteButton();
}

function readVideoFit() {
  try {
    const saved = sessionStorage.getItem('aird-call-video-fit');
    if (saved === 'fit' || saved === 'fill') return saved;
  } catch { /* private mode */ }
  return 'fit';
}

function layoutFittedVideo(node) {
  if (!node) return;
  const remote = node.closest('.chat-call-tile--remote');
  if (!remote || videoFit !== 'fit') {
    node.style.removeProperty('--call-vw');
    node.style.removeProperty('--call-vh');
    return;
  }
  const tile = node.parentElement;
  const vw = node.videoWidth;
  const vh = node.videoHeight;
  if (!tile || !vw || !vh) return;
  const scale = Math.min(tile.clientWidth / vw, tile.clientHeight / vh, 1);
  node.style.setProperty('--call-vw', `${Math.max(1, Math.round(vw * scale))}px`);
  node.style.setProperty('--call-vh', `${Math.max(1, Math.round(vh * scale))}px`);
}

function applyVideoFit(mode) {
  videoFit = mode === 'fill' ? 'fill' : 'fit';
  try { sessionStorage.setItem('aird-call-video-fit', videoFit); } catch { /* private mode */ }
  document.querySelector('#chatCallOverlay .chat-call-stage')?.classList.toggle('is-fit', videoFit === 'fit');
  const btn = document.getElementById('chatCallFitBtn');
  if (btn) {
    const fitting = videoFit === 'fit';
    paintCallCtrl(btn, fitting ? 'Fit' : 'Fill', fitting ? FIT_ICON : FILL_ICON);
    btn.setAttribute('aria-pressed', fitting ? 'true' : 'false');
    btn.setAttribute('aria-label', fitting ? 'Fit' : 'Fill');
    btn.title = fitting
      ? 'Whole frame, not enlarged past the camera size'
      : 'Fill the screen';
  }
  document.querySelectorAll('#chatCallOverlay video').forEach(layoutFittedVideo);
}

function watchFitLayout(root) {
  const stage = root.querySelector('.chat-call-stage');
  if (!stage || stage.dataset.fitWatch === '1' || !window.ResizeObserver) return;
  stage.dataset.fitWatch = '1';
  new ResizeObserver(() => {
    stage.querySelectorAll('video').forEach(layoutFittedVideo);
  }).observe(stage);
}

function remoteTileFor(participant) {
  if (!participant || participant.isLocal) return document.getElementById('chatCallLocalTile');
  const identity = String(participant.identity || participant.sid || 'guest');
  const safe = identity.replace(/[^a-zA-Z0-9_-]/g, '_');
  const grid = document.getElementById('chatCallRemoteGrid');
  if (!grid) return document.getElementById('chatCallRemoteTile');
  let tile = document.getElementById(`chatCallRemote-${safe}`);
  if (!tile) {
    tile = document.createElement('div');
    tile.className = 'chat-call-tile chat-call-tile--remote';
    tile.id = `chatCallRemote-${safe}`;
    tile.dataset.identity = identity;
    const label = participant.name || identity.split(':')[0] || 'Guest';
    tile.innerHTML = tileMarkup('remote', label);
    const legacy = document.getElementById('chatCallRemoteTile');
    if (legacy && legacy.parentElement === grid && !legacy.querySelector('video')) legacy.remove();
    grid.appendChild(tile);
    grid.classList.toggle('is-multi', grid.querySelectorAll('.chat-call-tile--remote').length > 1);
  }
  return tile;
}

function attachVideoTrack(track, participant) {
  const local = Boolean(participant?.isLocal);
  const tile = local ? document.getElementById('chatCallLocalTile') : remoteTileFor(participant);
  if (!tile || !track) return;
  let node = tile.querySelector('video');
  if (!node) {
    node = document.createElement('video');
    node.className = 'chat-call-video';
    node.autoplay = true;
    node.playsInline = true;
    node.muted = true;
    tile.prepend(node);
  }
  node.classList.toggle('chat-call-video--mirror', local);
  if (local) {
    node.style.setProperty('transform', 'scaleX(-1)', 'important');
    node.style.setProperty('-webkit-transform', 'scaleX(-1)', 'important');
  } else {
    node.style.removeProperty('transform');
    node.style.removeProperty('-webkit-transform');
  }
  track.attach(node);
  const markLive = () => {
    if (node.videoWidth > 0) tile.classList.add('has-video');
    layoutFittedVideo(node);
  };
  node.onloadeddata = markLive;
  node.onresize = markLive;
  markLive();
  void node.play?.().catch(() => {});
}

function bindRemoteVideo(liveRoom) {
  liveRoom?.remoteParticipants?.forEach((participant) => {
    participant.trackPublications.forEach((pub) => {
      const video = pub.kind === 'video' || pub.track?.kind === 'video';
      if (!video) return;
      if (pub.track) attachVideoTrack(pub.track, participant);
      else if (pub.isSubscribed === false) pub.setSubscribed(true);
    });
  });
}

function clearVideos() {
  const local = document.getElementById('chatCallLocalTile');
  local?.querySelectorAll('video').forEach((node) => node.remove());
  local?.classList.remove('has-video', 'is-speaking');
  const grid = document.getElementById('chatCallRemoteGrid');
  if (grid) {
    grid.innerHTML = `<div class="chat-call-tile chat-call-tile--remote" id="chatCallRemoteTile">${tileMarkup('remote', 'Other')}</div>`;
    grid.classList.remove('is-multi');
  } else {
    const legacy = document.getElementById('chatCallRemoteTile');
    legacy?.querySelectorAll('video').forEach((node) => node.remove());
    legacy?.classList.remove('has-video', 'is-speaking');
  }
}

async function joinSession(session, opts = {}) {
  document.documentElement.dataset.airdCall = '1';
  const { url, token, mode } = session;
  const keepRing = Boolean(opts.keepRing);
  const { Room, RoomEvent, VideoPresets } = await loadLiveKit();
  activeMode = mode === 'video' ? 'video' : 'audio';
  notifyAndroidCall(true, activeMode);
  if (!keepRing) {
    stopRing();
    hideIncoming();
  }
  stopMeters();
  clearRemoteDropTimer();
  const previous = room;
  room = null;
  if (previous) {
    try { await previous.disconnect(); } catch { /* ignore */ }
  }
  clearVideos();
  clearRemoteAudio();
  callMinimized = false;
  showOverlay(true);
  if (!keepRing) setStatus('Joining…');
  paintPeopleNames();
  if (!meterRaf) meterRaf = requestAnimationFrame(paintMeters);
  const mobile = globalThis.matchMedia('(max-width: 1023px)').matches;
  room = new Room({
    disconnectOnPageLeave: false,
    adaptiveStream: false,
    dynacast: false,
    audioCaptureDefaults: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
    videoCaptureDefaults: {
      resolution: mobile ? VideoPresets.h540.resolution : VideoPresets.h720.resolution,
    },
    publishDefaults: {
      simulcast: false,
      videoCodec: 'vp8',
      backupCodec: false,
    },
  });
  room.on(RoomEvent.TrackSubscribed, (track, _pub, participant) => {
    if (track.kind === 'video') attachVideoTrack(track, participant);
    if (track.kind === 'audio') {
      if (!participant?.isLocal) attachRemoteAudio(track);
      watchAudio(track, participant?.isLocal ? 'local' : 'remote');
    }
  });
  room.on(RoomEvent.TrackPublished, (pub, participant) => {
    if (pub.kind !== 'video' || participant?.isLocal) return;
    if (pub.track) attachVideoTrack(pub.track, participant);
    else pub.setSubscribed(true);
  });
  room.on(RoomEvent.TrackUnsubscribed, (track, _pub, participant) => {
    track.detach().forEach((el) => el.remove());
    if (track.kind !== 'video') return;
    const tile = participant?.isLocal
      ? document.getElementById('chatCallLocalTile')
      : remoteTileFor(participant);
    if (tile && !tile.querySelector('video')) tile.classList.remove('has-video');
  });
  room.on(RoomEvent.ParticipantConnected, (participant) => {
    clearRemoteDropTimer();
    if (!participant.isLocal) markAnswered();
    paintPeopleNames();
    bindRemoteVideo(room);
    paintSpeaking();
  });
  room.on(RoomEvent.ParticipantDisconnected, (participant) => {
    const tile = participant ? remoteTileFor(participant) : null;
    tile?.remove();
    const grid = document.getElementById('chatCallRemoteGrid');
    grid?.classList.toggle('is-multi', (grid?.querySelectorAll('.chat-call-tile--remote').length || 0) > 1);
    const live = room;
    if (waitingForAnswer || !live?.remoteParticipants) return;
    if (live.remoteParticipants.size === 0) scheduleRemoteDropCheck();
  });
  room.on(RoomEvent.LocalTrackPublished, (pub, participant) => {
    if (pub.track?.kind === 'audio') watchAudio(pub.track, 'local');
    if (pub.track?.kind === 'video') attachVideoTrack(pub.track, participant || room?.localParticipant);
  });
  room.on(RoomEvent.TrackMuted, () => { paintSpeaking(); });
  room.on(RoomEvent.TrackUnmuted, () => { paintSpeaking(); });
  room.on(RoomEvent.AudioPlaybackStatusChanged, () => { syncHearButton(); });
  room.on(RoomEvent.LocalTrackUnpublished, (pub) => {
    if (pub.track?.kind !== 'video') return;
    pub.track.detach().forEach((el) => el.remove());
    const tile = document.getElementById('chatCallLocalTile');
    if (tile && !tile.querySelector('video')) tile.classList.remove('has-video');
  });
  const current = room;
  current.on(RoomEvent.Disconnected, () => {
    if (room !== current) return;
    room = null;
    if (phase === 'idle') return;
    void endLocal('Call ended', false);
  });
  try {
    await current.connect(url, token);
  } catch (err) {
    if (room === current) {
      room = null;
      document.documentElement.dataset.airdCall = '';
    }
    throw err;
  }
  if (phase === 'idle' || room !== current) {
    if (room === current || !room) document.documentElement.dataset.airdCall = '';
    try { await current.disconnect(); } catch { /* ignore */ }
    return;
  }
  if (!keepRing) setStatus(activeMode === 'video' ? 'Video call' : 'Voice call');
  window.removeEventListener('freeze', current.onPageLeave);
  window.removeEventListener('pagehide', current.onPageLeave);
  window.removeEventListener('beforeunload', current.onPageLeave);
  if (room !== current) return;
  await current.localParticipant.setMicrophoneEnabled(true);
  if (room !== current) return;
  watchLocalMic();
  attachExistingRemoteAudio(current);
  bindRemoteVideo(current);
  await current.startAudio().catch(() => {});
  syncHearButton();
  await applySavedAudioDevices();
  paintPeopleNames();
  if (activeMode === 'video') {
    const preset = mobile ? VideoPresets.h540 : VideoPresets.h720;
    const cameraId = preferredDeviceId('videoinput');
    const capture = {
      resolution: preset.resolution,
      ...(cameraId ? { deviceId: cameraId } : {}),
    };
    try {
      await current.localParticipant.setCameraEnabled(true, capture);
    } catch {
      await current.localParticipant.setCameraEnabled(true, { resolution: preset.resolution });
    }
    if (room !== current) return;
    current.localParticipant.videoTrackPublications.forEach((pub) => {
      if (pub.track) attachVideoTrack(pub.track, current.localParticipant);
    });
  }
  syncCallModeUi();
}

async function startCall(mode, permission, targets) {
  if (phase === 'outgoing' || phase === 'joining' || phase === 'live') return;
  const convId = ctx?.getActiveConvId?.();
  if (!convId || !ctx?.api) return;
  if (phase === 'incoming') declineIncoming();
  phase = 'outgoing';
  activeCallConvId = convId;
  activeMode = mode === 'video' ? 'video' : 'audio';
  notifyAndroidCall(true, activeMode);
  callId = '';
  waitingForAnswer = true;
  showOverlay(true);
  paintPeopleNames();
  setStatus('Calling…');
  startRing('ringback');
  clearTimeout(busyTimer);
  busyTimer = setTimeout(() => { void dropAsBusy(); }, 30000);
  try {
    await (permission || ensureMediaPermission(activeMode));
    if (phase !== 'outgoing') return;
    const body = { mode: activeMode };
    if (Array.isArray(targets) && targets.length) body.targets = targets;
    const data = await ctx.api(`/api/chat/conversations/${convId}/call`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
    if (phase !== 'outgoing') return;
    callId = data.call_id || '';
    await joinSession(data, { keepRing: true });
    if (phase !== 'outgoing') return;
    if (room?.remoteParticipants?.size > 0) markAnswered();
  } catch (err) {
    if (phase === 'outgoing') await endLocal(String(err?.message || 'Call failed'), true);
  }
}

function cutCallNow(convId, endingId) {
  stopRing();
  hideIncoming();
  showOverlay(false);
  globalThis.__AIRD_PENDING_CALL__ = null;
  globalThis.__AIRD_HIDE_CALL_POPUP__?.();
  try { if (convId) globalThis.AndroidHost?.onCallEnded?.(convId); } catch { /* ignore */ }
  notifyAndroidCall(false, activeMode);
  try {
    navigator.serviceWorker?.controller?.postMessage({
      type: 'aird-call-clear',
      call_id: endingId || '',
      conversation_id: convId || '',
    });
  } catch { /* no worker */ }
}

async function endLocal(message, notify) {
  document.documentElement.dataset.airdCall = '';
  const convId = activeCallConvId || incoming?.conversation_id || '';
  const endingId = callId;
  const was = phase;
  phase = 'idle';
  callId = '';
  activeCallConvId = '';
  clearBusyTimer();
  clearRemoteDropTimer();
  cutCallNow(convId, endingId);
  if (notify && convId && was !== 'idle') signalCall('call_end', convId, endingId);
  const current = room;
  room = null;
  stopMeters();
  clearVideos();
  clearRemoteAudio();
  if (message && phase === 'idle' && !room) setStatus(message);
  if (current) void current.disconnect().catch(() => {});
}

async function hangUp() {
  await endLocal('', true);
}

async function toggleMute() {
  const live = room;
  if (!live?.localParticipant) return;
  const enabled = live.localParticipant.isMicrophoneEnabled;
  await live.localParticipant.setMicrophoneEnabled(!enabled);
  const btn = document.getElementById('chatCallMuteBtn');
  if (btn) {
    paintCallCtrl(btn, enabled ? 'Unmute' : 'Mute', enabled ? MIC_OFF_ICON : MIC_ICON);
    btn.setAttribute('aria-label', enabled ? 'Unmute' : 'Mute');
  }
}

async function toggleCamera() {
  const live = room;
  if (!live?.localParticipant) return;
  const enabled = live.localParticipant.isCameraEnabled;
  const cameraId = document.getElementById('chatCallCamSelect')?.value || preferredDeviceId('videoinput');
  await live.localParticipant.setCameraEnabled(!enabled, cameraId ? { deviceId: cameraId } : undefined);
  const btn = document.getElementById('chatCallCamBtn');
  if (btn) {
    const on = !enabled;
    paintCallCtrl(btn, 'Camera', on ? CAM_ICON : CAM_OFF_ICON);
    btn.setAttribute('aria-label', on ? 'Camera off' : 'Camera on');
  }
}

function hideIncoming() {
  stopRing();
  incoming = null;
  document.getElementById('chatCallIncoming')?.classList.add('hidden');
  document.getElementById('chatCallPageBar')?.classList.add('hidden');
  document.getElementById('chatCallInChat')?.classList.add('hidden');
}

function callConvMatches(convId) {
  const id = String(convId || '');
  if (!id) return false;
  return id === activeCallConvId || id === incoming?.conversation_id || id === ctx?.getActiveConvId?.();
}

function revealIncoming(payload) {
  const label = payload.mode === 'video' ? 'Video call' : 'Voice call';
  const message = `${label} from ${payload.from}. Accept or decline.`;
  const bar = ensureIncomingBar();
  const text = bar.querySelector('#chatCallIncomingText');
  if (text) text.textContent = message;
  document.body.appendChild(bar);
  bar.classList.remove('hidden');
  bar.style.display = 'flex';
  document.getElementById('chatCallPageBar')?.classList.add('hidden');
  document.getElementById('chatCallInChat')?.classList.add('hidden');
  const layout = document.getElementById('chatLayout');
  if (layout?.classList.contains('chat-layout--list')) {
    layout.classList.remove('chat-layout--list');
    layout.classList.add('chat-layout--thread');
  }
  startRing('incoming');
}

function callSilenced(id) {
  try { return JSON.parse(sessionStorage.getItem('aird-silenced-calls') || '[]').includes(id); }
  catch { return false; }
}

function silenceCall(id) {
  if (!id) return;
  let cur = [];
  try { cur = JSON.parse(sessionStorage.getItem('aird-silenced-calls') || '[]'); }
  catch { cur = []; }
  if (!cur.includes(id)) cur.push(id);
  sessionStorage.setItem('aird-silenced-calls', JSON.stringify(cur.slice(-30)));
}

function showIncoming(payload) {
  if (!payload || (payload.type && payload.type !== 'call_invite')) return;
  const from = String(payload.from || payload.caller || payload.sender || '').trim();
  const call_id = String(payload.call_id || '').trim();
  const conversation_id = String(payload.conversation_id || '').trim();
  if (!call_id || !conversation_id) return;
  if (from && identityBase(from) === identityBase(meName())) return;
  payload = { ...payload, type: 'call_invite', from: from || 'Someone', call_id, conversation_id };
  if (callSilenced(payload.call_id)) return;
  if (payload.call_id && callId && payload.call_id === callId && phase === 'incoming') {
    const bar = document.getElementById('chatCallIncoming');
    if (bar && !bar.classList.contains('hidden')) return;
    revealIncoming(payload);
    return;
  }
  if (payload.call_id && callId && payload.call_id === callId && phase !== 'idle') return;
  if (phase === 'outgoing' || phase === 'joining' || phase === 'live') {
    if (!payload.conversation_id || payload.conversation_id === activeCallConvId) return;
    signalCall('call_decline', payload.conversation_id, payload.call_id || '');
    return;
  }
  phase = 'incoming';
  incoming = payload;
  activeCallConvId = payload.conversation_id || activeCallConvId;
  callId = payload.call_id || '';
  globalThis.__AIRD_PENDING_CALL__ = payload;
  revealIncoming(payload);
  const convId = payload.conversation_id;
  const cur = ctx?.getActiveConvId?.();
  if (
    convId && convId !== cur
    && document.visibilityState === 'visible'
    && ctx?.openConversation
    && location.pathname === '/chat'
  ) {
    void ctx.openConversation(convId);
  }
}

async function acceptIncoming(permission) {
  const payload = incoming;
  if (!payload || phase === 'joining' || phase === 'live' || phase === 'outgoing') return;
  if (!ctx?.api) {
    globalThis.location.href = `/chat?c=${encodeURIComponent(payload.conversation_id)}`;
    return;
  }
  const convId = payload.conversation_id;
  const mode = payload.mode === 'video' ? 'video' : 'audio';
  phase = 'joining';
  activeCallConvId = convId;
  activeMode = mode;
  callId = payload.call_id || callId;
  clearBusyTimer();
  stopRing();
  incoming = null;
  document.getElementById('chatCallIncoming')?.classList.add('hidden');
  document.getElementById('chatCallPageBar')?.classList.add('hidden');
  document.getElementById('chatCallInChat')?.classList.add('hidden');
  globalThis.__AIRD_HIDE_CALL_POPUP__?.();
  globalThis.__AIRD_PENDING_CALL__ = null;
  showOverlay(true);
  paintPeopleNames();
  setStatus('Joining…');
  try {
    await (permission || ensureMediaPermission(mode));
    if (phase !== 'joining') return;
    const cur = ctx?.getActiveConvId?.();
    if (ctx.openConversation && location.pathname === '/chat' && cur !== convId) {
      await ctx.openConversation(convId);
    }
    const data = await ctx.api(`/api/chat/conversations/${convId}/call`, {
      method: 'POST',
      body: JSON.stringify({ mode, invite: false, call_id: callId }),
    });
    if (phase !== 'joining') return;
    callId = data.call_id || callId;
    await joinSession(data);
    if (phase !== 'joining') return;
    phase = 'live';
    silenceCall(callId);
    setStatus(mode === 'video' ? 'Video call' : 'Voice call');
    signalCall('call_accept', convId, callId);
  } catch (err) {
    const blocked = err?.name === 'NotAllowedError' || /not allowed|permission|denied/i.test(String(err?.message || ''));
    if (phase === 'joining' && blocked && payload) {
      phase = 'incoming';
      incoming = payload;
      callId = payload.call_id || callId;
      activeCallConvId = payload.conversation_id || activeCallConvId;
      globalThis.__AIRD_PENDING_CALL__ = payload;
      revealIncoming(payload);
      globalThis.__AIRD_SHOW_CALL_POPUP__?.(payload);
      const text = document.getElementById('chatCallIncomingText');
      if (text) text.textContent = `${payload.from} is calling · tap Accept`;
      showOverlay(false);
      return;
    }
    if (phase === 'joining') await endLocal(String(err?.message || 'Could not join'), true);
  }
}

function declineIncoming() {
  const conv = incoming?.conversation_id;
  const id = incoming?.call_id || callId;
  silenceCall(id);
  if (conv) signalCall('call_decline', conv, id);
  phase = 'idle';
  callId = '';
  activeCallConvId = '';
  globalThis.__AIRD_PENDING_CALL__ = null;
  globalThis.__AIRD_HIDE_CALL_POPUP__?.();
  hideIncoming();
}

function endsActiveCall(data) {
  const conv = String(data?.conversation_id || '');
  if (!conv || phase === 'idle') return false;
  return conv === activeCallConvId || conv === incoming?.conversation_id;
}

function onWs(data) {
  if (!data?.type) return;
  if (data.type === 'call_invite') showIncoming(data);
  if (data.type === 'call_accept' && sameCall(data) && phase === 'outgoing') markAnswered();
  if (data.type === 'call_decline' && endsThisCall(data)) {
    if (phase === 'outgoing') void endLocal('Busy', false);
    else if (phase === 'incoming' || phase === 'joining') void endLocal('', false);
    else if (phase !== 'idle') void endLocal('Call ended', false);
    return;
  }
  if (data.type === 'call_end' && endsThisCall(data) && phase !== 'idle') {
    void endLocal(phase === 'outgoing' ? 'Busy' : 'Call ended', false);
  }
  if (data.type === 'call_missed' && endsThisCall(data) && phase !== 'idle') {
    void endLocal(phase === 'outgoing' ? 'No answer' : '', false);
  }
  if (data.type === 'call_answered' && phase === 'incoming' && endsThisCall(data)) {
    void endLocal('', false);
  }
  if (data.type === 'call_mode' && (sameCall(data) || endsActiveCall(data)) && phase !== 'idle' && phase !== 'incoming') {
    applyRemoteCallMode(data.mode);
  }
}

function onNotificationAction(action, payload) {
  if (!payload?.call_id || !payload.conversation_id) return;
  if (action === 'decline') {
    if (phase === 'idle') showIncoming(payload);
    if (phase === 'incoming' && (!callId || callId === payload.call_id)) declineIncoming();
    return;
  }
  if (action === 'accept') {
    if (phase === 'idle') showIncoming(payload);
    if (phase !== 'incoming') return;
    unlockCallAudio();
    const permission = beginMediaPermission(payload.mode === 'video' ? 'video' : 'audio');
    acceptIncoming(permission).catch((err) => reportCallError(err));
    return;
  }
  if (phase === 'idle') showIncoming(payload);
}

function bindHeaderButtons() {
  if (headerBound) return;
  headerBound = true;
  document.addEventListener('click', (event) => {
    const voice = event.target.closest?.('#chatVoiceCallBtn');
    const video = event.target.closest?.('#chatVideoCallBtn');
    const group = event.target.closest?.('#chatGroupCallBtn');
    if (!voice && !video && !group) return;
    event.preventDefault();
    primeAudio();
    unlockCallAudio();
    if (group) {
      startGroupCall('video');
      return;
    }
    const mode = video ? 'video' : 'audio';
    const permission = beginMediaPermission(mode);
    startCall(mode, permission).catch((err) => reportCallError(err));
  });
}

function takeUrlCallAction() {
  const params = new URLSearchParams(location.search);
  const action = params.get('call_action');
  const call_id = params.get('call_id');
  const conversation_id = params.get('c') || '';
  if (!action || !call_id || !conversation_id) return null;
  const payload = {
    type: 'call_invite',
    conversation_id,
    call_id,
    from: params.get('call_from') || 'Incoming call',
    mode: params.get('call_mode') === 'video' ? 'video' : 'audio',
  };
  params.delete('call_action');
  params.delete('call_id');
  params.delete('call_mode');
  params.delete('call_from');
  const next = params.toString();
  history.replaceState(null, '', `${location.pathname}${next ? `?${next}` : ''}${location.hash}`);
  return { action, payload };
}

export function initCalls(options) {
  ctx = { ...ctx, ...options };
  globalThis.__AIRD_SHOW_CALL__ = showIncoming;
  globalThis.__AIRD_ACCEPT_CALL__ = (payload) => {
    if (payload?.call_id) globalThis.__AIRD_SILENCE_CALL__ = String(payload.call_id);
    onNotificationAction('accept', payload);
  };
  globalThis.__AIRD_REJECT_CALL__ = (payload) => { onNotificationAction('decline', payload); };
  ensureCallStyles();
  ensureOverlay();
  ensureIncomingBar();
  if (!globalThis.__AIRD_CALL_DEVICE_LISTENER__) {
    globalThis.__AIRD_CALL_DEVICE_LISTENER__ = true;
    navigator.mediaDevices?.addEventListener('devicechange', () => {
      if (room) void refreshDeviceSelectors();
    });
  }
  if (!globalThis.__AIRD_CALL_VISIBILITY_LISTENER__) {
    globalThis.__AIRD_CALL_VISIBILITY_LISTENER__ = true;
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'visible') return;
      const pending = globalThis.__AIRD_PENDING_CALL__;
      if (pending?.type === 'call_invite' && (phase === 'idle' || phase === 'incoming')) showIncoming(pending);
      if (room) void room.startAudio().catch(() => {});
    });
    globalThis.__AIRD_SHOW_CALL__ = showIncoming;
  }
  if (!wsBound) {
    wsBound = true;
    globalThis.AirdChatWS?.subscribe(onWs);
    globalThis.addEventListener('aird-call-invite', (e) => {
      if (e.detail) onWs(e.detail);
    });
    globalThis.addEventListener('aird-call-action', (e) => {
      const action = e.detail?.action;
      const payload = e.detail?.payload;
      if (action && payload) onNotificationAction(action, payload);
    });
  }
  bindHeaderButtons();
  const urlAction = takeUrlCallAction();
  if (urlAction) globalThis.__AIRD_PENDING_CALL__ = urlAction.payload;
  const pending = globalThis.__AIRD_PENDING_CALL__;
  if (pending) showIncoming(pending);
  if (urlAction?.action === 'accept' || urlAction?.action === 'decline') {
    onNotificationAction(urlAction.action, urlAction.payload);
  }
  navigator.serviceWorker?.controller?.postMessage({ type: 'aird-calls-ready' });
}

const CALL_VOICE_ICON = '<svg class="hdr-ico" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.8 19.8 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/></svg>';
const CALL_VIDEO_ICON = '<svg class="hdr-ico" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true"><path d="m16 13 5.2 3.1a.8.8 0 0 0 1.2-.7V8.6a.8.8 0 0 0-1.2-.7L16 11"/><rect x="2" y="6" width="14" height="12" rx="2"/></svg>';

export async function joinStandaloneCall(session, { title } = {}) {
  phase = 'live';
  activeCallConvId = '';
  waitingForAnswer = false;
  showOverlay(true);
  setStatus(title || 'Meeting');
  await joinSession(session, {});
}

export function startGroupCall(mode) {
  const conv = ctx?.getActiveConv?.() || null;
  const members = (conv?.members || []).map((m) => m.username).filter((n) => n && n !== meName());
  if (!members.length) {
    alert('No members to invite.');
    return;
  }
  const picked = prompt(`Group call — invite (comma-separated):\n${members.join(', ')}`, members.join(', '));
  if (picked == null) return;
  const targets = picked.split(',').map((s) => s.trim()).filter(Boolean);
  if (!targets.length) return;
  const permission = beginMediaPermission(mode === 'video' ? 'video' : 'audio');
  startCall(mode === 'video' ? 'video' : 'audio', permission, targets).catch((err) => reportCallError(err));
}

export function headerCallButtonsHtml() {
  return `<button type="button" id="chatVoiceCallBtn" class="btn btn-ghost btn-sm btn-square chat-call-btn" title="Voice call" aria-label="Voice call">${CALL_VOICE_ICON}</button>`
    + `<button type="button" id="chatVideoCallBtn" class="btn btn-ghost btn-sm btn-square chat-call-btn" title="Video call" aria-label="Video call">${CALL_VIDEO_ICON}</button>`
    + `<button type="button" id="chatGroupCallBtn" class="btn btn-ghost btn-sm chat-call-btn hidden sm:inline-flex" title="Group call" aria-label="Group call">Group</button>`;
}
