import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.dependencies import AppDep, WebhookDep

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/stripe/{app_id}")
async def stripe_webhook(
    request: Request,
    app: AppDep,
    webhooks: WebhookDep,
    stripe_signature: str | None = Header(default=None),
) -> dict[str, str]:
    """Public: authenticated by the gateway signature, not by x-api-key. One endpoint per app."""
    payload = await request.body()
    try:
        event = await webhooks.parse_for_app(app.id, payload, stripe_signature)
    except ValueError as error:
        logger.warning(f"Rejected webhook for {app.id}: {error}")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid webhook signature") from error
    return {"result": await webhooks.handle(app, event)}
