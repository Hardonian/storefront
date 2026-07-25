# Security Policy

## Reporting Vulnerabilities

If you discover a security vulnerability, please report it responsibly.
Do not open a public issue for security findings.

## Scope

This policy covers all repositories in The Platform ecosystem:
- hardonia-checkout-api: Payment processing, Stripe integration
- ai-lab-audit-api: Public audit API
- storefront: Customer-facing storefront
- hardonia-compute-api: GPU compute service

## Security Measures

- All webhook endpoints verify Stripe signatures
- All API endpoints require API keys or authentication
- Database queries use parameterized statements (no SQL injection)
- CORS is configured to allow only known origins
- Rate limiting is active on all public endpoints
- TLS is enforced on all public routes

## Verified Endpoints

| Endpoint | Auth | Verification |
|----------|------|-------------|
| /webhook/stripe | Stripe signature | Required |
| /api/* | API key | Required |
| /health | None | Open |

## Last Reviewed

2026-07-24
