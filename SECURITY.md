# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Liquid, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, email us at: **hello@ertad.com**

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

We will acknowledge receipt within 48 hours and provide a timeline for a fix.

## Scope

Security issues we care about:
- **Transform evaluator bypass** — executing arbitrary code via `FieldMapping.transform`
- **Credential leakage** — Vault keys or auth tokens exposed in logs, errors, or sync results
- **Injection attacks** — via API responses, field names, or mapping expressions
- **Dependency vulnerabilities** — in pydantic, httpx, pyyaml, or optional deps

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.2.x   | Yes       |
| < 0.2   | No        |
