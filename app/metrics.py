"""Shared Prometheus metrics for Storefront."""
from prometheus_client import Counter, Histogram, Gauge, Info
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "method", "endpoint", "status"],
)
REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["service", "method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
REQUEST_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "HTTP requests currently being processed",
    ["service", "method", "endpoint"],
)
LEAD_CAPTURES = Counter(
    "storefront_leads_total",
    "Total lead captures",
    ["service", "source"],
)
DB_ERRORS = Counter(
    "database_errors_total",
    "Total database errors",
    ["service", "operation"],
)
WEBHOOK_DELIVERIES = Counter(
    "webhook_deliveries_total",
    "Total webhook delivery attempts",
    ["service", "provider", "status"],
)
SERVICE_INFO = Info("service_info", "Service metadata")


class PrometheusMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, service_name: str):
        super().__init__(app)
        self.service_name = service_name

    async def dispatch(self, request: Request, call_next):
        method = request.method
        endpoint = request.url.path
        REQUEST_IN_PROGRESS.labels(service=self.service_name, method=method, endpoint=endpoint).inc()
        with REQUEST_DURATION.labels(service=self.service_name, method=method, endpoint=endpoint).time():
            try:
                response = await call_next(request)
            except Exception:
                REQUEST_COUNT.labels(service=self.service_name, method=method, endpoint=endpoint, status="500").inc()
                raise
        REQUEST_COUNT.labels(service=self.service_name, method=method, endpoint=endpoint, status=str(response.status_code)).inc()
        REQUEST_IN_PROGRESS.labels(service=self.service_name, method=method, endpoint=endpoint).dec()
        return response
