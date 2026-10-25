from src.routes.platform.accounts import router as accounts_router
from src.routes.platform.advertisements import router as advertisements_router
from src.routes.platform.billing import router as billing_router
from src.routes.platform.businesses import router as businesses_router
from src.routes.platform.callers import router as callers_router
from src.routes.platform.data import router as data_router
from src.routes.platform.demo import router as demo_router
from src.routes.platform.handoff import router as handoff_router
from src.routes.platform.ivr import router as ivr_router
from src.routes.platform.knowledge import router as knowledge_router
from src.routes.platform.media import router as media_router
from src.routes.platform.people import router as people_router
from src.routes.platform.public import router as public_router
from src.routes.platform.roles import router as roles_router
from src.routes.platform.usage import router as usage_router

PLATFORM_ROUTERS = [
    public_router, businesses_router, billing_router, usage_router, accounts_router, people_router,
    roles_router, data_router, handoff_router, media_router, ivr_router, callers_router,
    advertisements_router, demo_router, knowledge_router,
]

__all__ = ["PLATFORM_ROUTERS"]
