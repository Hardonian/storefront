# Deployment

## Production Deployment

This service is deployed on The Platform infrastructure.

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| DATABASE_URL | Yes | SQLite database path |
| STRIPE_SECRET_KEY | Yes | Stripe API secret key |
| STRIPE_WEBHOOK_SECRET | Yes | Stripe webhook signing secret |
| API_KEY | Yes | API key for authenticated endpoints |
| APP_ENV | No | Environment (production/staging) |

### Health Check

```
GET /health
```

Returns 200 if the service is healthy.

### Webhook Verification

All Stripe webhooks are verified using the Stripe SDK signature verification.
Unsigned events are rejected with 401.

### Rollback

See `/home/scott/.hermes/scripts/rollback-guide.md` for rollback procedures.
