from typing import Annotated

from fastapi import Depends, HTTPException, Path, Request, status

from app.models.auth import Audience, AuthContext, TokenClaims
from app.services.tokens.verify import AuthError, TokenVerifier

HEADER_NAME = "x-api-key"
QUERY_NAME = "token"


def get_verifier(request: Request) -> TokenVerifier:
    return request.app.state.verifier


async def _authenticate(request: Request, app_id: str, audience: Audience) -> AuthContext:
    """Shared body of both dependencies: find the token, verify it, bind it to the path's app."""
    token = (
        request.headers.get(HEADER_NAME)
        if audience is Audience.API
        else request.query_params.get(QUERY_NAME)
    )
    if not token:
        where = HEADER_NAME if audience is Audience.API else f"?{QUERY_NAME}="
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Missing {where}")

    try:
        claims: TokenClaims = await get_verifier(request).verify(token, audience)
    except AuthError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error

    if claims.iss != app_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token is not valid for this app")
    return AuthContext(app_id=app_id, tenant=claims.tenant)


async def require_api_token(
    request: Request,
    app_id: Annotated[str, Path(description="Which app the caller is acting for")],
) -> AuthContext:
    """JSON API auth. Header only — a URL token must not reach these routes."""
    return await _authenticate(request, app_id, Audience.API)


async def require_hosted_token(
    request: Request,
    app_id: Annotated[str, Path(description="Which app the caller is acting for")],
) -> AuthContext:
    """Browser redirect auth. `?token=` only, and a different audience from the API.

    Browsers cannot set headers on a redirect, so the hosted pages have to accept a URL token.
    Separating the audience means a leaked URL token (referer, history, access logs) still cannot
    call the JSON API, and an API token pasted into a URL is refused.
    """
    return await _authenticate(request, app_id, Audience.HOSTED)


ApiAuthDep = Annotated[AuthContext, Depends(require_api_token)]
HostedAuthDep = Annotated[AuthContext, Depends(require_hosted_token)]
