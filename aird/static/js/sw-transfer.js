/**
 * Service worker: notify clients to retry failed transfers when connectivity returns.
 */
'use strict';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('sync', (event) => {
  if (event.tag === 'aird-transfer-retry') {
    event.waitUntil(notifyClientsRetry());
  }
});

async function notifyClientsRetry() {
  const clients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  for (const client of clients) {
    client.postMessage({ type: 'aird-transfer-retry' });
  }
}
