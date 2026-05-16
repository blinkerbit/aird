# Admin — user attributes (ABAC)

**Route:** `GET /admin/user-attributes` — `AdminUserAttributesHandler`; API `POST|DELETE /admin/api/abac/user-attributes` — `AdminUserAttributeAPIHandler`.

**Template:** `admin_user_attributes.html`.

**Behavior:** Key/value pairs per username (clearance, `managed_device`, `groups`, etc.—see inline “Common attributes” table). Used as subject attributes in policy evaluation (`base_handler`/ABAC helpers).

**Do not break:** JSON keys `username`, `key`, `value` on POST; DELETE body shape.
