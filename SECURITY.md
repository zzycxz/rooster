# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.2.x   | :white_check_mark: |

## Reporting a Vulnerability

Rooster has desktop control, code execution, and browser automation capabilities. If you discover a security vulnerability, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, please report via:

- **GitHub Security Advisory**: [https://github.com/zzycxz/rooster/security/advisories/new](https://github.com/zzycxz/rooster/security/advisories/new)

### What to include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### Response timeline

- **Acknowledgment**: within 48 hours
- **Initial assessment**: within 7 days
- **Fix or mitigation**: within 30 days (depending on severity)

## Security Features

- API key authentication (`GATEWAY_API_KEY`)
- HMAC webhook verification (`WEBHOOK_HMAC_SECRET`)
- Path guard restricting file access to allowed directories
- Rate limiting on API endpoints
- Localhost auth can be disabled via `GATEWAY_LOCALHOST_AUTH=false`
