# Public / restricted share access

## Token verification  
**Route:** `GET /shared/{id}/verify` — renders `token_verification.html` when secret token required.

## File list  
**Route:** `GET /shared/{id}` — `SharedListHandler`; template `shared_list.html`.  
Embeds `#files-json`; client stores token in `sessionStorage` + short-lived cookie `share_token_{id}`; redirects to verify if missing.

## File fetch  
**Route:** `GET /shared/{id}/file/{path}` — `SharedFileHandler`; Bearer/cookie token must match for protected shares.

**Backend:** Share record from DB (`share_service`); expiry; user allowlists; dynamic/tag share expansion via `_get_share_file_list` helpers.

**Do not break:** Cookie name pattern `share_token_*`; JSON embedding safety (`</` escaped in handler); Authorization header convention in JS fetch helpers.
