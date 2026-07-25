# Monitoring

## Metrics

- Request count and latency (Prometheus format at /metrics)
- Error rate by endpoint
- Webhook delivery success rate
- Payment completion rate
- Service uptime

## Alerts

- Error rate > 5% over 5 minutes
- Webhook delivery failure rate > 1%
- Payment failure rate > 10%
- Service downtime > 1 minute
- Disk usage > 80%

## Dashboards

- System Status: `system-status.py` (run via Hermes cron hourly)
- Layer Status: `/home/scott/.hermes/state/layers/`
- Revenue Dashboard: `/metrics/funnel`

## Log Locations

- Application logs: journalctl --user -u <service-name>
- Layer state: /home/scott/.hermes/state/layers/
- Cron logs: Hermes cron job output
