# Voice and video calls (LiveKit integration)

Calls are an **Aird plugin**, not media inside the Python process. Aird checks chat membership, mints a short-lived LiveKit JWT, and signals ring/accept/hangup on `/ws/chat`. Audio and video go **directly** between browsers and your LiveKit server.

## External services (required)

Run these **beside** `aird.service`, not inside it:

| Service | Role |
|---------|------|
| **LiveKit Server** | SFU — forwards Opus/simulcast without decoding in Aird |
| **TURN** (e.g. coturn) | Relay when mobile networks block UDP; configure in LiveKit |

### Environment (systemd / `.env`)

```bash
LIVEKIT_URL=wss://dx6si.aiccloud.online/livekit
LIVEKIT_API_KEY=aird-lk
LIVEKIT_API_SECRET=<same secret as deploy/livekit/secrets.env>
```

Also enable in Admin → **Voice & video calls**, assign the plugin under **Plugins**, and keep **Direct messages** on.

### Firewall (dx6si-PORT.aiccloud.online)

| Port | Proto | Public hostname | Role |
|------|-------|-----------------|------|
| **8899** | TCP | `dx6si-8899.aiccloud.online` | Signaling (WebSocket) — **you opened this** |
| **8900** | TCP | `dx6si-8900.aiccloud.online` | WebRTC ICE/TCP fallback — **please open** |
| **8901** | **UDP** | forward to this host `:8901` | WebRTC media (single UDP mux) — **please open** |

8899 alone is enough to connect to the room API; **audio/video needs 8900 + 8901**. UDP is not HTTP — expose raw UDP 8901 to the machine (no TLS hostname on UDP).

## Quick start (this server)

```bash
cd /home/vis/aird/deploy/livekit
cp secrets.env.example secrets.env   # if missing; edit LIVEKIT_KEYS
docker compose up -d
```

Config: `deploy/livekit/livekit.yaml` (ports 8899 / 8900 / 8901). Container uses `network_mode: host` so those ports bind on the VM directly.

Restart Aird after setting env vars:

```bash
sudo systemctl restart aird.service
```

## Frontend bundle

The chat UI loads `livekit-client` from a vendored bundle:

```bash
cd /home/vis/aird
npm install
npm run vendor:livekit
```

Commit `aird/static/js/vendor/livekit-client.bundle.js` after building, or run the script on deploy.

## API

- `POST /api/chat/conversations/{id}/call`  
  Body: `{ "mode": "audio" | "video", "invite": true }`  
  - `invite: true` (default) — ring other members (`call_invite` on WS + optional push).  
  - `invite: false` — join an existing room (token only).

## WebSocket signals

| Type | Direction |
|------|-----------|
| `call_invite` | Server → members (after start with invite) |
| `call_accept` | Client → hub → others |
| `call_decline` | Client → hub |
| `call_end` | Client → hub |

## Security notes

- Tokens expire in ~5 minutes.
- Room names are `aird-{conversationId}` (sanitized).
- CSP `connect-src` includes the LiveKit origin when calls are enabled.
- Chat page sets `Permissions-Policy: camera=(self), microphone=(self)` when calls are on.

End-to-end encryption of **message text** remains in chat E2E; **media E2E** (insertable streams) is not in the first slice.
