from src.routes.agent import router as agent_router
from src.routes.agent import schema as agent_schema
from src.routes.checkout import router as checkout_router
from src.routes.health import router as health_router
from src.routes.home import router as home_router
from src.routes.payment import PAYMENT_ROUTERS
from src.routes.platform import PLATFORM_ROUTERS
from src.routes.recover import router as recover_router
from src.routes.subscription import router as subscription_router
from src.routes.webhook import router as webhook_router

ROUTERS = [
    health_router,
    subscription_router,
    checkout_router,
    webhook_router,
    *PLATFORM_ROUTERS,
    *PAYMENT_ROUTERS,
    agent_router,
    agent_schema,
    recover_router,
    home_router,
]

__all__ = ["ROUTERS"]
