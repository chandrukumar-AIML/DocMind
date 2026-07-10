# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| `main` branch | Yes |
| Older releases | No |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Email **terazionservices@gmail.com** with:

1. Description of the vulnerability and affected component
2. Steps to reproduce
3. Potential impact assessment
4. Any suggested fix (optional)

You will receive an acknowledgement within **48 hours** and a resolution timeline within **7 days**.

## Scope

In scope:
- Authentication bypass / JWT forgery
- SQL injection / data exfiltration
- Privilege escalation between workspaces (multi-tenant isolation)
- RCE via document upload or processing pipeline
- XSS in the chat interface

Out of scope:
- Denial of service attacks
- Issues requiring physical access
- Social engineering of team members

## Security controls summary

| Control | Implementation |
|---------|---------------|
| Authentication | JWT HS256 + JTI Redis revocation blacklist |
| Authorization | RBAC (viewer / editor / workspace_admin / admin) per workspace |
| Transport | TLS 1.2+ enforced via HSTS header (`max-age=63072000`) |
| Input validation | Pydantic v2 models on all API boundaries |
| SAST | Bandit (medium severity) in CI on every push |
| Dependency scanning | GitHub Dependabot (enabled) |
| Secrets | Kubernetes Secrets / environment variables — never in code |
| Content headers | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy` |
