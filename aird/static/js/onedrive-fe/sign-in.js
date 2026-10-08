'use strict';

import { getAuthMode, setAuthMode, getLocalAppConfig, setLocalAppConfig } from './settings.js';
import {
  loadPublicConfig,
  startRedirectLogin,
  startDeviceCode,
  pollDeviceCode,
  ensureAccessToken,
} from './auth.js';

let overlay = null;
let pollTimer = null;

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function closeOverlay() {
  stopPoll();
  overlay?.remove();
  overlay = null;
}

function say(el, text, isErr) {
  if (!el) return;
  el.textContent = text || '';
  el.classList.toggle('text-error', !!isErr);
  el.hidden = !text;
}

function needsAppSetup() {
  const page = globalThis.__ONEDRIVE_BROWSER_CONFIG || {};
  if (page.clientId) return false;
  if (getLocalAppConfig()?.clientId) return false;
  return true;
}

function appSetupHtml() {
  const local = getLocalAppConfig();
  const redirect = `${globalThis.location.origin}/onedrive-browser/callback`;
  return `
    <div class="od-fe-signin-app">
      <p class="text-sm opacity-80">Enter your Azure application (client) ID. Create a public app in Azure Portal with redirect URI <code class="text-xs">${redirect}</code> and scopes <code class="text-xs">Files.ReadWrite offline_access User.Read</code>.</p>
      <label class="form-control mt-3">
        <span class="label-text text-xs font-semibold">Application (client) ID</span>
        <input type="text" class="input input-bordered input-sm w-full font-mono od-fe-app-client" placeholder="00000000-0000-0000-0000-000000000000" value="${local?.clientId || ''}" autocomplete="off">
      </label>
      <label class="form-control mt-2">
        <span class="label-text text-xs font-semibold">Tenant</span>
        <input type="text" class="input input-bordered input-sm w-full max-w-xs od-fe-app-tenant" placeholder="common" value="${local?.tenant || 'common'}" autocomplete="off">
      </label>
    </div>`;
}

function authModesHtml(savedMode) {
  return `
    <div class="od-fe-signin-modes">
      <label class="od-fe-signin-mode">
        <input type="radio" name="odFeAuthMode" value="redirect" ${savedMode !== 'device' ? 'checked' : ''}>
        <span><strong>Browser sign-in</strong><small>Opens Microsoft login in this browser (recommended)</small></span>
      </label>
      <label class="od-fe-signin-mode">
        <input type="radio" name="odFeAuthMode" value="device" ${savedMode === 'device' ? 'checked' : ''}>
        <span><strong>Device code</strong><small>Sign in on another device using a one-time code</small></span>
      </label>
    </div>
    <div class="od-fe-signin-device hidden">
      <p class="text-sm">Go to <a class="od-fe-device-url link" href="#" target="_blank" rel="noopener"></a></p>
      <p class="text-sm">Enter code: <strong class="od-fe-device-code font-mono"></strong></p>
      <p class="text-xs opacity-70">Waiting for you to complete sign-in…</p>
    </div>`;
}

export async function showSignInDialog({ returnTo } = {}) {
  const token = await ensureAccessToken();
  if (token) return token;

  await loadPublicConfig();
  const showAppSetup = needsAppSetup();
  const savedMode = getAuthMode();

  return new Promise((resolve) => {
    overlay = document.createElement('div');
    overlay.className = 'od-fe-signin-overlay';
    overlay.innerHTML = `
      <div class="od-fe-signin-panel" role="dialog" aria-labelledby="odFeSignInTitle">
        <h3 id="odFeSignInTitle">${showAppSetup ? 'Set up OneDrive sign-in' : 'Sign in to OneDrive'}</h3>
        <p class="text-sm opacity-80">${showAppSetup
    ? 'Enter your Azure app details below, then choose how to sign in.'
    : 'Choose how to authenticate with Microsoft. Tokens stay in this browser only.'}</p>
        ${showAppSetup ? appSetupHtml() : ''}
        ${authModesHtml(savedMode)}
        <p class="od-fe-signin-msg text-sm hidden"></p>
        <div class="od-fe-signin-actions">
          <button type="button" class="btn btn-ghost btn-sm od-fe-signin-cancel">Cancel</button>
          <button type="button" class="btn btn-primary btn-sm od-fe-signin-continue">Continue</button>
        </div>
      </div>`;

    document.body.appendChild(overlay);
    const msgEl = overlay.querySelector('.od-fe-signin-msg');
    const deviceBlock = overlay.querySelector('.od-fe-signin-device');
    const continueBtn = overlay.querySelector('.od-fe-signin-continue');
    const cancelBtn = overlay.querySelector('.od-fe-signin-cancel');

    function selectedMode() {
      return overlay.querySelector('input[name="odFeAuthMode"]:checked')?.value || 'redirect';
    }

    cancelBtn?.addEventListener('click', () => {
      closeOverlay();
      resolve(null);
    });

    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) {
        closeOverlay();
        resolve(null);
      }
    });

    continueBtn?.addEventListener('click', async () => {
      say(msgEl, '');
      stopPoll();
      deviceBlock?.classList.add('hidden');

      const clientInput = overlay.querySelector('.od-fe-app-client');
      if (clientInput || !(await loadPublicConfig())?.clientId) {
        if (clientInput) {
          try {
            setLocalAppConfig({
              clientId: clientInput.value,
              tenant: overlay.querySelector('.od-fe-app-tenant')?.value || 'common',
            });
          } catch (err) {
            say(msgEl, err.message || 'Invalid application ID', true);
            return;
          }
        } else if (!(await loadPublicConfig())?.clientId) {
          say(msgEl, 'Azure application ID is required.', true);
          return;
        }
      }

      const mode = selectedMode();
      setAuthMode(mode);

      if (mode === 'redirect') {
        say(msgEl, 'Redirecting to Microsoft…');
        continueBtn.disabled = true;
        try {
          await startRedirectLogin(returnTo || globalThis.location.href);
        } catch (err) {
          say(msgEl, err.message || 'Sign-in failed', true);
          continueBtn.disabled = false;
        }
        resolve(null);
        return;
      }

      continueBtn.disabled = true;
      cancelBtn.disabled = true;
      try {
        say(msgEl, 'Starting device login…');
        const flow = await startDeviceCode();
        const urlEl = overlay.querySelector('.od-fe-device-url');
        const codeEl = overlay.querySelector('.od-fe-device-code');
        if (urlEl) {
          urlEl.href = flow.verification_uri || '#';
          urlEl.textContent = flow.verification_uri || '';
        }
        if (codeEl) codeEl.textContent = flow.user_code || '';
        deviceBlock?.classList.remove('hidden');
        say(msgEl, '');

        const intervalMs = Math.max(3000, (Number(flow.interval) || 5) * 1000);
        stopPoll();
        pollTimer = setInterval(async () => {
          try {
            const result = await pollDeviceCode();
            if (result?.pending) return;
            stopPoll();
            const nextToken = await ensureAccessToken();
            closeOverlay();
            resolve(nextToken);
          } catch (err) {
            stopPoll();
            say(msgEl, err.message || 'Device login failed', true);
            continueBtn.disabled = false;
            cancelBtn.disabled = false;
          }
        }, intervalMs);
      } catch (err) {
        say(msgEl, err.message || 'Could not start device login', true);
        continueBtn.disabled = false;
        cancelBtn.disabled = false;
      }
    });
  });
}
