"""/v1/payment: the internal app (karbon-admin), acting for a platform person."""

from src.routes.payment.agent_tokens import router as agent_tokens_router
from src.routes.payment.apps import router as apps_router
from src.routes.payment.businesses import router as businesses_router
from src.routes.payment.plans import router as plans_router

PAYMENT_ROUTERS = [apps_router, plans_router, businesses_router, agent_tokens_router]

__all__ = ["PAYMENT_ROUTERS"]
