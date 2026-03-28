/**
 * XSRF token utilities for Tornado-based apps.
 */
export function getXSRFToken() {
  const match = document.cookie.match(/_xsrf=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : '';
}

export function xsrfHeaders() {
  return { 'X-XSRFToken': getXSRFToken() };
}
