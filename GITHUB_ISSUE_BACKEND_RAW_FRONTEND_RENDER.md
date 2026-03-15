# Backend: Always Send Raw Byte Data; Frontend: Render and Display

## Summary

Refactor the application architecture so that the **backend always returns raw byte data** (JSON, plain text, binary) without any HTML rendering or interpretation, and the **frontend is solely responsible for rendering and displaying** that data safely.

## Motivation

1. **Security (XSS prevention)**: Currently, several templates and handlers mix server-rendered HTML with user-controlled or API data. Using `innerHTML` with unsanitized content, or rendering unescaped variables in templates, creates XSS vulnerabilities. By having the backend send only structured/raw data and the frontend render it with proper escaping (e.g., `textContent`, sanitized HTML), we reduce the attack surface.

2. **Separation of concerns**: The backend should focus on data and business logic; the frontend should focus on presentation. This makes the codebase easier to reason about, test, and maintain.

3. **API-first design**: APIs and WebSocket endpoints should return pure data (JSON, binary streams). HTML templates that currently embed data directly can be replaced or augmented with client-side rendering that consumes these APIs.

## Proposed Architecture

### Backend

- **APIs / WebSockets**: Return only raw data:
  - JSON for structured data (shares, file lists, audit logs, errors)
  - Binary/octet-stream for file downloads
  - Plain text for streaming file content (e.g., log tailing)
- **Templates**: Where server-rendered HTML is still used (e.g., initial page load), all dynamic values must be properly escaped. No mixing of raw user/API data into HTML without escaping.
- **Error responses**: Return `{"error": "message"}` or similar JSON; avoid embedding user-controlled strings in HTML.

### Frontend

- **Rendering**: Use safe methods:
  - `textContent` for user/API data when no HTML is needed
  - `escapeHtml()` or equivalent before any `innerHTML` when HTML structure is required
  - Sanitize Markdown output (e.g., `marked.parse()`) before rendering
- **Data binding**: Prefer DOM APIs (`createElement`, `appendChild`, `textContent`) over string concatenation + `innerHTML` when building dynamic content.
- **WebSocket / fetch**: Parse JSON, then render with proper escaping. Never insert raw API responses into the DOM without sanitization.

## Scope

### Areas to update

1. **Templates with unescaped output** (see template bug report):
   - `admin_network_shares.html`: `{{ error }}` → `{{ escape(error) }}`
   - `admin_audit.html`: Audit log fields
   - `shared_list.html`: `file_path`, `share_id`
   - `file.html`, `edit.html`: `filename`, `path`, `full_file_content`
   - Others as identified

2. **JavaScript using `innerHTML` with unsanitized data**:
   - `browse.html`: Share details popup, share picker options
   - `file.html`: WebSocket stream line content, error messages
   - `shared_list.html`: File list rendering
   - `share.html`: Various dynamic content
   - `super_search.html`: Search results, help text

3. **API responses**: Ensure all JSON APIs return structured data; handlers that currently render HTML for errors should return JSON instead where consumed by JS.

4. **WebSocket streams**: File streaming and feature-flag WebSockets send text/JSON. Ensure any text displayed in the UI is escaped before insertion.

## Acceptance Criteria

- [ ] Backend never embeds user-controlled or API-sourced data into HTML without escaping
- [ ] All API/WebSocket responses are raw data (JSON, binary, plain text)
- [ ] Frontend uses `textContent` or `escapeHtml()` for any dynamic content inserted into the DOM
- [ ] Markdown preview (e.g., in edit view) sanitizes output before rendering
- [ ] No XSS vectors from template variables or `innerHTML` with unsanitized data

## Related

- Template bug report (XSS, unescaped output, `innerHTML` usage)
- `duplicate_strings.txt` for centralizing error messages and constants
