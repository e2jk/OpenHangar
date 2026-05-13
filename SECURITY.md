# Security Policy

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report vulnerabilities privately using GitHub's built-in security advisory feature:

**[Report a vulnerability](https://github.com/e2jk/OpenHangar/security/advisories/new)**

Alternatively, you can reach the maintainer by email at botany-nest-bluish@duck.com

## What to Include

- A description of the vulnerability and its potential impact
- Steps to reproduce or a proof-of-concept
- Affected versions (if known)
- Any suggested mitigations

## Disclosure Policy

- We will acknowledge receipt within **5 business days**.
- We aim to release a fix within **90 days** of the initial report.
- We will coordinate public disclosure with the reporter and publish a GitHub Security Advisory once a fix is available.
- If a fix cannot be delivered within 90 days, we will notify the reporter and agree on an extended timeline.

## Scope

The following are considered in scope:

- Authentication and authorisation bypass
- SQL injection, XSS, CSRF, and other OWASP Top 10 vulnerabilities
- Sensitive data exposure (flight logs, personal data, credentials)
- Privilege escalation between roles or tenants

The following are **out of scope**:

- Vulnerabilities in the development or demo instance data (it contains only synthetic data)
- Issues requiring physical access to the server
- Denial-of-service attacks against self-hosted instances
