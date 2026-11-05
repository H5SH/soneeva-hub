"""`/`: a Soneeva page. Staff (admin dashboard or /recover, with Tools.Write) get a way to key recovery."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse

from src.routes.pages import HEADERS, TEMPLATES
from src.services.recover_session import session

router = APIRouter(include_in_schema=False)
SITE = "https://soneeva.com"
FAVICON = Path(__file__).parents[1] / "templates" / "favicon.ico"


@router.get("/")
async def home(request: Request) -> HTMLResponse:
    html = TEMPLATES.get_template("home.html").render(staff=bool(await session(request)), site=SITE)
    return HTMLResponse(html, headers=HEADERS)


@router.get("/favicon.ico")
async def favicon() -> FileResponse:
    cache = {"Cache-Control": "public, max-age=86400"}
    return FileResponse(FAVICON, media_type="image/x-icon", headers=cache)
