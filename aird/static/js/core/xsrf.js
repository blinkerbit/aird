/**
 * XSRF helpers — canonical implementation.
 *
 * Classic (non-module) pages keep using `globalThis.AirdCore.getXSRFToken()`;
 * the classic facade (`aird/static/js/aird-core.js`) is generated from this
 * module via `npm run js:core`, so both stay in sync.
 */

/** Read the `_xsrf` cookie value, or '' when absent. */
export function getXSRFToken() {
  const m = /(?:^|; )_xsrf=([^;]*)/.exec(document.cookie);
  return m ? decodeURIComponent(m[1]) : '';
}

/** Merge an `X-XSRFToken` header into a plain headers object. */
export function xsrfHeaders(extra) {
  return { ...(extra || {}), 'X-XSRFToken': getXSRFToken() };
}
