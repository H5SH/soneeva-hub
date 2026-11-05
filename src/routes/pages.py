"""What the hub's few HTML pages share."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATES = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / "templates"), autoescape=True)
HEADERS = {
    "Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; img-src data:; script-src 'unsafe-inline'; form-action 'self'; "
    "frame-ancestors 'none'",
}
