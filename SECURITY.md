# Security policy

## Reporting a vulnerability

Do not open a public GitHub issue for a security problem.

Use [Report a vulnerability](https://github.com/blinkerbit/aird/security/advisories/new) so the report stays private. Include the version (`python -m aird --version` or the git revision), how to reproduce the issue, and the impact you expect.

## Supported versions

Security fixes ship on the latest release published from `main`. Older tags are not patched separately.

## Production defaults

Set `AIRD_COOKIE_SECRET`, terminate TLS at a reverse proxy, and do not expose the admin token. Multi-user mode requires an explicit access token at startup.
