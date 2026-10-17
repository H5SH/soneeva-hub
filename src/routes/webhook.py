import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from src.dependencies import AppDep, VoiceDep, WebhooksDep
from src.models.mode import staged

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/stripe/{app_id}")
async def stripe_webhook(
    request: Request,
    app_id: str,
    app: AppDep,
    by_mode: WebhooksDep,
    voice: VoiceDep,
    tasks: BackgroundTasks,
    stripe_signature: str | None = Header(default=None),
) -> dict[str, str]:
    """Public: authenticated by the gateway signature. `{appId}_staging` is the app's Stripe test mode.
    A paid voice checkout deploys the agent."""
    webhooks = by_mode[staged(app_id)[1]]
    payload = await request.body()
    try:
        event = await webhooks.parse_for_app(app.id, payload, stripe_signature)
    except ValueError as error:
        logger.warning(f"Rejected webhook for {app.id}: {error}")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid webhook signature") from error
    result = await webhooks.handle(app, event)
    business_id = voice.checkout_business(app.id, event) if result not in ("duplicate", "unmapped") else None
    if business_id:
        tasks.add_task(voice.deploy_quietly, business_id)
    return {"result": result}
