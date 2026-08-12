from pathlib import Path
from urllib.parse import urlencode

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SESSION_PLACEHOLDER = "{CHECKOUT_SESSION_ID}"


def get_path(*parts: str | Path) -> Path:
    """Resolve a file or folder relative to the project root."""
    return PROJECT_ROOT.joinpath(*parts)


def with_query(url: str, **params: str | None) -> str:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    if not query:
        return url
    return f"{url}{'&' if '?' in url else '?'}{query}"


def hosted_urls(base_url: str, app_id: str, redirect: str) -> tuple[str, str]:
    """Success and cancel URLs that land back on this service, so state is synced before the app sees it."""
    base = f"{base_url.rstrip('/')}/billing/{app_id}"
    success = with_query(f"{base}/return", redirect=redirect)
    return f"{success}{'&' if '?' in success else '?'}session_id={SESSION_PLACEHOLDER}", with_query(
        f"{base}/cancel", redirect=redirect
    )
