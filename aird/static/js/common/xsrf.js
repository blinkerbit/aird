/**
 * XSRF token utilities for Tornado-based apps.
 */
export function getXSRFToken() {
  const match = /_xsrf=([^;]*)/.exec(document.cookie);
  return match ? decodeURIComponent(match[1]) : '';
}

export function xsrfHeaders() {
  return { 'X-XSRFToken': getXSRFToken() };
}
