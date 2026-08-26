"""CORS and payload size limit middleware."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

ALLOWED_ORIGINS = {
    "https://aiautomatedsystems.ca",
    "https://hardonia.store",
}
ALLOWED_METHODS = "GET, POST"
ALLOWED_HEADERS = "accept, accept-language, content-language, content-type"
MAX_BODY_BYTES = 64 * 1024  # 64 KB


class PayloadLimitAndCORSMiddleware(BaseHTTPMiddleware):
    """Enforce body limits and strict CORS origin allowlists."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        origin = request.headers.get("origin")

        # Check payload size for /api/ask and other mutation endpoints
        if request.url.path.startswith("/api/"):
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > MAX_BODY_BYTES:
                        return JSONResponse(
                            status_code=413,
                            content={"error": "request_too_large"},
                        )
                except ValueError:
                    pass

        # Handle Preflight OPTIONS
        if request.method == "OPTIONS":
            if origin in ALLOWED_ORIGINS:
                response = Response(status_code=200)
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = ALLOWED_METHODS
                response.headers["Access-Control-Allow-Headers"] = ALLOWED_HEADERS
                return response
            return Response(status_code=403)

        response = await call_next(request)

        # Attach CORS headers if origin is allowlisted
        if origin in ALLOWED_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = ALLOWED_METHODS
            response.headers["Access-Control-Allow-Headers"] = ALLOWED_HEADERS

        return response
