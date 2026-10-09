"""One Jinja environment for the whole service.

`Scope` is a global rather than a context key: the sidebar in the base layout checks it on
every page, so passing it per route means one forgotten template renders a 500.
"""

from fastapi.templating import Jinja2Templates

from app.models.admin import Scope
from app.utils import get_path

templates = Jinja2Templates(directory=get_path("app", "templates"))
templates.env.globals["Scope"] = Scope
