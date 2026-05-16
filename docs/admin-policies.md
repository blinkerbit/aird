# Admin — ABAC policies

**Route:** `GET /admin/policies` — `AdminPoliciesHandler`; `GET|POST /admin/api/abac/policies` and `…/policies/{id}`, `DELETE` — `AdminPolicyAPIHandler` (`abac_handlers.py`).  
**Template:** `admin_policies.html`.

**Behavior:** Lists policies from DB; modal edits JSON AST `condition` with operators documented in-template (`equals`, `in`, `tag_present`, `time_between`, `ip_in_cidr`, boolean combinators). Save/reload pattern.

**Do not change without migration plan:** Payload keys `effect`, `target_actions`, `priority`, `enabled`, `condition` must stay aligned with PEP/PDP evaluation code.
