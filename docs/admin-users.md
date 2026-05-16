# Admin — users

**Routes:**
- `GET /admin/users` — `AdminUsersHandler` → `admin_users.html`
- `GET|POST /admin/users/create` — `UserCreateHandler` → `user_create.html`
- `GET|POST /admin/users/edit/{id}` — `UserEditHandler` → `user_edit.html`
- `POST /admin/users/delete` — `UserDeleteHandler`

**Behavior:** Lists SQLite users via `user_service`; create/edit validates role, password rules, uniqueness. Delete uses dynamically built form + XSRF from hidden scaffold.

**API used elsewhere:** `/api/users/search` (share UI) relies on user list semantics—not duplicate user logic here.

**Do not break:** `_xsrf` hidden input expected by inline delete JS in `admin_users.html`.
