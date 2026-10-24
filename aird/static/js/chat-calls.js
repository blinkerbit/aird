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

function meName() {
  return ctx?.me || globalThis.__AIRD_CHAT_USER__ || '';
}

function ensureCallStyles() {
  const existing = document.getElementById('chatCallStyles');
  if (existing?.dataset.version === '11') return;
  existing?.remove();
  const style = document.createElement('style');
  style.id = 'chatCallStyles';
  style.dataset.version = '11';
  style.textContent = `
    .chat-call-btn{display:inline-flex;align-items:center;justify-content:center;width:2.15rem;height:2.15rem;padding:0;border:0;border-radius:999px;background:transparent;color:color-mix(in oklch,var(--color-base-content) 70%,transparent);box-shadow:none;cursor:pointer}
    .chat-call-btn svg{width:1.15rem;height:1.15rem}
    .chat-call-btn:hover{background:color-mix(in oklch,var(--color-primary) 14%,transparent);color:var(--color-primary)}
    .chat-call-incoming{position:fixed;inset:0;z-index:2147483000;display:flex !important;align-items:center;justify-content:center;padding:1rem;background:rgba(0,0,0,.55)}
    .chat-call-pagebar{position:fixed;top:0;left:0;right:0;z-index:2147483001;display:flex !important;align-items:center;justify-content:space-between;gap:.75rem;padding:calc(.65rem + env(safe-area-inset-top,0)) .8rem .65rem;background:#111827;color:#fff;box-shadow:0 8px 24px rgba(0,0,0,.28)}
    .chat-call-pagebar p{margin:0;font-weight:700;font-size:.95rem}
    .chat-call-pagebar div{display:flex;gap:.45rem;flex-shrink:0}
    .chat-call-overlay.hidden,.chat-call-incoming.hidden,.chat-call-pagebar.hidden,.chat-call-minimized.hidden,.chat-call-device-menu.hidden,.chat-call-inchat.hidden{display:none !important}
    .chat-call-inchat{display:flex !important;align-items:center;justify-content:space-between;gap:.75rem;margin:.65rem .75rem 0;padding:.9rem 1rem;border-radius:.9rem;background:#111827;color:#fff;position:relative;z-index:6}
    .chat-call-inchat p{margin:0;font-weight:700}
    .chat-call-inchat div{display:flex;gap:.45rem;flex-shrink:0}
    .chat-call-modal-card{width:min(22rem,92vw);padding:1.15rem;border-radius:1.1rem;background:var(--color-base-100,#fff);text-align:center;box-shadow:0 18px 48px rgba(0,0,0,.32)}
    .chat-call-overlay{position:fixed;inset:0;z-index:90;display:flex !important;flex-direction:column !important;align-items:stretch !important;justify-content:flex-start !important;width:100% !important;height:100% !important;background:#0c0c10 !important;color:#fff}
    .chat-call-top{display:flex;align-items:center;justify-content:space-between;gap:.75rem;width:100%;padding:.7rem .8rem;padding-top:calc(.7rem + env(safe-area-inset-top,0));position:relative;z-index:4}
    .chat-call-status{margin:0;font-size:.85rem;font-weight:600;opacity:.8}
    .chat-call-top-actions{display:flex;gap:.4rem}
    .chat-call-iconbtn{width:2.2rem;height:2.2rem;border:0;border-radius:999px;background:rgba(255,255,255,.14);color:#fff;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;font-size:1.15rem;line-height:1}
    .chat-call-iconbtn svg{width:1.05rem;height:1.05rem}
    .chat-call-device-menu{position:absolute;top:calc(3.2rem + env(safe-area-inset-top,0));right:.7rem;z-index:6;width:min(18rem,calc(100vw - 1.4rem));padding:.75rem;border-radius:.85rem;background:#1b1b22;box-shadow:0 16px 40px rgba(0,0,0,.45);display:grid;gap:.55rem}
    .chat-call-device-menu label{display:grid;gap:.25rem;font-size:.75rem;font-weight:650;text-align:left}
    .chat-call-device-menu select{width:100%;padding:.4rem .45rem;border-radius:.45rem;border:1px solid rgba(255,255,255,.16);background:#111;color:#fff;font:inherit}
    .chat-call-stage{position:relative;flex:1;width:100%;min-height:0}
    .chat-call-tile{position:absolute;overflow:hidden;background:#17171d}
    .chat-call-tile--remote{inset:0}
    .chat-call-tile--local{right:.75rem;bottom:.75rem;width:min(34vw,13.5rem);aspect-ratio:3/4;border-radius:.85rem;box-shadow:0 10px 28px rgba(0,0,0,.45);z-index:2;border:1px solid rgba(255,255,255,.12)}
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
    .chat-call-tile--local video{transform:scaleX(-1)}
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
    .chat-call-popup-btn,.chat-call-ctrl{min-width:6.2rem;padding:.7rem 1rem;border:0;border-radius:999px;font-weight:700;color:#fff;cursor:pointer}
    .chat-call-popup-btn--accept{background:#16a34a}
    .chat-call-popup-btn--decline,.chat-call-ctrl--danger{background:#dc2626}
    .chat-call-ctrl{background:#e5e7eb;color:#111}
    .chat-call-ctrl[aria-pressed="true"]{background:#2563eb;color:#fff}
    .chat-call-controls{display:flex;flex-wrap:wrap;gap:.5rem;justify-content:center;width:100%;position:relative;z-index:3;padding:.75rem .8rem;padding-bottom:calc(.8rem + env(safe-area-inset-bottom,0))}
    .chat-call-minimized{position:fixed;z-index:91;left:50%;transform:translateX(-50%);bottom:calc(.75rem + env(safe-area-inset-bottom,0));display:flex;align-items:center;gap:.5rem;padding:.55rem .65rem;border-radius:999px;background:var(--color-primary,#2563eb);color:#fff;box-shadow:0 8px 28px rgba(0,0,0,.28);min-width:min(92vw,20rem);max-width:92vw;cursor:pointer}
    .chat-call-minimized-pulse{width:.55rem;height:.55rem;border-radius:50%;background:#4ade80;flex-shrink:0}
    .chat-call-minimized.is-live .chat-call-minimized-pulse{animation:chat-call-pulse 1s ease infinite}
    @keyframes chat-call-pulse{50%{opacity:.35}}
    .chat-call-minimized-label{flex:1;font-weight:700;font-size:.85rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:left}
    .chat-call-minimized-actions{display:flex;gap:.35rem;flex-shrink:0}
    .chat-call-minimized-actions button{background:rgba(255,255,255,.22);border:0;color:#fff;border-radius:999px;padding:.3rem .55rem;font-size:.75rem;font-weight:700;cursor:pointer}
  `;
  document.head.appendChild(style);
}

function muteIcon() {
  return `<span class="chat-call-mute" aria-label="Microphone off"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><line x1="2" y1="2" x2="22" y2="22"/><path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V5a3 3 0 0 0-5.94-.6"/><path d="M17 16.95A7 7 0 0 1 5 12v-2m14 0v2c0 .28-.02.56-.05.83"/><line x1="12" y1="19" x2="12" y2="22"/><line x1="8" y1="22" x2="16" y2="22"/></svg></span>`;
}

const CALL_MINIMIZE_ICON = '<svg class="icon-hide" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M5 12h14"/></svg><svg class="icon-show" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 14v6h6M20 10V4h-6M14 10l6-6M10 14l-6 6"/></svg>';

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

function ensureOverlay() {
  const existing = document.getElementById('chatCallOverlay');
  if (existing && existing.dataset.version === '9') return existing;
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
      <div class="chat-call-tile chat-call-tile--remote" id="chatCallRemoteTile">${tileMarkup('remote', 'Other')}</div>
      <div class="chat-call-tile chat-call-tile--local" id="chatCallLocalTile">${tileMarkup('local', 'You')}</div>
    </div>
    <div class="chat-call-controls">
      <button type="button" id="chatCallHearBtn" class="chat-call-ctrl hidden">Speaker</button>
      <button type="button" id="chatCallMuteBtn" class="chat-call-ctrl">Mute</button>
      <button type="button" id="chatCallFitBtn" class="chat-call-ctrl" aria-pressed="true">Fit</button>
      <button type="button" id="chatCallCamBtn" class="chat-call-ctrl hidden">Camera</button>
      <button type="button" id="chatCallHangupBtn" class="chat-call-ctrl chat-call-ctrl--danger">End</button>
    </div>`;
  el.dataset.version = '9';
  document.body.appendChild(el);
  el.querySelector('#chatCallHangupBtn')?.addEventListener('click', () => { void hangUp(); });
  el.querySelector('#chatCallMinimizeBtn')?.addEventListener('click', () => {
    closeDeviceMenu();
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
  el.querySelector('#chatCallHearBtn')?.addEventListener('click', () => { void enableCallSound(); });
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
  if (el) return el;
  el = document.createElement('div');
  el.id = 'chatCallMinimized';
  el.className = 'chat-call-minimized hidden';
  el.setAttribute('role', 'button');
  el.setAttribute('tabindex', '0');
  el.setAttribute('aria-label', 'Return to call');
  el.innerHTML = `
    <span class="chat-call-minimized-pulse" aria-hidden="true"></span>
    <span id="chatCallMinimizedLabel" class="chat-call-minimized-label">On a call</span>
    <span class="chat-call-minimized-actions">
      <button type="button" id="chatCallMiniExpand" aria-label="Open call">Open</button>
      <button type="button" id="chatCallMiniEnd" aria-label="End call">End</button>
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
  if (el?.dataset.version === '3') return el;
  el?.remove();
  el = document.createElement('div');
  el.id = 'chatCallIncoming';
  el.dataset.version = '3';
  el.className = 'chat-call-incoming hidden';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  el.innerHTML = `
    <div class="chat-call-modal-card">
      <div class="chat-call-modal-icon" aria-hidden="true">📞</div>
      <p id="chatCallIncomingText" class="chat-call-incoming-text"></p>
      <div class="chat-call-modal-actions">
        <button type="button" data-call-reject class="chat-call-popup-btn chat-call-popup-btn--decline">Reject</button>
        <button type="button" data-call-accept class="chat-call-popup-btn chat-call-popup-btn--accept">Accept</button>
      </div>
    </div>`;
  document.body.appendChild(el);
  bindIncomingClick(el);
  return el;
}

function ensureChatPopup() {
  let el = document.getElementById('chatCallInChat');
  if (el?.dataset.version === '1') return el;
  el?.remove();
  el = document.createElement('div');
  el.id = 'chatCallInChat';
  el.dataset.version = '1';
  el.className = 'chat-call-inchat hidden';
  el.innerHTML = `
    <p id="chatCallInChatText"></p>
    <div>
      <button type="button" data-call-reject class="chat-call-popup-btn chat-call-popup-btn--decline">Reject</button>
      <button type="button" data-call-accept class="chat-call-popup-btn chat-call-popup-btn--accept">Accept</button>
    </div>`;
  const thread = document.querySelector('.chat-thread-panel');
  if (thread) thread.insertBefore(el, thread.firstChild);
  else document.body.appendChild(el);
  bindIncomingClick(el);
  return el;
}

function ensurePageBar() {
  let el = document.getElementById('chatCallPageBar');
  if (el?.dataset.version === '1') return el;
  el?.remove();
  el = document.createElement('div');
  el.id = 'chatCallPageBar';
  el.dataset.version = '1';
  el.className = 'chat-call-pagebar hidden';
  el.innerHTML = `
    <p id="chatCallPageBarText"></p>
    <div>
      <button type="button" data-call-reject class="chat-call-popup-btn chat-call-popup-btn--decline">Reject</button>
      <button type="button" data-call-accept class="chat-call-popup-btn chat-call-popup-btn--accept">Accept</button>
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

function preferLoudspeaker() {
  try {
    if (navigator.audioSession) navigator.audioSession.type = 'playback';
  } catch { /* unsupported */ }
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

function markAnswered() {
  if (phase !== 'outgoing' && !waitingForAnswer) return;
  phase = 'live';
  clearBusyTimer();
  stopRing();
  setStatus(activeMode === 'video' ? 'Video call' : 'Voice call');
  paintSpeaking();
}

function signalCall(type, convId, id) {
  if (!convId) return;
  ctx.sendWs?.({ type, conversation_id: convId, call_id: id || callId || '' });
}

function sameCall(data) {
  const conv = String(data?.conversation_id || '');
  if (!conv) return false;
  if (activeCallConvId && conv !== activeCallConvId && incoming?.conversation_id !== conv) return false;
  if (data.call_id && callId && data.call_id !== callId) return false;
  return phase !== 'idle' || Boolean(incoming);
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
  const remote = live ? [...live.remoteParticipants.values()][0] : null;
  const speaking = (participant) => Boolean(
    participant && !participantMuted(participant) && (participant.isSpeaking || (participant.audioLevel || 0) > 0.04),
  );
  document.getElementById('chatCallLocalTile')?.classList.toggle('is-speaking', speaking(local));
  document.getElementById('chatCallLocalTile')?.classList.toggle('is-muted', participantMuted(local));
  document.getElementById('chatCallRemoteTile')?.classList.toggle('is-speaking', speaking(remote));
  document.getElementById('chatCallRemoteTile')?.classList.toggle('is-muted', participantMuted(remote));
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
  if (isPhone()) return;
  const id = deviceId || preferredDeviceId('audiooutput');
  if (!id) return;
  document.querySelectorAll('audio.chat-call-remote-audio').forEach((el) => {
    if (typeof el.setSinkId === 'function') void el.setSinkId(id).catch(() => {});
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
  if (!id || !room) return;
  savePreferredDevice('audiooutput', id);
  await room.switchActiveDevice('audiooutput', id).catch(() => {});
  await applySpeakerToAttachedAudio(id);
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
    savePreferredDevice('audiooutput', spkSel.value);
    await live.switchActiveDevice('audiooutput', spkSel.value).catch(() => {});
    await applySpeakerToAttachedAudio(spkSel.value);
  }
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
  livekit = await import('./vendor/livekit-client.bundle.js?v=20261021t');
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
    btn.textContent = fitting ? 'Fit' : 'Fill';
    btn.setAttribute('aria-pressed', fitting ? 'true' : 'false');
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

function attachVideoTrack(track, participant) {
  const local = Boolean(participant?.isLocal);
  const tile = document.getElementById(local ? 'chatCallLocalTile' : 'chatCallRemoteTile');
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
  ['chatCallLocalTile', 'chatCallRemoteTile'].forEach((id) => {
    const tile = document.getElementById(id);
    tile?.querySelectorAll('video').forEach((node) => node.remove());
    tile?.classList.remove('has-video', 'is-speaking');
  });
}

async function joinSession(session, opts = {}) {
  const { url, token, mode } = session;
  const keepRing = Boolean(opts.keepRing);
  const { Room, RoomEvent, VideoPresets } = await loadLiveKit();
  activeMode = mode === 'video' ? 'video' : 'audio';
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
    const tile = document.getElementById(participant?.isLocal ? 'chatCallLocalTile' : 'chatCallRemoteTile');
    if (tile && !tile.querySelector('video')) tile.classList.remove('has-video');
  });
  room.on(RoomEvent.ParticipantConnected, (participant) => {
    clearRemoteDropTimer();
    if (!participant.isLocal) markAnswered();
    paintPeopleNames();
    bindRemoteVideo(room);
    paintSpeaking();
  });
  room.on(RoomEvent.ParticipantDisconnected, () => {
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
  await current.connect(url, token);
  if (phase === 'idle' || room !== current) {
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
  const camBtn = document.getElementById('chatCallCamBtn');
  if (activeMode === 'video') {
    camBtn?.classList.remove('hidden');
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
  } else {
    camBtn?.classList.add('hidden');
    setStatus('Voice call');
  }
}

async function startCall(mode, permission) {
  if (phase === 'outgoing' || phase === 'joining' || phase === 'live') return;
  const convId = ctx?.getActiveConvId?.();
  if (!convId || !ctx?.api) return;
  if (phase === 'incoming') declineIncoming();
  phase = 'outgoing';
  activeCallConvId = convId;
  activeMode = mode === 'video' ? 'video' : 'audio';
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
    const data = await ctx.api(`/api/chat/conversations/${convId}/call`, {
      method: 'POST',
      body: JSON.stringify({ mode: activeMode }),
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

async function endLocal(message, notify) {
  const convId = activeCallConvId || incoming?.conversation_id || '';
  const endingId = callId;
  const was = phase;
  phase = 'idle';
  callId = '';
  clearBusyTimer();
  clearRemoteDropTimer();
  stopRing();
  activeCallConvId = '';
  globalThis.__AIRD_PENDING_CALL__ = null;
  globalThis.__AIRD_HIDE_CALL_POPUP__?.();
  if (notify && convId && was !== 'idle') signalCall('call_end', convId, endingId);
  const current = room;
  room = null;
  if (current) {
    try { await current.disconnect(); } catch { /* ignore */ }
  }
  hideIncoming();
  stopMeters();
  clearVideos();
  clearRemoteAudio();
  if (message) {
    showOverlay(true);
    setStatus(message);
    setTimeout(() => { if (!room && phase === 'idle') showOverlay(false); }, 1600);
  } else {
    showOverlay(false);
  }
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
  if (btn) btn.textContent = enabled ? 'Unmute' : 'Mute';
}

async function toggleCamera() {
  const live = room;
  if (!live?.localParticipant) return;
  const enabled = live.localParticipant.isCameraEnabled;
  const cameraId = document.getElementById('chatCallCamSelect')?.value || preferredDeviceId('videoinput');
  await live.localParticipant.setCameraEnabled(!enabled, cameraId ? { deviceId: cameraId } : undefined);
  const btn = document.getElementById('chatCallCamBtn');
  if (btn) btn.textContent = enabled ? 'Camera on' : 'Camera off';
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
  const label = payload.mode === 'video' ? 'video' : 'voice';
  const message = `${payload.from} is calling · ${label}`;
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

function showIncoming(payload) {
  if (!payload || payload.type !== 'call_invite') return;
  if (!payload.call_id || !payload.conversation_id || !payload.from || payload.from === meName()) return;
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
  if (conv) signalCall('call_decline', conv, id);
  phase = 'idle';
  callId = '';
  activeCallConvId = '';
  globalThis.__AIRD_PENDING_CALL__ = null;
  globalThis.__AIRD_HIDE_CALL_POPUP__?.();
  hideIncoming();
}

function onWs(data) {
  if (!data?.type) return;
  if (data.type === 'call_invite') showIncoming(data);
  if (data.type === 'call_accept' && sameCall(data) && phase === 'outgoing') markAnswered();
  if (data.type === 'call_decline' && sameCall(data) && phase === 'outgoing') {
    void endLocal('Call declined', false);
  }
  if (data.type === 'call_end' && sameCall(data) && phase !== 'idle') {
    void endLocal('Call ended', false);
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
    if (!voice && !video) return;
    event.preventDefault();
    primeAudio();
    unlockCallAudio();
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
  globalThis.__AIRD_ACCEPT_CALL__ = (payload) => { onNotificationAction('accept', payload); };
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

const CALL_VOICE_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.8 19.8 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/></svg>';
const CALL_VIDEO_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m16 13 5.2 3.1a.8.8 0 0 0 1.2-.7V8.6a.8.8 0 0 0-1.2-.7L16 11"/><rect x="2" y="6" width="14" height="12" rx="2"/></svg>';

export function headerCallButtonsHtml() {
  return `<button type="button" id="chatVoiceCallBtn" class="chat-call-btn" title="Voice call" aria-label="Voice call">${CALL_VOICE_ICON}</button>`
    + `<button type="button" id="chatVideoCallBtn" class="chat-call-btn" title="Video call" aria-label="Video call">${CALL_VIDEO_ICON}</button>`;
}
