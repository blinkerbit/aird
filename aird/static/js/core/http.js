/**
 * Same-origin fetch wrapper that injects the XSRF header.
 */
import { getXSRFToken } from './xsrf.js';

/**
 * `fetch` with `X-XSRFToken` added unless the caller already set one.
 * All other options pass through untouched.
 *
 * @param {string|URL} url
 * @param {RequestInit} [options]
 * @returns {Promise<Response>}
 */
export function apiFetch(url, options = {}) {
  const { headers: optHeaders, ...rest } = options;
  const headers = new Headers(optHeaders || {});
  if (!headers.has('X-XSRFToken')) {
    headers.set('X-XSRFToken', getXSRFToken());
  }
  return fetch(url, { ...rest, headers });
}
