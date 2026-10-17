"""`/`: a Soneeva page. Staff (admin dashboard or /recover, with Tools.Write) get a way to key recovery."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.routes.recover import HEADERS, TEMPLATES, session

router = APIRouter(include_in_schema=False)
SITE = "https://soneeva.com"


@router.get("/")
async def home(request: Request) -> HTMLResponse:
    html = TEMPLATES.get_template("home.html").render(staff=bool(await session(request)), site=SITE)
    return HTMLResponse(html, headers=HEADERS)
