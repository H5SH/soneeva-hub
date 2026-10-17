from typing import Annotated

from fastapi import Depends, HTTPException, Path, Request, status

from src.models.auth import Audience, AuthContext, TokenClaims
from src.models.mode import staged
from src.services.tokens.verify import AuthError, TokenVerifier

HEADER_NAME = "x-api-key"


def get_verifier(request: Request) -> TokenVerifier:
    return request.app.state.verifier


async def require_api_token(
    request: Request,
    app_id: Annotated[str, Path(description="Which app the caller is acting for")],
) -> AuthContext:
    """The payment JSON API (`/v1/apps/{app_id}`): `x-api-key` header, bound to the path's app;
    `iss` and path `{appId}_staging` is that app in Stripe test mode."""
    token = request.headers.get(HEADER_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Missing {HEADER_NAME}")
    try:
        claims: TokenClaims = await get_verifier(request).verify(token, Audience.API)
    except AuthError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error
    if claims.iss != app_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token is not valid for this app")
    return AuthContext(app_id=staged(app_id)[0], tenant=claims.tenant)


ApiAuthDep = Annotated[AuthContext, Depends(require_api_token)]
