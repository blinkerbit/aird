/**
 * PWA install prompt + Web Push subscription for Aird.
 */
(function () {
  'use strict';

  const LS_NOTIFY = 'aird.chat.browserNotify';
  let deferredPrompt = null;
  let swReg = null;

  function qs(id) {
    return document.getElementById(id);
  }

  globalThis.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    updateInstallButton();
  });

  function isStandalone() {
    return (
      globalThis.matchMedia('(display-mode: standalone)').matches
      || globalThis.navigator.standalone === true
    );
  }

  function isIos() {
    const ua = navigator.userAgent || '';
    return /iPad|iPhone|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  }

  function xsrfToken() {
    const m = document.cookie.match(/(?:^|; )_xsrf=([^;]*)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  async function ensureServiceWorker() {
    if (!('serviceWorker' in navigator)) return null;
    if (swReg) return swReg;
    try {
      swReg = await navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' });
      // Also keep legacy URL registered clients migrating
      try {
        await navigator.serviceWorker.register('/sw-transfer.js', { scope: '/', updateViaCache: 'none' });
      } catch { /* ignore */ }
      return swReg;
    } catch (err) {
      console.debug('SW register failed', err);
      return null;
    }
  }

  function updateInstallButton() {
    const btn = qs('airdPwaInstallBtn');
    if (!btn) return;
    if (isStandalone()) {
      btn.classList.add('hidden');
      return;
    }
    if (deferredPrompt || isIos()) {
      btn.classList.remove('hidden');
    } else {
      // Chromium may fire beforeinstallprompt later; keep visible as soft hint
      btn.classList.remove('hidden');
    }
  }

  function showIosHelp() {
    let dlg = qs('airdPwaIosDialog');
    if (!dlg) {
      dlg = document.createElement('dialog');
      dlg.id = 'airdPwaIosDialog';
      dlg.className = 'modal';
      dlg.innerHTML = `
        <div class="modal-box max-w-sm">
          <h3 class="font-bold text-lg">Install Aird</h3>
          <p class="py-3 text-sm opacity-80">
            On iPhone/iPad: tap <strong>Share</strong>
            <svg class="inline w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v12"/><path d="M5 12l7-7 7 7"/><path d="M5 19h14"/></svg>
            then <strong>Add to Home Screen</strong>.
          </p>
          <p class="text-xs opacity-60 mb-2">After installing, open Aird from the home screen and enable notifications in Profile for alerts when the app is closed.</p>
          <form method="dialog" class="modal-action">
            <button class="btn btn-primary btn-sm">Got it</button>
          </form>
        </div>
        <form method="dialog" class="modal-backdrop"><button>close</button></form>`;
      document.body.appendChild(dlg);
    }
    dlg.showModal();
  }

  async function onInstallClick() {
    if (isIos()) {
      showIosHelp();
      return;
    }
    if (deferredPrompt) {
      deferredPrompt.prompt();
      try {
        await deferredPrompt.userChoice;
      } catch { /* ignore */ }
      deferredPrompt = null;
      updateInstallButton();
      return;
    }
    // Fallback instructions for browsers without beforeinstallprompt
    alert('To install Aird: use your browser menu → “Install app” or “Add to Home screen”.');
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base64);
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  async function fetchVapidPublicKey() {
    const res = await fetch('/api/pwa/vapid-public-key', { headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error('VAPID key unavailable');
    const data = await res.json();
    return data.publicKey;
  }

  async function postJson(url, body) {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        'X-XSRFToken': xsrfToken(),
      },
      body: JSON.stringify(body),
      credentials: 'same-origin',
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || res.statusText);
    }
    return res.json();
  }

  async function subscribePush() {
    const reg = await ensureServiceWorker();
    if (!reg || !('PushManager' in globalThis)) {
      throw new Error('Push messaging is not supported in this browser');
    }
    if (Notification.permission !== 'granted') {
      const perm = await Notification.requestPermission();
      if (perm !== 'granted') throw new Error('Notification permission denied');
    }
    const publicKey = await fetchVapidPublicKey();
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
      });
    }
    await postJson('/api/pwa/push-subscribe', sub.toJSON());
    localStorage.setItem(LS_NOTIFY, '1');
    return sub;
  }

  async function unsubscribePush() {
    const reg = await ensureServiceWorker();
    if (!reg) return;
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      try {
        await postJson('/api/pwa/push-unsubscribe', { endpoint: sub.endpoint });
      } catch { /* ignore */ }
      await sub.unsubscribe();
    }
    localStorage.setItem(LS_NOTIFY, '0');
  }

  /** Prefer SW notification when available (works better in PWA / background). */
  async function showViaServiceWorker(payload) {
    const reg = swReg || (await ensureServiceWorker());
    if (!reg || !reg.showNotification) return false;
    if (localStorage.getItem(LS_NOTIFY) === '0') return false;
    if (Notification.permission !== 'granted') return false;
    const convId = payload.conversation_id;
    await reg.showNotification(payload.sender || 'Aird', {
      body: payload.preview || 'New message',
      icon: '/static/img/pwa-icon-192.png',
      badge: '/static/img/pwa-icon-192.png',
      tag: `aird-chat-${convId}`,
      renotify: true,
      data: {
        url: `/chat?c=${encodeURIComponent(convId || '')}`,
        conversation_id: convId,
      },
    });
    return true;
  }

  function boot() {
    const btn = qs('airdPwaInstallBtn');
    if (btn) {
      btn.addEventListener('click', () => { onInstallClick().catch(() => {}); });
    }

    globalThis.addEventListener('appinstalled', () => {
      deferredPrompt = null;
      updateInstallButton();
      // Auto-prompt push after install when user already opted into browser notify
      if (localStorage.getItem(LS_NOTIFY) !== '0') {
        subscribePush().catch((err) => console.debug('post-install push subscribe', err));
      }
    });

    navigator.serviceWorker?.addEventListener('message', (ev) => {
      const data = ev.data || {};
      if (data.type === 'aird-navigate' && data.url) {
        location.href = data.url;
      }
    });

    updateInstallButton();
    ensureServiceWorker().then((reg) => {
      if (!reg) return;
      // Re-sync subscription if permission already granted
      if (
        localStorage.getItem(LS_NOTIFY) !== '0'
        && Notification.permission === 'granted'
        && document.documentElement.dataset.airdUser === '1'
      ) {
        subscribePush().catch((err) => console.debug('push resync', err));
      }
    });
  }

  globalThis.AirdPwa = {
    ensureServiceWorker,
    subscribePush,
    unsubscribePush,
    showViaServiceWorker,
    isStandalone,
    isIos,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
