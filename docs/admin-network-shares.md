# Admin — network shares (SMB / WebDAV)

**Routes:** `GET|POST /admin/network-shares`, `POST …/toggle`, `POST …/delete` — `AdminNetworkSharesHandler` and related (`admin_handlers.py`).  
**Template:** `admin_network_shares.html`.

**Behavior:** CRUD for embedded share definitions; mount-hint panels built client-side from `data-*` on rows; copy-to-clipboard for generated commands.

**Do not change lightly:** `mount_idx` / panel DOM coupling; `server_host_js` embedded value for command text.
