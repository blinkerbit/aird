# P2P transfer

**Route:** `GET /p2p` — `P2PTransferHandler` (`p2p_handlers.py`).  
**Signaling:** `POST /p2p/signal` (see handler for payloads).

**Template:** `p2p_transfer.html`  
**Scripts:** `aird-core.js`, `p2p/*.js`, `vendor/qrcode-browser.js` (node-qrcode bundle, `npm run vendor:qrcode`), `pages/p2p-page.js`, `theme.js`.

**Behavior:** Optional auth (`is_anonymous` / `room_id` query); WebRTC room flow; STUN selection in UI. Server does not store file bytes—signaling only.

**Flags:** `p2p_transfer` feature; login page may link to anonymous P2P when enabled.

**Do not change without testing:** State machine + signaling message schema; `data-is-anonymous` / `data-pending-room-id` attributes read by page JS.
