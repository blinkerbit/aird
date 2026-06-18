/**
 * Optional WebAuthn passkeys — hidden when unsupported or feature disabled.
 */
(function (global) {
  'use strict';

  let _enabled = null;
  let _rpId = null;

  function getXSRFToken() {
    if (global.AirdCore?.getXSRFToken) {
      return global.AirdCore.getXSRFToken();
    }
    const m = /(?:^|; )_xsrf=([^;]*)/.exec(document.cookie);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function isBrowserSupported() {
    return typeof global.PublicKeyCredential !== 'undefined'
      && typeof global.navigator?.credentials?.create === 'function';
  }

  function b64ToBuf(b64) {
    const pad = (4 - (b64.length % 4)) % 4;
    const s = (b64 + '='.repeat(pad)).replace(/-/g, '+').replace(/_/g, '/');
    const bin = atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out.buffer;
  }

  function bufToB64(buf) {
    const bytes = new Uint8Array(buf);
    let bin = '';
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
  }

  function decodeCreationOptions(json) {
    const o = { ...json };
    o.challenge = b64ToBuf(o.challenge);
    if (o.user?.id) o.user = { ...o.user, id: b64ToBuf(o.user.id) };
    if (Array.isArray(o.excludeCredentials)) {
      o.excludeCredentials = o.excludeCredentials.map((c) => ({
        ...c,
        id: b64ToBuf(c.id),
      }));
    }
    return o;
  }

  function decodeRequestOptions(json) {
    const o = { ...json };
    o.challenge = b64ToBuf(o.challenge);
    if (Array.isArray(o.allowCredentials)) {
      o.allowCredentials = o.allowCredentials.map((c) => ({
        ...c,
        id: b64ToBuf(c.id),
      }));
    }
    return o;
  }

  function credentialToJSON(cred) {
    const r = cred.response;
    const out = {
      id: cred.id,
      rawId: bufToB64(cred.rawId),
      type: cred.type,
      response: {
        clientDataJSON: bufToB64(r.clientDataJSON),
        attestationObject: bufToB64(r.attestationObject),
      },
    };
    if (cred.authenticatorAttachment) {
      out.authenticatorAttachment = cred.authenticatorAttachment;
    }
    if (r.getTransports) {
      out.response.transports = r.getTransports();
    }
    return out;
  }

  function authCredentialToJSON(cred) {
    const r = cred.response;
    return {
      id: cred.id,
      rawId: bufToB64(cred.rawId),
      type: cred.type,
      response: {
        clientDataJSON: bufToB64(r.clientDataJSON),
        authenticatorData: bufToB64(r.authenticatorData),
        signature: bufToB64(r.signature),
        userHandle: r.userHandle ? bufToB64(r.userHandle) : null,
      },
    };
  }

  async function fetchStatus() {
    if (!isBrowserSupported()) {
      _enabled = false;
      return false;
    }
    try {
      const res = await fetch('/api/webauthn/status', { credentials: 'same-origin' });
      if (!res.ok) {
        _enabled = false;
        return false;
      }
      const data = await res.json();
      _enabled = !!data.enabled;
      _rpId = data.rpId || null;
      return _enabled;
    } catch {
      _enabled = false;
      return false;
    }
  }

  async function isEnabled() {
    if (_enabled !== null) return _enabled && isBrowserSupported();
    return fetchStatus();
  }

  async function registerPasskey(nickname) {
    const xsrf = getXSRFToken();
    const optRes = await fetch('/api/webauthn/register/options', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-XSRFToken': xsrf,
      },
      body: '{}',
    });
    if (!optRes.ok) {
      const err = await optRes.json().catch(() => ({}));
      throw new Error(err.error || 'Could not start passkey registration.');
    }
    const options = await optRes.json();
    const prfSalt = options.prfSalt;
    delete options.prfSalt;

    const pubKey = decodeCreationOptions(options);
    const createOpts = { publicKey: pubKey };
    if (prfSalt && global.PublicKeyCredential?.getClientCapabilities) {
      try {
        const caps = await global.PublicKeyCredential.getClientCapabilities();
        if (caps?.publicKeyCredential?.prf) {
          createOpts.publicKey.extensions = {
            prf: { eval: { first: b64ToBuf(prfSalt) } },
          };
        }
      } catch {
        /* PRF optional */
      }
    } else if (prfSalt) {
      createOpts.publicKey.extensions = {
        prf: { eval: { first: b64ToBuf(prfSalt) } },
      };
    }

    const cred = await navigator.credentials.create(createOpts);
    if (!cred) throw new Error('Passkey registration was cancelled.');

    let prfCapable = false;
    try {
      const ext = cred.getClientExtensionResults?.();
      prfCapable = !!(ext?.prf?.enabled || ext?.prf?.results?.first);
    } catch {
      prfCapable = false;
    }

    const verifyRes = await fetch('/api/webauthn/register/verify', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-XSRFToken': xsrf,
      },
      body: JSON.stringify({
        credential: credentialToJSON(cred),
        prfCapable,
        nickname: nickname || null,
        transports: cred.response.getTransports?.() || [],
      }),
    });
    if (!verifyRes.ok) {
      const err = await verifyRes.json().catch(() => ({}));
      throw new Error(err.error || 'Passkey registration failed.');
    }
    return verifyRes.json();
  }

  async function loginWithPasskey(username, nextUrl) {
    const optRes = await fetch('/api/webauthn/auth/options', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: username || '' }),
    });
    if (!optRes.ok) {
      const err = await optRes.json().catch(() => ({}));
      throw new Error(err.error || 'No passkey available for this user.');
    }
    const options = await optRes.json();
    const cred = await navigator.credentials.get({
      publicKey: decodeRequestOptions(options),
    });
    if (!cred) throw new Error('Passkey sign-in was cancelled.');

    const verifyRes = await fetch('/api/webauthn/auth/verify', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        credential: authCredentialToJSON(cred),
        next: nextUrl || '/files/',
      }),
    });
    if (!verifyRes.ok) {
      const err = await verifyRes.json().catch(() => ({}));
      throw new Error(err.error || 'Passkey sign-in failed.');
    }
    return verifyRes.json();
  }

  async function deletePasskey(id) {
    const xsrf = getXSRFToken();
    const res = await fetch(`/api/webauthn/credentials/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      credentials: 'same-origin',
      headers: { 'X-XSRFToken': xsrf },
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Could not remove passkey.');
    }
  }

  function showIfEnabled(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    isEnabled().then((on) => {
      if (on) el.classList.remove('hidden');
    });
  }

  global.AirdWebAuthn = {
    isBrowserSupported,
    isEnabled,
    registerPasskey,
    loginWithPasskey,
    deletePasskey,
    showIfEnabled,
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
