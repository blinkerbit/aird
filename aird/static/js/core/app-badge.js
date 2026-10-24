/** Home-screen badge for the installed PWA. */

export async function setAppIconBadge(count) {
  const n = Math.max(0, Math.floor(Number(count) || 0));
  try {
    if (n > 0 && typeof navigator.setAppBadge === 'function') await navigator.setAppBadge(n);
    else if (typeof navigator.clearAppBadge === 'function') await navigator.clearAppBadge();
  } catch {
    /* This launcher may only show a dot, or none. */
  }
  try {
    const reg = await navigator.serviceWorker?.ready;
    reg?.active?.postMessage({ type: 'aird-badge', count: n });
  } catch {
    /* No controlling worker yet. */
  }
}
