"""Dashboard routes, one module per screen group."""

from app.routes.admin.apps import router as apps_router
from app.routes.admin.dashboard import router as dashboard_router
from app.routes.admin.session import router as session_router
from app.routes.admin.users import router as users_router

ADMIN_ROUTERS = [session_router, dashboard_router, apps_router, users_router]

__all__ = ["ADMIN_ROUTERS"]
