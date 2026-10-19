/**
 * HTTP gateway — the single place that talks to the same-origin API.
 *
 * Pattern: Gateway + interceptor. Every request passes through `apiFetch`,
 * which injects the XSRF header; the JSON helpers on top add body
 * serialisation, safe parsing, and error normalisation (`HttpError`) so
 * call sites stop re-implementing `fetch` + headers + `.json().catch()`
 * boilerplate. Callers that need the raw `Response` (streaming, blob,
 * header inspection) use `apiFetch` / `postForm` directly.
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

/** Error thrown by the JSON helpers for non-2xx responses. */
export class HttpError extends Error {
  constructor(status, data, statusText = '') {
    super(data?.error || statusText || `HTTP ${status}`);
    this.name = 'HttpError';
    this.status = status;
    this.data = data ?? {};
  }
}

async function parseJsonSafe(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

/**
 * JSON request through the gateway.
 *
 * @param {string|URL} url
 * @param {object} [options]
 * @param {string} [options.method] HTTP method (default `GET`).
 * @param {*} [options.json] Value serialised as the JSON request body.
 * @param {boolean} [options.throwOnError] Throw `HttpError` on non-2xx
 *   (default `true`); pass `false` for endpoints whose error payload the
 *   caller inspects itself (e.g. `{ error: '…' }` responses).
 * @returns {Promise<object>} Parsed JSON body (`{}` when unparsable).
 */
export async function requestJson(url, options = {}) {
  const { json, throwOnError = true, headers: optHeaders, ...rest } = options;
  const headers = new Headers(optHeaders || {});
  headers.set('Accept', 'application/json');
  let body = rest.body;
  if (json !== undefined) {
    headers.set('Content-Type', 'application/json');
    body = JSON.stringify(json);
  }
  const response = await apiFetch(url, { ...rest, headers, body });
  const data = await parseJsonSafe(response);
  if (!response.ok && throwOnError) {
    throw new HttpError(response.status, data, response.statusText);
  }
  return data;
}

/** GET a JSON endpoint. */
export function getJson(url, options = {}) {
  return requestJson(url, { ...options, method: 'GET' });
}

/** POST a JSON body and return the parsed JSON response. */
export function postJson(url, json, options = {}) {
  return requestJson(url, { ...options, method: 'POST', json });
}

/**
 * POST `application/x-www-form-urlencoded` data; returns the raw
 * `Response` for legacy endpoints whose callers branch on `res.ok`
 * and read text bodies themselves.
 *
 * @param {string|URL} url
 * @param {URLSearchParams|string} params
 * @param {RequestInit} [options]
 * @returns {Promise<Response>}
 */
export function postForm(url, params, options = {}) {
  const { headers: optHeaders, ...rest } = options;
  const headers = new Headers(optHeaders || {});
  headers.set('Content-Type', 'application/x-www-form-urlencoded');
  return apiFetch(url, { ...rest, method: 'POST', headers, body: params.toString() });
}
