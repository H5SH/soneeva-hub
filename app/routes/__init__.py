from app.routes.admin import ADMIN_ROUTERS
from app.routes.billing import router as billing_router
from app.routes.checkout import router as checkout_router
from app.routes.health import router as health_router
from app.routes.subscription import router as subscription_router
from app.routes.webhook import router as webhook_router

ROUTERS = [
    health_router,
    *ADMIN_ROUTERS,
    subscription_router,
    checkout_router,
    billing_router,
    webhook_router,
]

__all__ = ["ROUTERS"]
