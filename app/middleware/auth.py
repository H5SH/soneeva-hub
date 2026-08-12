import jwt
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.models.auth import TokenClaims

ALGORITHM = "HS256"
LEEWAY_SECONDS = 30
HEADER_NAME = "x-api-key"
QUERY_NAME = "token"
PUBLIC_PATHS = {"/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
PUBLIC_PREFIXES = ("/webhooks/")
PUBLIC_BILLING_ACTIONS = {"return", "cancel"}


class AuthMiddleware(BaseHTTPMiddleware):
    """Verifies the caller's JWT before any route runs.

    The JSON API sends it as the `x-api-key` header. Browser redirects cannot set headers, so the
    hosted checkout pages accept the same token as `?token=` — issue those short-lived.
    """

    def __init__(self, app: FastAPI, secret: str) -> None:
        super().__init__(app)
        self._secret = secret

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        if self._is_public(request.url.path):
            return await call_next(request)

        token = request.headers.get(HEADER_NAME) or request.query_params.get(QUERY_NAME)

        if not token:
            return self._denied(f"Missing {HEADER_NAME}")
        try:
            request.state.claims = self._decode(token)
        except jwt.InvalidTokenError as error:
            return self._denied(f"Invalid {HEADER_NAME}: {error}")
        return await call_next(request)

    def _decode(self, token: str) -> TokenClaims:
        payload = jwt.decode(
            token,
            self._secret,
            algorithms=[ALGORITHM],
            leeway=LEEWAY_SECONDS
        )

        store_hash = payload.get('sub', '')
        access_token = payload.get('accessToken', "")
        return TokenClaims(store_hash=store_hash, access_token=access_token, **payload)

    @staticmethod
    def _is_public(path: str) -> bool:
        """Gateway webhooks carry their own signature, and the redirect landing pages carry none."""
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return True
        parts = path.strip("/").split("/")
        return len(parts) == 3 and parts[0] == "billing" and parts[2] in PUBLIC_BILLING_ACTIONS

    @staticmethod
    def _denied(detail: str) -> JSONResponse:
        return JSONResponse({"detail": detail}, status_code=status.HTTP_401_UNAUTHORIZED)
