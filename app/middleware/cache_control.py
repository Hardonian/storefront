"""Cache control middleware for assets, APIs, and crawlable pages."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class CacheControlMiddleware(BaseHTTPMiddleware):
    """Apply strict, high-performance cache rules based on endpoint patterns."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        path = request.url.path

        # Never cache stateful, transactional, or operator APIs
        if (
            path.startswith("/api/")
            or path.startswith("/webhook/")
            or path in ("/order/success", "/order/cancel")
            or path.startswith("/download/")
        ):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"

        # Long-cache immutable static assets
        elif path.startswith("/product-assets/"):
            response.headers["Cache-Control"] = "public, max-age=3600"
        elif path.startswith("/landing-assets/"):
            response.headers["Cache-Control"] = "public, max-age=86400, immutable"

        # Crawlable SEO surfaces: cached briefly
        elif path in ("/sitemap.xml", "/blog/rss.xml", "/robots.txt", "/llms.txt"):
            response.headers["Cache-Control"] = "public, max-age=3600"
            if path != "/robots.txt":
                response.headers["X-Robots-Tag"] = "index, follow"

        # HTML catalog pages: short cache with required revalidation
        elif (
            path.endswith(".html")
            or path in ("/", "/blog", "/pricing", "/status", "/proof-score", "/proof-benchmark")
            or path.startswith("/p/")
            or path.startswith("/shop/")
            or path.startswith("/compare/")
        ):
            response.headers["Cache-Control"] = "public, max-age=300, must-revalidate"

        return response
