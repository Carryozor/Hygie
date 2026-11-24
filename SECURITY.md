# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 4.x     | ✅ (latest minor only) |
| < 4.0   | ❌ |

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

## Authentication log (format v1)

Every authentication event is written to stdout (container logs) by the dedicated
logger `hygie_auth` — it is **not** affected by the `log_level` setting — and to the
Hygie journal (source `auth`). One line per event:

```
<ISO8601 UTC> HYGIE_AUTH v=1 event=<e> ip=<ip> method=<m> reason=<r> user=<u>
```

- `event`: `login_success`, `login_failure`, `mfa_failure`, `locked`, `refresh_rejected`, `mfa_enabled`, `mfa_disabled`, `mfa_reset`
- `method`: `password`, `totp`, `backup_code`, `refresh`, `mfa`, `cli`
- `reason`: `ok`, `invalid_credentials`, `invalid_code`, `replay`, `challenge_invalid`, `rate_limit`, `daily_cap`, `revoked`, `reused`, `version`
- `ip` is the client IP as resolved by Hygie (see `HYGIE_TRUSTED_PROXIES` below).
- `user` is stripped of control/format characters, truncated to 64 characters and percent-encoded,
  so a username can never inject a space or `=` (and thus a forged field) into the line.

A successful login (password, TOTP or backup code) also sends a Discord alert, unless
`discord_alert_login_success` is set to `false`. Without a dedicated alerts webhook the
main webhook is used.

### fail2ban

`/etc/fail2ban/filter.d/hygie.conf`:

```ini
[Definition]
failregex = HYGIE_AUTH v=1 event=(?:login_failure|mfa_failure|locked) ip=<ADDR> 
ignoreregex =
```

Jail reading the Docker journald driver (`journalmatch` selects the container):

```ini
[hygie]
enabled  = true
filter   = hygie
backend  = systemd
journalmatch = CONTAINER_NAME=hygie
maxretry = 5
findtime = 10m
bantime  = 1h
```

Jail reading the default `json-file` driver:

```ini
[hygie-json]
enabled  = true
filter   = hygie
backend  = auto
logpath  = /var/lib/docker/containers/*/*-json.log
maxretry = 5
findtime = 10m
bantime  = 1h
```

Note that a ban on the Docker host only protects Hygie if the banned IP is the one Hygie
sees: behind a reverse proxy, configure `HYGIE_TRUSTED_PROXIES` first, otherwise you would
ban the proxy itself.

### CrowdSec

The same lines can be parsed by CrowdSec: acquire the container logs with the `docker`
datasource and write a parser matching `HYGIE_AUTH v=1 event=(login_failure|mfa_failure|locked) ip=(\S+) `.

## Reverse proxies and client IP

Rate limiting, the auth log and bans all rely on the client IP. Set
`HYGIE_TRUSTED_PROXIES` to the CIDR(s) of your reverse proxy (for example
`172.27.0.0/16`); `X-Forwarded-For` is honoured only for requests coming from those
addresses. Do not use a wildcard: any client could then forge its IP. This replaces the
former `HYGIE_TRUST_PROXY=1`.

## Prometheus `/metrics`

`/metrics` answers 404 until a bearer token is configured (setting `prometheus_bearer_token`).
Configure the same token as `authorization: credentials` in your Prometheus scrape job.

## Lost device (two-factor authentication)

If the authenticator device is lost:

1. Log in with one of the 10 single-use backup codes, then disable and re-enable 2FA.
2. If no backup code is left, reset 2FA from the host with the CLI,
   e.g. `docker exec -it hygie python -m backend.tools.reset_mfa --username <username>` — it
   emits an `event=mfa_reset method=cli` line.
3. Change your password afterwards: an attacker able to read the log of a lost device may know it.
