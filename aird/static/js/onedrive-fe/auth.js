'use strict';

import { loadTokens, saveTokens } from './tokens.js';
import { getLocalAppConfig } from './settings.js';

const PKCE = 'aird.onedriveFe.pkce';
const DEVICE = 'aird.onedriveFe.device';
const SCOPES = 'Files.ReadWrite offline_access User.Read';

function pageCfg() {
  return globalThis.__ONEDRIVE_BROWSER_CONFIG || {};
}

function storeCfg(clientId, tenant) {
  globalThis.__ONEDRIVE_BROWSER_CONFIG = {
    enabled: true,
    clientId,
    tenant: tenant || 'common',
  };
  return globalThis.__ONEDRIVE_BROWSER_CONFIG;
}

function validClientId(id) {
  return /^[0-9a-fA-F-]{8,80}$/.test(String(id || '').trim());
}

export async function loadPublicConfig() {
  const page = pageCfg();
  if (validClientId(page.clientId)) return storeCfg(page.clientId, page.tenant);

  const local = getLocalAppConfig();
  if (local?.clientId) return storeCfg(local.clientId, local.tenant);

  try {
    const res = await fetch('/api/onedrive-browser/config', { credentials: 'same-origin' });
    const data = await res.json().catch(() => ({}));
    if (res.ok && validClientId(data.client_id)) {
      return storeCfg(data.client_id, data.tenant);
    }
  } catch { /* offline */ }

  return null;
}

function redirectUri() {
  return `${globalThis.location.origin}/onedrive-browser/callback`;
}

function b64url(bytes) {
  let bin = '';
  bytes.forEach((b) => { bin += String.fromCharCode(b); });
  return btoa(bin).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
}

function randomBytes(n) {
  const buf = new Uint8Array(n);
  crypto.getRandomValues(buf);
  return buf;
}

async function sha256b64url(text) {
  const data = new TextEncoder().encode(text);
  const hash = await crypto.subtle.digest('SHA-256', data);
  return b64url(new Uint8Array(hash));
}

function authorizeUrl(clientId, tenant, challenge, state) {
  const u = new URL(`https://login.microsoftonline.com/${tenant || 'common'}/oauth2/v2.0/authorize`);
  u.searchParams.set('client_id', clientId);
  u.searchParams.set('response_type', 'code');
  u.searchParams.set('redirect_uri', redirectUri());
  u.searchParams.set('response_mode', 'query');
  u.searchParams.set('scope', SCOPES);
  u.searchParams.set('code_challenge', challenge);
  u.searchParams.set('code_challenge_method', 'S256');
  u.searchParams.set('state', state);
  return u.toString();
}

async function exchangeCode(code, verifier, clientId, tenant) {
  const body = new URLSearchParams({
    client_id: clientId,
    grant_type: 'authorization_code',
    code,
    redirect_uri: redirectUri(),
    code_verifier: verifier,
    scope: SCOPES,
  });
  const res = await fetch(`https://login.microsoftonline.com/${tenant || 'common'}/oauth2/v2.0/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error_description || data.error || 'Token exchange failed');
  return {
    access_token: data.access_token,
    refresh_token: data.refresh_token || '',
    expires_at: Date.now() + Math.max(60, Number(data.expires_in) || 3600) * 1000,
  };
}

async function refreshTokens(tokens, clientId, tenant) {
  if (!tokens?.refresh_token) return null;
  const body = new URLSearchParams({
    client_id: clientId,
    grant_type: 'refresh_token',
    refresh_token: tokens.refresh_token,
    scope: SCOPES,
  });
  const res = await fetch(`https://login.microsoftonline.com/${tenant || 'common'}/oauth2/v2.0/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return null;
  const next = {
    access_token: data.access_token,
    refresh_token: data.refresh_token || tokens.refresh_token,
    expires_at: Date.now() + Math.max(60, Number(data.expires_in) || 3600) * 1000,
  };
  await saveTokens(next);
  return next;
}

export async function ensureAccessToken() {
  const publicCfg = await loadPublicConfig();
  if (!publicCfg?.clientId) return null;
  let tokens = await loadTokens();
  if (tokens?.access_token && tokens.expires_at > Date.now() + 30_000) return tokens.access_token;
  if (tokens) {
    const refreshed = await refreshTokens(tokens, publicCfg.clientId, publicCfg.tenant);
    if (refreshed?.access_token) return refreshed.access_token;
  }
  return null;
}

export async function startRedirectLogin(returnTo) {
  const publicCfg = await loadPublicConfig();
  if (!publicCfg?.clientId) throw new Error('Azure application ID is required.');
  const verifier = b64url(randomBytes(32));
  const challenge = await sha256b64url(verifier);
  const state = b64url(randomBytes(16));
  sessionStorage.setItem(PKCE, JSON.stringify({ verifier, state, returnTo: returnTo || globalThis.location.href }));
  globalThis.location.assign(authorizeUrl(publicCfg.clientId, publicCfg.tenant, challenge, state));
}

export async function finishCallback() {
  const params = new URLSearchParams(globalThis.location.search);
  const err = params.get('error_description') || params.get('error');
  if (err) throw new Error(err);
  const code = params.get('code');
  const state = params.get('state');
  const pkce = JSON.parse(sessionStorage.getItem(PKCE) || 'null');
  sessionStorage.removeItem(PKCE);
  if (!code || !pkce || pkce.state !== state) throw new Error('OneDrive sign-in was cancelled.');
  const publicCfg = await loadPublicConfig();
  if (!publicCfg?.clientId) throw new Error('Azure application ID is required.');
  const tokens = await exchangeCode(code, pkce.verifier, publicCfg.clientId, publicCfg.tenant);
  await saveTokens(tokens);
  return pkce.returnTo || '/files/';
}

export async function startDeviceCode() {
  const publicCfg = await loadPublicConfig();
  if (!publicCfg?.clientId) throw new Error('Azure application ID is required.');
  const body = new URLSearchParams({
    client_id: publicCfg.clientId,
    scope: SCOPES,
  });
  const res = await fetch(`https://login.microsoftonline.com/${publicCfg.tenant || 'common'}/oauth2/v2.0/devicecode`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error_description || 'Device code start failed');
  sessionStorage.setItem(DEVICE, JSON.stringify({
    device_code: data.device_code,
    interval: data.interval,
  }));
  return data;
}

export async function pollDeviceCode(deviceCode) {
  const publicCfg = await loadPublicConfig();
  if (!publicCfg?.clientId) throw new Error('Azure application ID is required.');
  let code = deviceCode;
  if (!code) {
    const pending = JSON.parse(sessionStorage.getItem(DEVICE) || 'null');
    code = pending?.device_code;
  }
  if (!code) throw new Error('No device login in progress');
  const body = new URLSearchParams({
    client_id: publicCfg.clientId,
    grant_type: 'urn:ietf:params:oauth:grant-type:device_code',
    device_code: code,
  });
  const res = await fetch(`https://login.microsoftonline.com/${publicCfg.tenant || 'common'}/oauth2/v2.0/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
  const data = await res.json().catch(() => ({}));
  if (data.error === 'authorization_pending') return { pending: true };
  if (data.error === 'slow_down') return { pending: true, slow: true };
  if (!res.ok) throw new Error(data.error_description || data.error || 'Device login failed');
  const tokens = {
    access_token: data.access_token,
    refresh_token: data.refresh_token || '',
    expires_at: Date.now() + Math.max(60, Number(data.expires_in) || 3600) * 1000,
  };
  await saveTokens(tokens);
  sessionStorage.removeItem(DEVICE);
  return { pending: false, tokens };
}

export async function ensureSignedIn({ returnTo } = {}) {
  const token = await ensureAccessToken();
  if (token) return token;
  const { showSignInDialog } = await import('./sign-in.js');
  return showSignInDialog({ returnTo: returnTo || globalThis.location.href });
}
