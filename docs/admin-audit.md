# Admin — audit log

**Route:** `GET /admin/audit` — `AdminAuditHandler` → `admin_audit.html`.  
**Export:** `?format=csv` returns CSV (not HTML).

**HTML behavior:** Two tabs — user audit table (server-rendered `logs`) and “Policy decisions” panel: `GET /admin/api/abac/decisions` + live `WS /ws/policy-decisions` (message type `policy_decision`).

**Auth:** Admin session required.

**Do not break:** JSON row shape expected by `renderRow()` in inline script; WebSocket URL build from `window.location`.
