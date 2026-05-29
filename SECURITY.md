# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 2.x.x   | ✅ |
| 1.x.x   | ❌ |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report security issues by email to **gegeve05@gmail.com** with the subject line `[SECURITY] Hygie — <short description>`.

Please include:
- Hygie version affected
- Steps to reproduce the issue
- Potential impact (data exposure, privilege escalation, etc.)
- Any suggested fix if you have one

**Response timeline:**
- Acknowledgement within 48 hours
- Status update within 7 days
- Fix for critical issues within 14 days

## Scope

The following are **in scope**:
- Authentication bypass
- SSRF via the image proxy
- Stored XSS in any rendered field
- SQLite injection in any user-controlled input
- Exposure of API keys or the encryption key
- Privilege escalation via the API

The following are **out of scope**:
- Issues requiring physical access to the host
- Self-hosted infrastructure misconfigurations (e.g., exposing port 8000 publicly without a reverse proxy)
- Rate-limiting gaps on trusted local networks

## Security hardening checklist

When self-hosting Hygie in production:
- Set `HYGIE_ENCRYPTION_KEY` — without it, API keys are stored in plaintext
- Place Hygie behind a reverse proxy (Nginx, Caddy, Traefik) with HTTPS
- Do not expose port 8000 directly to the internet
- Use `mem_limit: 1g` and `cpus: '2.0'` in your compose file for production workloads
- Back up `./data/hygie.db` and `./data/.secret` regularly
